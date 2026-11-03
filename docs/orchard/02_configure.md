# `Config::configure()`：Orchard Action 电路的骨架

> 上游：`zcash/orchard` @ `616a669d`（`0.16.0`），`src/circuit.rs` L310–L503
> 依赖：`halo2_proofs 0.4.0`、`halo2_gadgets 0.6.0`
> 原始证据：`results/halo2/configure/cs_pinned.txt`（`ConstraintSystem::pinned()` 的完整 dump，548 KB）
> 复现：`cargo run --release --bin cs_dump -- <out_dir>`（见 §8）

---

## 0. 一句话

`configure()` 只做三件事：**声明 10 个 advice 列 + 14 个 fixed 列 + 1 个 instance 列**，**注册一个承载 4 条约束的 `q_orchard` 门**，然后**把同一批列借给 8 个 chip**（ECC / Poseidon / 两套 Sinsemilla / 两套 Merkle / CommitIvk / NoteCommit / Add / RangeCheck）。

它**不看 `circuit_version`**——这是理解 NU6.3 成本的前提（§5）。

---

## 1. 实测的电路形状

不是从源码数出来的，是把 `Circuit::configure` 跑完后直接读 halo2 的 `ConstraintSystem`：

| 项 | 值 | 怎么看 |
|---|---:|---|
| advice 列 | **10** | `num_advice_columns` |
| fixed 列 | **14** | `num_fixed_columns` |
| instance 列 | **1** | `num_instance_columns` |
| selector | **56** | `num_selectors`（逻辑选择器，非物理列） |
| 约束多项式 | **193** | `gates` 展开后的顶层元素数 |
| 最大约束次数 | **9** | `cs.degree()` |
| blinding factors | **5** | `cs.blinding_factors()` |
| `minimum_rows` | **8** | 决定最小可用行数 |
| 置换论证覆盖列 | **15**（10 advice + 4 fixed + 1 instance） | `permutation.columns` |
| lookup 论证 | **3** | `lookups` |
| 全局常量列 | **1**（`lagrange_coeffs[0]`） | `constants` |

三个容易误读的地方：

1. **56 个 selector 不是 56 个物理列。** `Selector(usize, bool)` 只是一个索引；keygen 时 `compress_selectors`（`plonk/circuit.rs:1237`，调用点 `plonk/keygen.rs:222`）会按每个 selector 的实际激活模式把 56 个压进若干**新建的 fixed 列**。所以 `configure` 结束时的 14 个 fixed 列**不是最终数**——keygen 后还会增加。这也是为什么 §1 的数字只能用于描述"结构"，不能直接换算成证明大小。
2. **193 条约束多项式**是各门内部 `Constraints::with_selector(q, [...])` 展开的结果，每条都长成 `q · 多项式`（所以 dump 里 193 个顶层元素全是 `Product`）。它不是"193 个门"——门的数量远少于这个。
3. **置换论证 15 列 ≠ 15 个 product 多项式。** 每列要乘 `z(ωX)`，chunk 长度是 `cs_degree - 2 = 7`（`plonk/permutation/prover.rs:76`），所以 `⌈15/7⌉ = 3` 个 product 多项式，各自要一次承诺。

---

## 2. 10 个 advice 列各自干什么

先给结论表，后面是依据。

| 列 | 门里的角色 | 主要占用者（声明层） |
|---|---|---|
| `advices[0]` | `v_old` | ECC `witness_point` x、`add_incomplete`、`mul` 的 double-and-add `x_a`；Sinsemilla#1 `x_a`；CommitIvk 的 `ak`/`nk` |
| `advices[1]` | `v_new` | ECC `witness_point` y；Sinsemilla#1 `x_p`；CommitIvk 的 `a`/`c` |
| `advices[2]` | `magnitude` | Sinsemilla#1 `bits`；CommitIvk 的 `b`/`d`；ECC `mul` lo 半区 |
| `advices[3]` | `sign` | Sinsemilla#1 `lambda_1`；ECC `mul` hi 半区 |
| `advices[4]` | `root` | Sinsemilla#1 `lambda_2`；ECC `mul_fixed` |
| `advices[5]` | `anchor` | Sinsemilla#2 `x_a`；**Poseidon `partial_sbox`**；ECC `add`/`mul_fixed` |
| `advices[6]` | `enable_spend` | **AddChip 的输出 `c`**；Poseidon `state[0]`；Sinsemilla#1 `witness_pieces`；Sinsemilla#2 `x_p`；NoteCommit `col_l` |
| `advices[7]` | `enable_output` | **AddChip 的输入 `a`**；Poseidon `state[1]`；Sinsemilla#2 `witness_pieces`（与其 `bits` 同列）；NoteCommit `col_m` |
| `advices[8]` | — | **AddChip 的输入 `b`**；Poseidon `state[2]`；Sinsemilla#2 `lambda_1`；NoteCommit `col_r` |
| `advices[9]` | — | ECC `mul` hi 半区起点；**RangeCheck 的 `running_sum`**；Sinsemilla#2 `lambda_2`；NoteCommit `col_z` |

