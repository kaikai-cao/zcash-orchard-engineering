# `cv_net`：两套 range check 的分界，与两个乘子的分工

> 上游：`zcash/orchard` @ `616a669d`，`src/circuit.rs` L609–L665、`src/circuit/gadget.rs` L118–L147
> 依赖：`halo2_gadgets 0.6.0` —— `src/ecc/chip/mul_fixed.rs`、`src/ecc/chip/mul_fixed/short.rs`、`src/ecc/chip/mul_fixed/full_width.rs`、`src/utilities/decompose_running_sum.rs`、`src/utilities/lookup_range_check.rs`、`src/sinsemilla.rs` L408–L500
> 承接：`03_synthesize_base.md` §7 问题 1

---

## 0. 一句话

`cv_net = [m·s]V + [rcv]R` 里的两个标量走**两条不同的乘法通道**：`m`（64 位短标量）走 22 窗口的 running-sum 分解，`rcv`（255 位完整标量）走 85 窗口的直接分解；两者的 3-bit 约束都落在 **`advices[4]`**，而 `advices[9]` 上的 `LookupRangeCheckConfig` 与它们**无关**。

---

## 1. 先把 `/range check` 的三种机制分开

上一轮我留的问题是"`magnitude` 为什么落在 `advices[9]`，范围检查怎样被触发"。结论是：**它落在 `advices[9]` 与范围检查无关，只是借用了一个 equality-enabled 的右侧列。** 这里实际并存三个同名不同物的东西：

| 机制 | 类型 | 列 | 约束形式 | 谁在用 |
|---|---|---|---|---|
| 查表式 range check | `LookupRangeCheckConfig` | **`advices[9]`** | lookup 到 `table_idx`（3 列表） | Sinsemilla 字分解；**变基**标量乘（`mul::Config`，`ecc/chip.rs` L339）；CommitIvk/NoteCommit 的规范性检查 |
| running-sum 式 | `RunningSumConfig` | **`advices[4]`** | 纯门：`q · word(1−word)…(7−word)`，**次数 9** | **定基短标量乘**（`mul_fixed::short`） |
| 直接窗口式 | 无独立 config | **`advices[4]`** | `range_check(window, 8)` + `coords_check` | **定基全宽标量乘**（`mul_fixed::full_width`） |

三个必须讲准的点：

1. **`mul_fixed` 的 `running_sum_config` 不是 `LookupRangeCheckConfig`。**
   ```rust
   // ecc/chip/mul_fixed.rs L5, L34, L65
   use crate::utilities::decompose_running_sum::RunningSumConfig;
   running_sum_config: RunningSumConfig<pallas::Base, FIXED_BASE_WINDOW_SIZE>,
   let running_sum_config = RunningSumConfig::configure(meta, q_running_sum, window);
   ```
   `LookupRangeCheckConfig` 在 `utilities/lookup_range_check.rs`，`RunningSumConfig` 在 `utilities/decompose_running_sum.rs`。名字都带 "running sum"，但前者查表、后者用 8 项乘积门。

2. **次数 9 是这套机制的指纹。** `range_check(word, 8) = word·(1−word)···(7−word)`（`utilities.rs` L170–L174）是 8 次，乘上 selector 就是 **9 次** —— 正是 `02_configure.md` 实测的 `cs.degree() = 9`。也就是说，整份电路的最高次数来自这里，而不是 Poseidon 或 Sinsemilla。

3. **`window` 与 `running_sum_config.z` 是同一列。** `RunningSumConfig::configure(meta, q, window)` 把 `z` 直接绑到传入的 `window`（`advices[4]`），所以 "z 列" 和 "窗口列" 不是两列；同区域里 `sign` 也被 copy 到这一列（`short.rs` L164–L169），只是不同行。

### 1.1 `m` 的 64 位是怎么闭合的

`short.rs` 的文档注释自己写了结论，值得原样引用：

> Constraints `magnitude` to be at most 66 bits.
> The final window is separately constrained to be a single bit, which completes the 64-bit range constraint.

链条（`decompose_running_sum.rs` L170–L240 + `short.rs` L52–L70）：

| 步骤 | 约束 | 位置 |
|---|---|---|
| 22 个窗口各 3 bit | `q_range_check · k_i(1−k_i)…(7−k_i) = 0`，`i = 0..21` | `z` 列（`advices[4]`）逐行 |
| 末位 running sum 归零 | `z_22 = 0`（`strict = true` → `constrain_constant`） | 第 22 行之后 |
| 最高窗口再收窄成 1 bit | `bool_check(z_21) = z_21(1−z_21) = 0` | `Short fixed-base mul gate`，`z_21` 被 copy 到 `u`（`advices[5]`，`short.rs` L175） |

因此 `m = Σ_{i=0}^{20} k_i 8^i + k_21·8^21 ≤ (8^21−1) + 8^21 = 2^64−1`。**21 个窗口 × 3 bit + 末窗口 1 bit = 64 bit**，你这条推算与源码一致。