### 2.1 依据一：`q_orchard` 只钉住前 8 列

门里查询了 `advices[0..7]`（`circuit.rs:337-346`），`advices[8]`、`advices[9]` **在门里没有任何约束**。这两列是"留给 gadget 的余量"，也是为什么 `configure` 的注释说"最右边几列还有大量空位，拿一列做 range check"（`circuit.rs:415-417`）。

### 2.2 依据二：每个 chip 的 configure 签名把列号写死了

```rust
// circuit.rs:374
AddChip::configure(meta, advices[7], advices[8], advices[6]);   // a, b, c
```

这里 `c = advices[6]` **排在 `a`、`b` 左边**，不是笔误。`mul.rs:104` 的注释解释了原因：mul 的 `z`、`lambda1` 与 `add` 的输出同处一行，不能和 `add` 的 `x_qr`/`y_qr` 撞列。把输出放到 6 号列，输入占 7、8，正好避开。

```rust
// circuit.rs:421  ECC 用全部 10 列，横跨整个电路
EccChip::<OrchardFixedBases>::configure(meta, advices, lagrange_coeffs, range_check);
// mul 内部（halo2_gadgets ecc/chip/mul.rs:71-79）
//   hi 半区: advices[9], [3], [0], [1], [4], [5]
//   lo 半区: advices[6], [7], [0], [1], [8], [2]   ← 0/1 两列 hi/lo 共用

// circuit.rs:425  Poseidon: state = advices[6..9]，partial_sbox = advices[5]
PoseidonChip::configure::<poseidon::P128Pow5T3>(meta, advices[6..9]…, advices[5], rc_a, rc_b);

// circuit.rs:440  两套 Sinsemilla 左右并排：一套占 0..5，一套占 5..10
SinsemillaChip::configure(meta, advices[..5]…, advices[6], …);
SinsemillaChip::configure(meta, advices[5..]…, advices[7], …);

// circuit.rs:475  CommitIvk 用全部 10 列，2 行一组（cur/next）
CommitIvkChip::configure(meta, advices);
// circuit.rs:479/484  NoteCommit 只用右半边 6..10
NoteCommitChip::configure(meta, advices, sinsemilla_config_1/2);
//   col_l = advices[6], col_m = [7], col_r = [8], col_z = [9]   (note_commit.rs:1484-1487)
```

`Sinsemilla #1` 占 0–4，`#2` 占 5–9，这是刻意的：两套 Sinsemilla 在**同一批行**上并排跑（注释 "we can fit two instances side-by-side"），左边算 `cm_old`/Merkle 路径，右边算 `cmx`/NoteCommit。CommitIvk 复用左半区，NoteCommit 复用右半区——同一列在不同行上归不同 chip，靠 region 的行范围隔离。

### 2.3 依据三：10 列全部 `enable_equality`

```rust
// circuit.rs:389-391
for advice in advices.iter() {
    meta.enable_equality(*advice);
}
```

不是随手写的。至少三处硬需求：

- `assign_free_advice` 返回的 `AssignedCell` 后续要被 `copy_advice` 到别处（例如 `psi_old`、`rho_old` 被 Pod 进 Poseidon 的 state 列）；
- ECC 点坐标的 cell 要被 `constrain_equal` 与实例/其他点对齐（`pk_d_old equality`，`circuit.rs:746`）；
- 跨地址检查要把 ECC 算出来的 4 个坐标**复制**进门行（`circuit.rs:999-1010`）。