一个容易漏的细节：第 22 行上**同时**存在两重约束——`q_range_check` 也在这行启用（`for idx in 0..num_windows`），所以 `k_21` 先被限制成 3 bit，再被 `bool_check` 收窄到 `{0,1}`。不是"末窗口只做 bool"。

### 1.2 证据边界：为什么放 `advices[9]`

`copy_decompose`（`decompose_running_sum.rs` L144–L158）的第一步就是 `alpha.copy_advice(…, self.z, offset)`——把传入的 cell 复制到 `z` 列。复制约束要求源列 equality-enabled，而 `configure()` 对全部 10 个 advice 列都调了 `enable_equality`（`circuit.rs` L389–L391）。所以：

> 源码证明"放在 `[9]` 且能工作"，**不证明**"必须放在 `[9]`"。任何 advice 列都行；选 `[9]` 是因为它最右侧、最清闲，且已被 `LookupRangeCheckConfig` 启用过 equality。

（一个反向的注意点：`advices[9]` 上若有行被启用 lookup，那行的值就受查表约束。`assign_free_advice` 建的是无 selector 的独立单行区域，所以安全。）

---

## 2. `cv_net` 的两个乘子：22 窗口 vs 85 窗口

```rust
// circuit.rs L641–L661
let v_net = ScalarFixedShort::new(ecc_chip.clone(), …, v_net_magnitude_sign.clone())?;
let rcv   = ScalarFixed::new(ecc_chip.clone(), …, self.rcv.as_ref().map(|rcv| rcv.inner()))?;
let cv_net = gadget::value_commit_orchard(…, ecc_chip.clone(), v_net, rcv)?;
layouter.constrain_instance(cv_net.inner().x().cell(), config.primary, CV_NET_X)?;
layouter.constrain_instance(cv_net.inner().y().cell(), config.primary, CV_NET_Y)?;
```

`value_commit_orchard`（`gadget.rs` L118–L147）把两个乘子接到两个不同的定点族上：

| 乘子 | 基 | 类型 | 分解 | 窗口数 |
|---|---|---|---|---|
| `v`（= `m·s`） | `ValueCommitV` | `FixedPointShort`（`OrchardShortScalarBases`） | `RunningSumConfig` + `bool_check` | **22** |
| `rcv` | `ValueCommitR` | `FixedPoint`（`OrchardFixedBasesFull`） | `decompose_word` 直接给 85 个 3-bit 窗口 | **85** |

常量（`ecc/chip/constants.rs` L12–L25）：

```rust
pub const FIXED_BASE_WINDOW_SIZE: usize = 3;
pub(crate) const L_SCALAR_SHORT: usize = 64;                 // → NUM_WINDOWS_SHORT = ceil(64/3) = 22
pub const NUM_WINDOWS: usize = 255_usize.div_ceil(3);        // → 85
```

**两条通道的差别不只是窗口数。**

- 短标量：running sum 串行、`strict` 归零、末位 bool → 精确 `[0, 2^64)`，**唯一表示**（`2^64 ≪ p`）。
- 全宽标量：`full_width.rs` L75–L100 直接 `decompose_word` 出 85 个窗口，每窗 3 bit，**没有 running sum、没有 strict**；源码注释直言 "The scalar is allowed to be non-canonical"。
  非规范表示（比如 `rcv + p < 2^255`）也能通过，但因为 `R` 的阶是 `p`，`[rcv+p]R = [rcv]R`，乘法结果不变 —— 所以对 `cv_net` 无害。这不是"漏了检查"，而是刻意留出的自由度。

`rcv` 的惰性也在这里落地：`ScalarFixed::new` 对这套 chip 是 `Ok(EccScalarFixed { value, windows: None })`（`ecc/chip.rs` L563–L573），**不分配任何 cell**；真正的 85 行窗口是在 `[rcv]ValueCommitR` 的 `mul` 里才写出来的。

---

## 3. 为什么`cv_net` 既要 `m/s` 又要 `rcv`：同一组 cell，两处落点

这是本次最值得记住的结构事实。`m` 和 `s` 这两个 cell 各自出现在**三个**位置，靠 permutation 串成同一个值：

| cell | ① 初始见证 | ② 进入标量乘 | ③ 进入 Orchard gate |
|---|---|---|---|
| `m` | `advices[9]`（L628–L632） | 复制到 `advices[4]`（`copy_decompose`） | 复制到 `advices[2]`（L848–L853） |
| `s` | `advices[9]`（L633–L637） | 复制到 `advices[4]`（`short.rs` "sign"） | 复制到 `advices[3]`（L854–L859） |

于是：