一条 `copy_advice` 就要求源列和目标列都在置换论证里，所以 10 列必须全开。代价是置换论证宽度变成 15 列 → 3 个 product 多项式（§1）。

---

## 3. `q_orchard` 的四条约束

```rust
// circuit.rs:350-370
Constraints::with_selector(q_orchard, [
    ("v_old - v_new = magnitude * sign", v_old - v_new - magnitude * sign),
    ("Either v_old = 0, or root = anchor", v_old * (root - anchor)),
    ("v_old = 0 or enable_spend = 1",      v_old * (1 - enable_spend)),
    ("v_new = 0 or enable_output = 1",     v_new * (1 - enable_output)),
])
```

`Constraints::with_selector` 会把每条都乘上 `q_orchard`，所以门关闭时 4 条全部退化为 `0 = 0`。

### 3.1 约束 ①：`v_old − v_new = magnitude · sign`

这是**对值平衡的第二次检查**，但检查对象不是承诺，而是标量本身。

真正的值绑定在别处完成：`magnitude`/`sign` 被喂给 `ScalarFixedShort`（`circuit.rs:641`），做定基标量乘得到 `cv_net = [v_net]V + [rcv]R`，再与公开实例的 `cv_net` 两个坐标对齐（`circuit.rs:660-661`）。那里 `sign` 由 `mul_fixed::short` 的门约束成 ±1（`sign² − 1 = 0`，`halo2_gadgets ecc/chip/mul_fixed/short.rs:52`），`magnitude` 由 `magnitude_sign()` 的 u64 语义 + range check 保证落在 64 bit 内。

那么门里这条还防什么？防的是**证明者让 `v_old`/`v_new` 与 `magnitude`/`sign` 脱钩**。`v_old`、`v_new` 是私有 witness，除了这条门之外没有任何约束把它们和承诺连起来——没有它，证明者可以宣称任意 `v_old`/`v_new`，只要 `cv_net` 对得上。

注意 `sign` 在这个门里**没有**被约束为 ±1。门的可靠性不依赖这一点：`magnitude·sign` 只是把两个数绑在一起，`sign` 的合法性由 ECC 那条路保证。这是"约束分工"而不是疏漏。

### 3.2 约束 ②：`v_old · (root − anchor) = 0`

**这条是四条里唯一"承载"跨地址检查的**（§6 会看到它被整个复用）。

它编码的是一个析取：`v_old = 0` **或** `root = anchor`。也就是"这一行要么没有真实花费（值为 0 的填充 action），要么 Merkle 路径算出来的根必须等于公开的 anchor"。

`root` 来自 `MerklePath::calculate_root`（`circuit.rs:599-607`），是电路**自己算出来**的；`anchor` 是从 instance 列 `assign_advice_from_instance` 拷进来的（`circuit.rs:862-868`）。所以这条同时钉住了：Merkle 路径有效、且路径对应的是验证者认可的那个锚点。

### 3.3 约束 ③④：`v_old · (1 − enable_spend) = 0`、`v_new · (1 − enable_output) = 0`

同样是对偶析取：

- 只要 `v_old ≠ 0`，就强制 `enable_spend = 1`；
- 只要 `v_new ≠ 0`，就强制 `enable_output = 1`。

这两条把**电路内部的"这笔 action 是否真有花费/输出"**与**公开实例里的两个布尔标志**绑定。没有它们，一个 `enable_spend = 0` 的 coinbase action 可以偷偷带一个非零 `v_old`。

反方向**不成立**：`enable_spend = 1` 但 `v_old = 0` 是允许的（零值花费，伪造 spend 就是这么来的）。这正是 NU6.3 迁移交易里 change output 要配一笔零值 spend 的机制基础——`enable_spend = 1` 只是"声明我要花"，不代表"值不为零"。

### 3.4 一个不在门里的东西

`v_old`、`v_new` 都是**私有**的。验证者只看得到 `cv_net`（两个坐标）。所以"值守恒"在电路里是：`v_old − v_new = magnitude·sign`（门 ①）+ `cv_net` 与实例一致 + `magnitude`/`sign` 的 range/bool 约束，三者合起来。任何单一环节都不足以推出结论。

---

## 4. 8 个 chip 如何共享列、selector、lookup

### 4.1 fixed 列：14 个，跨 chip 复用

| fixed 列号 | 用途 | 谁声明的 |
|---|---|---|
| 0, 1, 2 | Sinsemilla 生成元查表（`table_idx`, `table_x`, `table_y`） | `circuit.rs:377-382`，并由 `constants` dump 反证：`lagrange_coeffs[0]` 落在 fixed 3 |
| 3–10 | `lagrange_coeffs[0..8]` | `circuit.rs:398-407` |
| 11, 12 | 两套 Sinsemilla 各自的 `q_sinsemilla2` | 由 `sinsemilla/chip.rs:190` 每次 `configure` 各建 1 个；**列号是扣除法推断**（见 §7） |
| 13 | 未能定位到具体来源，见 §7 | — |

这批 lagrange 列的复用是**省证明体积的关键手段**，源码注释写得很直白：

```rust
// circuit.rs:393-397
// Poseidon requires four advice columns, while ECC incomplete addition requires
// six, so we could choose to configure them in parallel. However, we only use a
// single Poseidon invocation, and we have the rows to accommodate it serially.
// Instead, we reduce the proof size by sharing fixed columns between the ECC and
// Poseidon chips.
```

具体怎么共用的：

```rust
// circuit.rs:408-409
let rc_a = lagrange_coeffs[2..5];   // ＝ fixed 5,6,7
let rc_b = lagrange_coeffs[5..8];   // ＝ fixed 8,9,10
```

Poseidon 的轮常量就写在这 6 个 fixed 列里——**和 ECC 定点标量乘的 lagrange 系数是同一批列**。ECC 用的时候是拉格朗日系数，Poseidon 用的时候是轮常量，行范围不重叠所以不冲突。固定列不是不占空间的免费资源：每一列都要在 keygen 时算一次多项式承诺，共用一列就省一次。

`enable_constant(lagrange_coeffs[0])`（`circuit.rs:413`）让 fixed 3 兼作全局常量列，`assign_advice_from_constant` 都落在它上面——包括跨地址检查里的 `zero`/`one`。

### 4.2 selector：56 个逻辑选择器

按来源粗分：

| 来源 | 数量级 |
|---|---|
| `q_orchard` + `AddChip` | 2 |
| ECC（witness_point / add_incomplete / add / mul 及其 hi-lo 子门 / mul_fixed 四种变体） | ~14 |
| Poseidon（`s_full`, `s_partial`, `s_pad_and_add`） | 3 |
| RangeCheck（`q_lookup`/`q_running` 为 complex selector，`q_bitshift` 为 simple） | 3 |
| Sinsemilla ×2（`q_sinsemilla1` complex、`q_sinsemilla4`，加 D&D 的门） | 每套数个 |
| Merkle ×2（`q_decompose` + CondSwap） | 每套数个 |
| CommitIvk | 1 |
| NoteCommit（b/d/e/g/h + `g_d`/`pk_d` canonicity） | 7 |

**注意 complex selector 的代价**：`meta.complex_selector()` 需要两列的组合，`meta.selector()` 只需一列。RangeCheck 里有 2 个 complex（`q_lookup`、`q_running`）就是这样——它被称为"最便宜的检查"，但仍要占列。

上表的分项是**估算**（按 gadget 源码里 `meta.selector()` 的调用点数出来的），只有总数 **56** 是实测值。

### 4.3 lookup：3 个，共享同一张表

dump 里 `lookups` 有 3 个 `Argument`，它们的 table 表达式分别是：

| lookup | 引用的 fixed 表列 | 来源 |
|---|---|---|
| #0 | `fixed 0` 单列 | RangeCheck：`meta.lookup(..., config.table_idx)` |
| #1 | `fixed 0, 1, 2` | Sinsemilla #1 的生成元表 |
| #2 | `fixed 0, 1, 2` | Sinsemilla #2 的生成元表 |

也就是说 `circuit.rs:378-382` 只创建了**一组**三列表，但**两个** Sinsemilla 实例各自注册了一个 lookup 论证去查同一张表。`SinsemillaChip::load` 只调一次（`circuit.rs:527`）也是这个原因——表装一次，两个实例共用。

RangeCheck 的 `table_idx` 恰好就是这张表的第一列，所以"查表 0..2^k"和"查生成元"共用列。