- **② 与 ③ 锁的是同一个 `m`**：`[m·s]V` 里用的标量，就是 gate 里 `v_old − v_new = m·s` 的那个 `m`。金额关系与承诺内容不需要额外的等式约束去对齐——它们本来就是同一批 cell。
- **`rcv` 只出现在 ②**，从不进入 ③。这正是它的作用：它是 `cv_net` 里唯一的 hiding 自由度。若 `rcv` 被约束成常数，`cv_net` 就退化成 `[m·s]V`，直接泄漏 `v_net`。
- **公开绑定只到坐标**：`constrain_instance` 只钉 x/y 两行（L660–L661）。点是否恒等、y 的符号约定都不由这里管；`cv_net` 是 `Point`（允许恒等）而非 `NonIdentityPoint`。

换一种说法：`q_orchard` 负责"**金额差**等于 witness 的 `m·s`"，`cv_net` 负责"**金额差的承诺**等于公开的 x/y"，而 `rcv` 让第二件事不暴露第一件事。

---

## 4. 成本含义（结构推断，非时间测量）

按上面的通道划分，一个 Action 里的**定基标量乘**可以清点如下：

| 标量 | 基 | 族 | 窗口行 |
|---|---|---|---|
| `m·s`（v_net） | `ValueCommitV` | 短 | **22** |
| `α` | `SpendAuthG` | 全宽 | 85 |
| `rcv` | `ValueCommitR` | 全宽 | 85 |
| `rcm_old` | `NoteCommitR` | 全宽 | 85 |
| `rcm_new` | `NoteCommitR` | 全宽 | 85 |
| `rivk` | `CommitIvkR` | 全宽 | 85 |

其中 `rcm_*`/`rivk` 的乘法不在 `circuit.rs` 里显式出现，而是藏在 Sinsemilla `CommitDomain::commit` 内部：`R: ecc::FixedPoint`，`self.R.mul(layouter.namespace(|| "[r] R"), r)`（`halo2_gadgets/src/sinsemilla.rs` L425、L491、L512）。

**5 个全宽 × 85 = 425 行 3-bit 窗口约束（每行 degree 9）**，而金额只占 22 行。

这条清点只说明**结构占比**，不能直接换算成时间：窗口行数不等价于 gate 数量（每行还有 `coords_check`、`add`/`add_incomplete` 的配套行），更不能替代阶段 E 的实测。但它给了一个可检验的排序假设——**若定基乘法是热点，热点在 `rcv`/`rcm`/`rivk`/`α` 这一族，不在金额**。这条假设要等阶段 E 的硬件计数器或 `create_proof` 内部分段计时来判。

---

## 5. 本篇结论

| # | 结论 | 依据 |
|---|---|---|
| F9 | `advices[9]` 上的 lookup 式 range check 与 `v_net` 无关；`m` 只是借用该列作见证 | L628–L637、`circuit.rs` L417 |
| F10 | 定基短标量乘用 `RunningSumConfig`（纯门，8 项乘积，次数 9），**不是** `LookupRangeCheckConfig` | `mul_fixed.rs` L5/L34/L65、`decompose_running_sum.rs` L85–L113 |
| F11 | `m < 2^64` 由"21×3 bit + 末窗口 `bool_check(z_21)` + `z_22=0`"共同闭合；末窗口同时受 3-bit 与 bool 两重约束 | `short.rs` L52–L70、L83–L102 |
| F12 | 全宽标量乘（`rcv`）走 85 个直接窗口，无 running sum、无 strict，允许非规范表示；因 `R` 阶为 `p` 而无害 | `constants.rs` L18–L25、`full_width.rs` L34–L100 |
| F13 | `m`/`s` 各自出现在 `[9]→[4]→[2]/[3]` 三处，说明"金额关系"与"承诺内容"共用同一批 cell | L628–L637、L846–L859、`short.rs` L164–L169 |
| F14 | `rcv` 只进 `cv_net`、不进 gate，是唯一的 hiding 自由度；公开绑定只到 x/y 两行 | L641–L661 |

---

## 6. 下一步：`nf_old`

`derive_nullifier`（`gadget.rs` L154–L206）要回答四个问题：

1. **为什么 `psi` 用普通加法而不是再进一次 Poseidon？** `AddChip` 落在 `advices[6..9]`（`AddChip::configure(meta, advices[7], advices[8], advices[6])`），一个加法门就把 `Poseidon(nk,rho)+psi` 拼出来，代价是一行。
2. **`NullifierK` 用 `FixedPointBaseField`（基域标量乘）而不是 `ScalarFixed`。** 为什么允许？（提示：`scalar` 是 `pallas::Base` 元素，不是 `pallas::Scalar`；基域乘的窗口数与 255 位标量乘不同，需要单独清点。）
3. **`nf_old` 只 `extract_p()` 取 x 再绑 `Instance[NF_OLD]`**：y 为什么不需要？（与 `cmx` 同样的理由：x 已经蕴含 y 的符号信息不足，但 nullifier 的语义只要求绑定一个域元素。）
4. **`rho_new = nf_old`（L805）** 是 copy 而非计算：新 note 的 `ρ` 直接复用旧 nullifier 的 x 坐标。这条链在约束层面怎么表达？跨 note 的"链式"关系是否完全靠这一步？