---

## 5. 最关键的一条：`configure` 与电路版本无关

```rust
// circuit.rs:1060-1062
fn configure(meta: &mut plonk::ConstraintSystem<pallas::Base>) -> Self::Config {
    Config::configure(meta)
}
```

`configure` 的签名里**没有** `circuit_version`，`Config::configure` 也没有版本分支。三个版本（`InsecurePreNu6_2` / `FixedPostNu6_2` / `PostNu6_3`）**共享完全相同的列、门、选择器和次数**。

上游把这句话写成了断言，就在 `round_trip_for_version` 里：

```rust
// circuit.rs:1809-1813
// These sizes are identical for every circuit version: the post-NU 6.3 circuit
// reuses the existing Orchard checks gate on spare rows and adds no columns or
// commitments, leaving the proof shape unchanged.
assert_eq!(usize::from(circuit_cost.proof_size(1)), 4992);
assert_eq!(usize::from(circuit_cost.proof_size(2)), 7264);
```

对应 `Proof::expected_proof_size`（`lib.rs:156-163`）：`BASE = 2720`、`PER_ACTION = 2272`。

这两条加起来，直接解释了 V3 实验里那个反直觉的结果：

- **证明成本的主项由 (advice 列数, 置换列数, k) 决定**——三者都是版本无关的，所以 IPA 的 MSM 规模、承诺数量、域大小全都不变；
- 版本差异**只**体现在 `synthesize` 多铺的 4 行；
- 4 行 / 2048 行 = 0.2% 的门求值，落在测量的噪声里。实测 p≈0.14 未检出差异，与这个结构一致。

但要说清楚边界：**"证明形状不变"不等于"约束零成本"**。多出来的 4 行仍要参与门求值与商多项式计算，只是这部分相对 MSM/FFT 太小。把"被 2¹¹ 填充吸收"当结论是错的，正确的说法是"增量落在可检出阈值以下，且主项由形状决定"。

---

## 6. `synthesize_cross_address_checks`：把 4 行塞进已有的门

这是 NU6.3 唯一新增的电路部分，也是全项目最省的一个设计——**不新建门、不新建列、不新建承诺**。

### 6.1 复用原理

门 ②（`v_old · (root − anchor) = 0`）的形状恰好就是 `D · (old − new) = 0`。把 8 个 advice 单元接成下面这样，4 条约束就全部成立：

| 单元 | 接什么 | 代入后 |
|---|---|---|
| `advices[0]` | `disableCrossAddress`（从 instance 拷入） | `v_old = D` |
| `advices[1]` | 常量 `0` | `v_new = 0` |
| `advices[2]` | `disableCrossAddress`（复制） | `magnitude = D` |
| `advices[3]` | 常量 `1` | `sign = 1` |
| `advices[4]` | 旧地址坐标 | `root` |
| `advices[5]` | 新地址坐标 | `anchor` |
| `advices[6]` | 常量 `1` | `enable_spend = 1` |
| `advices[7]` | 常量 `1` | `enable_output = 1` |

代入四条约束：

```
① D − 0 − D·1 = 0            → 恒成立（占位，无信息）
② D · (旧坐标 − 新坐标) = 0    ← 真正的跨地址检查
③ D · (1 − 1) = 0            → 恒成立
④ 0 · (1 − 1) = 0            → 恒成立
```

三条被"中和"掉，只有 ② 起作用。源码里 `"one (neutralize enable_spend check)"` 这种标签就是在提醒读者这不是笔误。

### 6.2 为什么是 4 行

`(g_d, pk_d)` 是 Pallas 上的两个仿射点，各有 x、y 两个坐标：`g_d.x / g_d.y / pk_d.x / pk_d.y`（`circuit.rs:954-959`）。**4 个坐标 → 4 行**，每行强制一个坐标相等。两个点四个坐标全部相等，就等价于两个扩张接收地址相同。

### 6.3 一个不显眼但重要的细节：占满 10 列

```rust
// circuit.rs:1027-1041  用 D 填满 advices[8]、advices[9]
cross_address_disabled.copy_advice(…, config.advices[8], offset)?;
cross_address_disabled.copy_advice(…, config.advices[9], offset)?;
```

这两列**不被 `q_orchard` 约束**（门只查 0..7），填它们纯粹是为了**占位**：

```rust
// circuit.rs:936-938
// `disableCrossAddress` so these rows occupy every advice column; that prevents
// the floor planner from overlapping another selector-enabled region with the
// check rows.
```

如果只写 8 列，FloorPlanner V1 认为第 8、9 列在这 4 行上是空闲的，可能把别的 region 排进来；那个 region 一旦 `enable` 自己的 selector，同一行上就会有两条门同时生效，而门 ② 的中和策略只对它自己那 8 个单元成立——两条门的约束会互相打架或不成立。**填满 10 列 = 在列维度上把行"锁住"**。

代价是每次证明多 8 个 cell 赋值（4 行 × 2 列），可以忽略。

### 6.4 布尔性不是必要的

`disableCrossAddress` 在公开 API 里被编码成 0/1，但这条约束**不依赖**这一点：

- `D = 0` → 约束 ② 退化成 `0 = 0`，不检查；
- `D` 为任何非零值（甚至 `D = 7`）→ 仍强制 `旧坐标 = 新坐标`。

因为 `D · Δ = 0` 在域上只有两种解：`D = 0` 或 `Δ = 0`。所以即便有人构造出 `D = 2` 的 instance，检查依然是"要么关，要么强制同地址"，不会漏。源码注释也明确写了这一点（`circuit.rs:932-933`）。

---

## 7. 未定位项与它为什么不影响结论

**14 个 fixed 列里，13 个能追到出处，第 13 号（下标 13）没有定位到。**

已确认的链条：`constants` dump 显示 `lagrange_coeffs[0]` = fixed **3**，反推出查表列是 0–2、lagrange 是 3–10；`permutation` dump 显示参与置换的 fixed 列是 3、8、9、10，而 8/9/10 正是 `rc_b = lagrange_coeffs[5..8]`，与 Poseidon 的 `enable_equality(rc_b)` 吻合——这条交叉验证把 0–10 共 11 列的归属锁死了。

剩下 3 列（11、12、13）里，必须有两列是两套 Sinsemilla 的 `q_sinsemilla2`（`sinsemilla/chip.rs:190`，每次 `configure` 建一个），第 3 列来源未知。所以 §4.1 表里把 `q_sinsemilla2` 标在 11、12 上，是按"扣除法 + 创建顺序"推的，**没有直接证据**证明它不是 12、13。

它不影响任何结论：

- 不影响 §4.1 的共享关系（那些列号有 `permutation` 和 `constants` 的 dump 直接佐证）；
- 不影响 §5 的版本论证（论证基于 `configure` 无版本参数 + 上游的 proof size 断言）；
- 不影响 §6（跨地址检查不用 fixed 列，只用常量和 instance 拷贝）。

要定位它需要逐段配置 CS，得改上游源码或写一个能分步调用 `Config::configure` 的副本，性价比不高。记在这里，等真正做到 `keygen` 级剖析时一并处理。

---

## 8. 复现方式

```powershell
cd D:\Research\zcash-orchard-engineering\experiments\transaction_e2e\harness
cargo run --release --bin cs_dump -- ..\..\..\results\halo2\configure
```

输出：

- 终端：`DEGREE` / `BLINDING_FACTORS` / `MINIMUM_ROWS` / 前几行 `HEAD`；
- 文件：`cs_pinned.txt`，`ConstraintSystem::pinned()` 的完整 `Debug` dump。

工具本身不依赖上游改动：`Config::configure` 是私有的，但 `Circuit` trait 的 `configure` 是公开的，只是转发给它。上游仓库保持 clean 在 `616a669d`。

---

## 9. 这一节回答了原来的四个问题

| 问题 | 答案位置 |
|---|---|
| 10 个 advice 列各自承担什么职责 | §2（含三条依据） |
| `q_orchard` 四条约束分别约束什么 | §3（逐条给出数学含义 + 它防什么） |
| 各 Gadget 如何复用列、Selector、Lookup | §4（fixed 跨 chip 共用、56 selector、3 lookup 共享一张表） |
| `PostNu6_3` 加了哪些约束、为什么 Action 计数变了而单 Action 成本没变 | §5 + §6（configure 无版本参数、4 行复用旧门、proof shape 不变；计数规则变在 `bundle.rs` 层，与电路无关） |
