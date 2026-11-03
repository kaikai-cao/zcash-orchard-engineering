# 06 · Variable-base 标量乘：`[ivk] g_d_old`

上游固定版本 `616a669d`；`halo2_gadgets 0.6.0` + `halo2_proofs 0.4.0`（按 Orchard
`Cargo.lock`，不混用后续版本的基准）。

这一份回答：**Orchard Action 里唯一一次变基标量乘，究竟做了什么、占多少行、边界在哪。**
它接在 `05_nf_old.md`（定基、BaseFieldElem、85 窗口 + 规范性）之后，主题正好相反：
同样是 Pallas Base 输入，机制却完全不同。

---

## 0. 先固定三个容易混淆的对象

| 名称 | 出处 | 是什么 |
|---|---|---|
| `OrchardCircuitVersion` | Orchard `circuit.rs` L136–147 | Orchard 自己的三档电路版本（`InsecurePreNu6_2` / `FixedPostNu6_2` / `PostNu6_3`） |
| `halo2_gadgets::ecc::chip::CircuitVersion` | halo2 `ecc/chip.rs` L43–53 | **ECC chip 内部**的两档（`InsecureUnanchoredBase` / `AnchoredBase`） |
| `ScalarVar::BaseFieldElem` | halo2 `ecc/chip.rs` L449–466 | 变基乘标量的唯一已实现形态 |

Orchard 通过 `halo2_version()`（`circuit.rs` L168–175）把前者映射到后者：
`InsecurePreNu6_2 → InsecureUnanchoredBase`，其余两档 → `AnchoredBase`。
所以「`CircuitVersion::AnchoredBase`」是 **halo2 的**类型，不是 Orchard 的版本枚举；
两者不是同一层的东西，`06` 后面 §8 会用到这个区分。

---

## 1. `ivk` 从哪里来，`ScalarVar::from_base` 花了多少

`circuit.rs` L706–727：

```rust
let ivk = {
    let ak = ak_P.extract_p().inner().clone();
    let rivk = ScalarFixed::new(ecc_chip.clone(), ..., self.rivk.map(|r| r.inner()))?;
    gadget::commit_ivk(
        config.sinsemilla_chip_1(), ecc_chip.clone(), config.commit_ivk_chip(),
        layouter.namespace(|| "CommitIvk"), ak, nk, rivk,
    )?
};
let ivk = ScalarVar::from_base(ecc_chip.clone(), layouter.namespace(|| "ivk"), ivk.inner())?;
```

`commit_ivk` 内部是 `CommitDomain::short_commit`（Sinsemilla，定基），产出 `Extract_P(·)`；
**`ivk` 不是一个新算出来的 cell，就是那次 `extract_p` 的结果 cell。**

`ScalarVar::from_base`（`ecc.rs` L225–233）→ `chip.scalar_var_from_base`（`chip.rs` L712–718）：

```rust
fn scalar_var_from_base(&self, _layouter: &mut impl Layouter<pallas::Base>, base: &Self::Var)
    -> Result<Self::ScalarVar, Error> {
    Ok(ScalarVar::BaseFieldElem(base.clone()))
}
```

**F25｜`ScalarVar::from_base` 这一步是零成本转换。** 它不分配 cell、不加门、不建
region，只是给已有 `AssignedCell` 套一层枚举标签。因此，**从已经算好的 `ivk` 到计算
`pk_{d,old} = [ivk] g_d` 这一步的电路开销，全部落在 `g_d_old.mul(...)` 里，转换本身是
0 行。**

但必须划定边界：`ScalarVar::from_base` 零成本，**不代表整个地址派生没有别的成本**。
`ivk` 本身由独立的 `CommitIvk` 电路路径算出（`commit_ivk` 里的 Sinsemilla 哈希 +
`rivk` 的固定基乘法），那部分行数应**单独记账**，不能因为 `from_base` 免费就误归到本篇
的 137 行里。本篇的"137 行"只描述 `[ivk] g_d` 这一次变基乘，不含 `CommitIvk`。

顺带一个语义边界：`ScalarVar` 的文档注释（`chip.rs` L449–460）明说
"You are not guaranteed that a scalar field element fits in a curve's base field, and in
particular it is untrue for the Pallas curve, whose scalar field Fq is larger than its base
field Fp"——但 Orchard 只用它做 `[ivk] g_d`，而 `ivk` 由 `Extract_P` 产生，天然在基域内。
这是**协议层**的不变量，不是电路层证明的；`FullWidth` 变体至今 `unimplemented`。

---

## 2. `k = ivk + t_q`：为什么要绕这一下

### 2.1 数论部分

Pallas 标量域阶 `q = 2^254 + t_q`，其中（`ecc/chip/constants.rs` L29）

```text
T_Q = 45560315531506369815346746415080538113 = 0x224698fc0994a8dd8c46eb2100000001
```

这个常量在门里能直接看到（`overflow checks` 的 `recovery` 约束右侧即 `0x224698fc0994a8dd8c46eb2100000001`），
说明它不是注释里的约定，而是**电路里硬编码的常数**。

于是 `k = ivk + t_q` 满足

```text
2^254 + k = 2^254 + ivk + t_q = ivk + q  ⟹  [2^254 + k]T = [ivk]T
```

`decompose_for_scalar_mul`（`ecc/chip/mul.rs` L425–459）就是按这个式子取位：
`U256::from_little_endian(scalar.to_repr()) + T_Q`，**不做模约减**，然后取低 255 bit、
反转为大端 `bits[0] = k_254 … bits[254] = k_0`。

数值验证（5 个样本，含边界 `q-1`）：`(2^254 + k) mod q == ivk` 全部成立；
`(2^255 + k) mod q == ivk` 全部不成立。所以偏移量是 **2^254**，不是 2^255。

### 2.2 但"最高位常数 1"不在 `k` 里

这一点很容易读错，必须写清：`t_q ≈ 2^125`，而典型 `ivk` 只有 ~126 bit，
所以 **`k = ivk + t_q` 的 bit 254 通常是 0**（实测：`ivk=7` 时 `k` 只有 126 bit）。
只有当 `ivk ≥ 2^254 - t_q = p - (t_p + t_q)`（即 `ivk` 落在一个很窄的高位区间，精确下界是
`p - (t_p + t_q)`，不要写成 `p - 2t_q` 的近似）时，`k_254` 才为 1。这里 `p = 2^254 + t_p`
是 Pallas **基域**模数、`q = 2^254 + t_q` 是 Pallas **标量域**模数，`t_p ≠ t_q`，
所以 `p - (t_p + t_q)` 与 `p - 2t_q` 不是一回事；该区间的精确写法是 `[p - (t_p + t_q), q)`。

那"常数 2^254"由谁提供？由**累加器的初值**：

```rust
// mul.rs L188–190
let acc = self.add_config.assign_region(&base_point, &base_point, offset, &mut region)?;
```

即 `Acc = [2]T`（完整加法 `T + T`），行 0 上的一行。变数部分才是 255 个见证 bit。
这样"最高位的 1"是电路内生的常数，不需要见证它、也不可能被 prover 篡改——
这正是 `[2^254 + k]` 相对 `[k]` 的好处：`k` 的最高位是变量（可能 0 可能 1），
而 `2^254 + k` 的最高位恒为 1（只要 `k < 2^254`，见 §5 的边界讨论）。

**F26｜`[ivk]g_d` 的行数 = 137（实测），与按 offset 手算完全一致。** 手算：
行 0 初始化 `[2]T`；hi 半 125 bit（行 1 起）、lo 半 126 bit（同一批行、不同列）；
完整加法 3 bit × 2 行；LSB 1 行；末行 136。见 §6 实测表。

### 2.3 证据边界

`Acc=[2]T` 到 `2^254 + k` 之间的**逐位权重记账**（哪一行做 `A+P`、哪一行做 `2A+P`、
`q_mul_3` 行是否补一次加倍）我没有在本笔记里逐步重推。理由：`q_mul_2` 门的六条约束
把 `A+P` 与 `2A+P` 合并在一行完成（`λ1` 走割线、`λ2` 走切线），逐行语义要从
`gradient_1 / secant_line / gradient_2` 三条式子反解，重推极易错，而且它对
"137 行""成本归属"这两个结论没有影响。**能被实测锚住的是行数与 selector 数（§6），
能被数论锚住的是 `2^254 + k ≡ ivk (mod q)`；两者之间的指数链我不做断言。**

---

## 3. 255 bit 怎么切：hi / lo / complete / LSB

常量（`mul.rs` L27–45）：

| 常量 | 值 | 含义 |
|---|---|---|
| `NUM_COMPLETE_BITS` | 3 | `k_3, k_2, k_1` 用完整加法 |
| `INCOMPLETE_LEN` | 255 − 1 − 3 = **251** | `k_254 … k_4` 用不完整加法 |
| `INCOMPLETE_HI_LEN` | 125 | `k_254 … k_130` |
| `INCOMPLETE_LO_LEN` | 126 | `k_129 … k_4` |
| `COMPLETE_RANGE` | 251..254 | `k_3, k_2, k_1` |
| LSB | `bits[254]` | `k_0` 单独处理 |

### 3.1 hi/lo 不是"两段计算"，是"同一批行、不同列"

`mul.rs` L212–235：两次 `double_and_add` **传的是同一个 `offset`**：

```rust
let (x_a, y_a, zs_incomplete_hi) = self.hi_config.double_and_add(&mut region, offset, base, bits_incomplete_hi, (X(acc.x), Y(acc.y), z_init.clone()), circuit_version)?;
let z = zs_incomplete_hi.last().expect("should not be empty");
let (x_a, y_a, zs_incomplete_lo) = self.lo_config.double_and_add(&mut region, offset, base, bits_incomplete_lo, (x_a, y_a, z.clone()), circuit_version)?;
```

列分配（`mul.rs` L87–98，实测门表达式完全吻合）：

| 角色 | hi 半 | lo 半 |
|---|---|---|
| `z`（running sum） | `advices[9]` | `advices[6]` |
| `x_a` | `advices[3]` | `advices[7]` |
| `x_p` | **`advices[0]`（共用）** | **`advices[0]`** |
| `y_p` | **`advices[1]`（共用）** | **`advices[1]`** |
| `lambda_1` | `advices[4]` | `advices[8]` |
| `lambda_2` | `advices[5]` | `advices[2]` |

`configure` 里两条 `assert_eq!`（`mul.rs` L109–117）显式钉住 `x_p`/`y_p` 共用。
共用是**安全的**，因为两半的基点 `base` 是同一个点，`x_p/y_p` 在同一条行上取值相同；
真正的独立性在 `z / x_a / lambda_1 / lambda_2` 四列，两半互不重叠。

**F27｜`251 = 125 + 126` 不是把 251 bit 拆成两段各算一遍，而是让 126 行的行区间
承载 251 bit 的加法链。** 行占用 = `max(125, 126)`，不是 `125 + 126`。
这是变基乘在布局上最省的一处设计，也是它敢用 137 行做完 255 bit 的原因。

### 3.2 实测对账

hi 的 `q_mul_2` 激活 **124** 次 + `q_mul_3` **1** 次 = 125；lo 是 **125 + 1** = 126。
两个数正好等于位长。selector 计数与常量表互相印证，不是巧合。

---

## 4. 三种加法的门

全部在同**一个** region 内（`[ivk] g_d_old/variable-base scalar mul`），
靠不同 selector 区分阶段。门的规模（实测，见 `gate_table.txt`）：

| 门 | 约束数 | 最高次数 | selector | 用在哪一段 |
|---|---|---|---|---|
| `q_mul_1 == 1 checks` | 1 | 4 | S9 / S12 | hi/lo 的首行（`init y_a`） |
| `q_mul_2 == 1 checks` | 6 | 4 | S10 / S13 | hi/lo 的主循环 |
| `q_mul_3 == 1 checks` | 4 | 4 | S11 / S14 | hi/lo 的末行 |
| `Decompose scalar for complete bits of variable-base mul` | 2 | 3 | S15 | 完整加法的标量分解 |
| `LSB check` | 3 | 3 | S17 | 最后一位 |
| `complete addition` | 12 | 6 | S8 | 初值 + 完整段 + LSB 收尾 |
| `overflow checks` | 5 | **5** | S16 | 独立 region |

注意 `q_mul_1/2/3` 各出现**两次**（`gates_raw.txt` 里标为 `(instance 2)`），
因为 `incomplete::Config::configure` 被调了两次（hi 与 lo），各自 `meta.selector()`。
它们**共享同名门**但**不共享 selector**——这是"同一份约束、两套列"的实现方式，
不是重复定义。

### 4.1 不完整加法：`q_mul_2` 的六条

按 `incomplete.rs` L120–208 与实测表达式：

```text
x_p_check   : x_p@0 − x_p@1 = 0                       （基点跨行恒定）
y_p_check   : y_p@0 − y_p@1 = 0
bool_check  : (z@0 − 2 z@−1)·(1 − (z@0 − 2 z@−1)) = 0  （k_i ∈ {0,1}）
gradient_1  : λ1(x_A − x_P) = (y_A − y_P)              （割线：A + P）
secant_line : λ2² − x_A@1 − x_R − x_A = 0
gradient_2  : λ2(x_A − x_A@1) = …                      （切线：2A + P）
```

其中 `x_R = λ1² − x_A − x_P`，`Y_A = (λ1+λ2)(x_A − x_R)`，常数
`0x2000…11234c7e…` 是 `2^{-1} mod p`（`TWO_INV`）。
"一行同时产出 `A+P` 与 `2A+P`"就是它能在每行消费一个 bit 的原因。

**F28｜不完整性的前提是运行时检查，不是约束。** `double_and_add` 开头
（`incomplete.rs` L245–252）用 `error_if_known_and` 在**见证期**排除
"P 为恒等 / A 为恒等 / x_p = x_a" 三种退化，失败只在 prover 侧报错；
电路本身没有对应门。安全性依赖"这些情况在合法 witness 下不会发生"，
而不是"发生了也证不出来"。这一点与 §5 的 overflow 检查是两种不同性质的保护。

### 4.2 完整加法：为什么必须在这里出现

三个位置：行 0 的初值 `[2]T`、`k_3…k_1` 的 3 次迭代（每次 **2 行**，因为
`Acc+U` 与 `Acc+(Acc+U)` 各占一行）、LSB 收尾。`complete addition` 12 条约束、
最高 6 次（`3a`–`3d` 四条），远重于不完整加法的 4 次。

**必须用完整加法的理由**是 LSB 那一行：`p` 可能是恒等点 `(0,0)`
（`k_0 = 1` 时 `p = 0`），且 `acc` 也可能落到恒等，不完整加法在这里没有定义。
`process_lsb` 的注释（`mul.rs` L322–326）把行布局画了出来。

### 4.3 LSB：为什么单独一行

```text
lsb = z_0 − 2 z_1   ⟹  k_0 = z_0 − 2z_1
bool_check(lsb)
lsb_x : (1−lsb)·(x_p − base_x) + lsb·x_p       （lsb=0 取 base_x，lsb=1 取 0）
lsb_y : (1−lsb)·(y_p + base_y) + lsb·y_p       （lsb=0 取 −base_y，lsb=1 取 0）
```

即 `p = −T`（`lsb=0`）或 `p = 𝒪`（`lsb=1`），最后 `result = p + acc`。
`ternary(lsb, x_p, x_p − base_x)` 的写法让这一行同时完成"取负"与"置零"两件事。

---

## 5. Overflow checks：5 条约束、1 次 13 词 lookup

位置是**独立的 region**（`overflow check`，实测 3 行、`advices[6..9]`、S16 激活 1 次），
不在 137 行那个大 region 里。

行布局（`overflow.rs` L131–180，与实测列一致）：

| 行 | `advices[6]` | `advices[7]` | `advices[8]` |
|---|---|---|---|
| 0 | `z_0` | `k_254` | — |
| 1 | `z_130` | `alpha` | `s` |
| 2 | `η = inv0(z_130)` | `s_minus_lo_130` | — |

（门在 offset+1 处启用，所以门读的是 `@−1 / @0 / @1` 三行。）

五条约束（实测表达式，逐字）：

```text
s_check              : s − (alpha + k_254·2^130)                     deg 2
recovery             : z_0 − alpha − t_q                             deg 2
lo_zero              : k_254 · (z_130 − 2^124)                       deg 3
s_minus_lo_130_check : k_254 · s_minus_lo_130                        deg 3
canonicity           : (1 − k_254)·(1 − z_130·η)·s_minus_lo_130      deg 5
```

外加一次 lookup：`copy_check(s, num_words = 130/10 = 13, strict = false)`
（`overflow.rs` L199–206）。`strict=false` 很关键——它把第 13 个 running sum
（`s_minus_lo_130 = (s − Σ_{i<130} s_i 2^i)/2^130`）**交回门里**条件约束，
而不是要求它归零。

**F29｜这 5 条约束都要在 `𝔽_p` 中成立——它们是门等式，不是整数等式。** 区分两层语义：

- *门等式（在 `𝔽_p` 上）*：`s_check` 约束 `s ≡ α + k_254·2^130 (mod p)`，`recovery`
  约束 `z_0 ≡ α + t_q (mod p)`；另外三条（`lo_zero` / `s_minus_lo_130_check` /
  `canonicity`）同样是在 `𝔽_p` 上的多项式等式，所以"只有 recovery 是模 p 的"这个说法
  不准确。
- *整数语义（位串的正确取值范围）*：单看 `recovery`，prover 确实可以用
  `k' = k + p` 的位串（同样满足 `z_0 ≡ α + t_q`，且 `[k']T ≠ [k]T` 因为 `p` 不是
  `q` 的倍数）。把位串真正钉在**整数**区间 `[0, 2^255)` 上的，是 `lo_zero` /
  `s_minus_lo_130_check` / `canonicity` 三条**结合条件约束**、外加 **13 次 10-bit
  lookup**（`copy_check`，`strict=false`）的**组合效果**，不是某一条等式单独完成的。
  Halo2 Book 对这套范围检查有完整推导。

一句话：**五条都是模 p 的门等式**；差别在于 `recovery` / `s_check` 给出模关系，
其余三条 + lookup 负责把整数取值范围收口。

**F30｜5 条里最高次数是 5（`canonicity`），因此这个门是 5 次，不是 9 次。**
它与 §5 的 9 次来源无关，也不是 `cs.degree()=9` 的因素。`s_check` / `recovery` 都是 2 次。

**关于 `k_254 = 1` 分支的几何读法**（之前列为证据边界，这里补清）：

` s = (α + 2^130) mod p ` 当 `k_254 = 1` 时，`α` 已经处于接近 `p` 的高位区间，
因此加上 `2^130` 会发生模 `p` 回绕，回绕后的 `s` 完全可能小于 `2^130`——这与
`s ≡ α + k_254·2^130 (mod p)` 中 `k_254 = 1` 并不矛盾，只是同一等式在 `𝔽_p` 中的
两种读法。更精确地，由 `p = 2^254 + t_p`、`q = 2^254 + t_q`、`k = α + t_q`：

```text
k_254 = 1  ⇒  α ≥ 2^254 − t_q = p − (t_p + t_q)
```

所以触发最高位为 1 的 `α` 落在一个很窄的高位区间，精确下界是 `p − (t_p + t_q)`，
不要写成 `p − 2t_q`（`t_p ≠ t_q`，后者至多是近似）。Halo2 Book 的完整推导见
`https://p.z.cash/halo2-0.1:ecc-var-mul-overflow`（源码注释即引此链接）。

我实测确认的部分：`t_q` 是硬编码常数、`η = inv0(z_130)`、13 词 lookup 的
`strict=false`、以及五条约束的条数与次数——这些来自实测表达式，不依赖对 book 的转述。

---

## 6. 实测布局：137 行、10 列、9 个 selector

工具：`experiments/transaction_e2e/harness/src/bin/layout_rows.rs`
（实现 `Assignment`，交给真实的 `floor_planner::V1::synthesize`；V1 内部的第一趟
measurement 用自己的 `Assignment`，所以记录器只看到**真正的那一趟**）。

结果文件：`results/halo2/synthesize/layout_v2.txt`、`layout_v3.txt`。

```text
298,[ivk] g_d_old/variable-base scalar mul/variable-base scalar mul,0,136,137,137,
    A0|A1|A2|A3|A4|A5|A6|A7|A8|A9,
    S10x124|S11x1|S12x1|S13x125|S14x1|S15x3|S17x1|S8x8|S9x1
```

| 项 | 值 |
|---|---|
| 行区间 | 0 – 136（**137 行**） |
| 用到的 advice 列 | **全部 10 列** |
| 完整加法（S8） | 8 次：初值 1 + 完整段 3×2 + LSB 1 |
| `q_mul_decompose_var`（S15） | 3 次（= `COMPLETE_RANGE.len()`） |
| `LSB check`（S17） | 1 次 |
| overflow 检查 | **不在**这个 region：独立 region 占行 247–249 |

对照：整个 Action 在 `FixedPostNu6_2` 下用掉 **0 – 1771 共 1772 行**（K=11 → 2048 行域，
余量 276 行），`PostNu6_3` 是 **1779 行**。

**F31｜`PostNu6_3` 的跨地址检查正好 +4 行、+24 copy、+4 次 `q_orchard` 激活，
0 个新 selector、0 个新门。** 实测：新增 region `post-NU 6.3 cross-address checks`
占行 1681–1684（4 行、10 列、`S0x4`）；全局 copy 数 2798 → 2822（+24）；
`S0` 激活 1 → 5，其余 selector 计数逐一不变。24 = 每行 6 次 copy（instance→`advices[0]`
1 次，`advices[2]/[4]/[5]/[8]/[9]` 各 1 次）× 4 行。

**F32｜行数不变／微增，不等于证明大小或 `k` 不变的原因被找到。**
`k` 由 1772（或 1779）与 2048 的比较决定，两者都落在 `k=11` 内，所以证明大小不变；
但 V1 的重排让总行数从 1772 变到 **1779**（+7，不是 +4）——**新增 4 行的 region 会让
floor planner 整体重排**。这条同时是"不要把 region 行数当成证明成本"的实证：
一个 4 行的 region 改变了全局行数，却没改变 `k`。

**F33｜137 行与手算 offset 一致**（这是我唯一用来交叉验证 V1 记录器的量）：
行 0 初始化 → 行 1 起 hi/lo → 完整段 3×2 行 → LSB 1 行，末行 136。
记录器给出的 `min/max = 0/136` 与手算逐项相同，说明记录器没有漏记 selector-only 行。

---

## 7. `derived_pk_d_old` 与 `pk_d_old`：一次 `constrain_equal`

`circuit.rs` L729–750：

```rust
let (derived_pk_d_old, _ivk) = g_d_old.mul(layouter.namespace(|| "[ivk] g_d_old"), ivk)?;

let pk_d_old = NonIdentityPoint::new(ecc_chip.clone(), layouter.namespace(|| "witness pk_d_old"),
                                     self.pk_d_old.map(|p| p.inner().to_affine()))?;
derived_pk_d_old.constrain_equal(layouter.namespace(|| "pk_d_old equality"), &pk_d_old)?;
pk_d_old   // ← 后面 note_commit 用的是这个
```

- `g_d_old.mul(...)` 返回 `(Point, ScalarVar)`；`_ivk` 被丢弃（注释："The scalar value is
  passed through and discarded"）。
- `constrain_equal` 在 `ecc.rs` 里做的是 x、y 各一次 `copy`，**2 条 copy 约束、0 行、
  0 个门**。实测里它表现为一个 `pk_d_old equality/constrain equal` 的**空 region**（表中 `-`）。
- 之后 `note_commit(..., g_d_old.inner(), pk_d_old.inner(), ...)`（L761–774）用的是
  **witness 出来的 `pk_d_old`**，不是 `derived_pk_d_old`。

源码注释自己说得很坦白（L734–740）：

> This equality constraint is technically superfluous, because the assigned value of
> `derived_pk_d_old` is an equivalent witness. But it's nice to see an explicit connection
> between circuit-synthesized values, and explicit prover witnesses.

**F34｜`pk_d_old` 的"正确性"由三段拼成，缺一不可：** ①`ivk` 由 `CommitIvk` 从
`ak/nk/rivk` 算出（§1）；②`[ivk]g_d_old` 由变基乘算出（§6）；③派生点被
`constrain_equal` 钉到见证点上（本节）。③本身"冗余"，但它把②的输出接进了
`note_commit` 的输入口——真正保证"旧 note 的地址与密钥一致"的是①②；③只是接口对齐。
换句话说：**删掉③不会让证明变弱（因为 `derived_pk_d_old` 已经约束好了），
但会让 `cm_old` 用的 `pk_d_old` 与地址检查脱钩**——所以它是必需的接线，不是多余的约束。

---

## 8. `AnchoredBase` 到底锚了什么

`incomplete.rs` L310–330（nu6.2 修复处，注释是上游写的）：

```rust
// The `q_mul_2` gate only constrains `(x_p, y_p)` to be *constant* across the
// incomplete-addition rows; it does not by itself tie them to the real `base`.
// The fixed circuit therefore `copy_advice`s `base` into the first loop row, so the
// `q_mul_2` constancy propagates the equality to every loop row, forcing the
// per-iteration base to equal `base`.
match circuit_version {
    CircuitVersion::AnchoredBase if row == 0 => {
        base.x.copy_advice(|| "anchor x_p to base", region, self.double_and_add.x_p, row + offset)?;
        base.y.copy_advice(|| "anchor y_p to base", region, self.y_p, row + offset)?;
    }
    _ => { /* 见证 x_p / y_p，不锚定 —— 只为重建旧 vk */ }
}
```

于是链条是：

```text
g_d_old 见证（NonIdentityPoint::new，只约束 on-curve / 非恒等）
  └─ g_d_old.mul(..., ivk) 把这个 cell 作为 base 传进去
       └─ AnchoredBase: base.x / base.y 被 copy 到主循环首行的 x_p / y_p
            └─ q_mul_2 的 x_p_check / y_p_check 把"等于 base"传播到每一行
                 └─ 结果 derived_pk_d_old = [ivk]·(那个被锚住的点)
                      └─ constrain_equal 到见证的 pk_d_old
```

**F35｜`AnchoredBase` 锚的是"`base` 参数这个 cell"，不是"`g_d_old`"。**
把 `base` 绑到 `g_d_old` 的是调用方（`g_d_old.mul` 传 `&self.inner`），
把 `g_d_old` 绑到 note 内容的是 `note_commit` 的输入。
三层里任何一层断了，地址完整性都失效——而 NU6.2 修的是**最里面**那层：
修复前 `x_p/y_p` 只是"跨行恒定的自由见证"，prover 可以挑任意常点当基点，
于是 `[ivk]·(任意点)` 也能对上 `pk_d_old`（只要 `pk_d_old` 是同一个点），
地址检查就变成空转。

**F36｜`InsecurePreNu6_2` 保留旧行为不是"兼容性妥协"，是"重建旧 vk"的唯一手段。**
`OrchardCircuitVersion` 的文档注释（L137–140）写明：仅供重建历史 verifying key
"never for proving or current verification"。`halo2_version()` 的映射是单向的：
`PostNu6_3` 与 `FixedPostNu6_2` **共用** `AnchoredBase`，
所以这两个版本的变基乘部分**逐字节相同**（实测 `layout_v2.txt` 与 `layout_v3.txt` 的
变基乘 region 完全一致：同 137 行、同 selector 计数）。

---

## 9. 结论清单

| # | 结论 | 依据 |
|---|---|---|
| F25 | `ScalarVar::from_base` 零成本（不分配 cell、不加门），但仅限 `ivk → pk_d_old` 这一步；`CommitIvk` 成本需单独记账，不计入 137 行 | `ecc.rs` L225–233、`chip.rs` L712–718 |
| F26 | 变基乘占 137 行（0–136），与手算 offset 一致 | `layout_v2.txt` |
| F27 | 251 bit 走 126 行：hi/lo 同区间、不同列，`x_p/y_p` 共用 | `mul.rs` L87–117、L212–235 |
| F28 | 不完整加法的退化条件是见证期检查，不是约束 | `incomplete.rs` L245–252 |
| F29 | overflow 的 5 条里只有 `recovery` 是模 p 的；其余钉整数表示 | 实测表达式 |
| F30 | overflow 门最高 5 次（`canonicity`），与 9 次无关 | `gate_table.txt` |
| F31 | 跨地址检查 +4 行 / +24 copy / +4 次 `q_orchard`，0 新门 | `layout_v3.txt` vs `layout_v2.txt` |
| F32 | 总行数 1772 → 1779（V1 重排），但 `k` 仍为 11 | 同上 |
| F33 | 记录器与手算行数互证 | `layout_rows.rs` |
| F34 | `pk_d_old` 由 commit_ivk + 变基乘 + constrain_equal 三段拼成 | `circuit.rs` L706–750 |
| F35 | `AnchoredBase` 锚的是 `base` cell；绑定到 `g_d_old` 是调用方的事 | `incomplete.rs` L310–330 |
| F36 | `FixedPostNu6_2` 与 `PostNu6_3` 的变基乘部分逐字节相同 | 两版本 layout 对比 |

---

## 10. 与定基乘的最终对照

| | `NullifierK`（定基，见 05） | `[ivk] g_d`（变基，本篇） |
|---|---|---|
| 基点 | 常数（`FixedPointBaseField`） | **见证点**（`NonIdentityPoint`） |
| 标量 | Pallas Base，85 个 3-bit 窗口 | Pallas Base → `k = ivk + t_q`，255 个 bit |
| 分解 | `RunningSumConfig`（纯门，次数 9） | 逐 bit，`z` 递推（次数 ≤ 4） |
| 主门 | `range check`（S18，85 窗口）+ `Canonicity checks`（S21） | `q_mul_1/2/3`（hi、lo 各一套） |
| 额外检查 | `Canonicity checks`（证 `α < p`） | `overflow checks`（钉整数表示）+ 锚定基点 |
| 次要计算 | 1 次 Poseidon + 1 次域加法 + 1 次完整加法 | 1 次初值加法 + 3 次完整加法 + 1 次 LSB 加法 |
| 实测行数 | 86（行 247–332） | **137**（行 0–136） |
| 结构占比 | 85 窗口 × 5 条长标量乘 + 1 次基域乘 = 532 窗口 | 251 不完整 + 3 完整 + 1 LSB |

一句话：**定基乘的开销在"窗口数"，变基乘的开销在"行数"。**
同样是 Pallas Base 输入，前者把标量拆成 85 个 3-bit 字（每字一次查表/门），
后者把标量拆成 255 个 bit（每 bit 一行），再用"一行两加"把它压到 126 行。
两者的倍率差别（85 窗口/次 vs 137 行/次）与它们在一个 Action 里出现的次数
（6 次 vs 1 次）方向相反，这也是为什么下一步的 profiling 假设要分开写，
不能笼统说"ECC 是热点"。

---

## 11. 工具与可复跑性

| 文件 | 作用 |
|---|---|
| `experiments/.../src/bin/layout_rows.rs` | 实现 `Assignment`，在真实 `V1` 下记录每个 region 的行范围／列／selector 次数 |
| `results/halo2/synthesize/layout_v2.txt` | `FixedPostNu6_2`，395 个 region，1772 行 |
| `results/halo2/synthesize/layout_v3.txt` | `PostNu6_3`，396 个 region，1779 行 |
| `scripts/analysis/gate_degree.py --table` | 门 → 约束数 → 次数 → selector 编号（含 `# selector,gate(s)` 反查表） |
| `results/halo2/synthesize/gate_table.txt` | 上述表格的落地文件 |

跑法：

```bash
cargo run --release --bin layout_rows -- v2      # 或 v3
python scripts/analysis/gate_degree.py results/halo2/synthesize/gates_raw.txt --table
```

两处实现约束（都写进了 `layout_rows.rs` 的注释）：

1. `Column::new` 与 `ConstraintSystem::constants` 都是 `pub(crate)`，无法把 Orchard
   用 `enable_constant(lagrange_coeffs[0])` 注册的那一列交给 `V1::synthesize`。
   于是传空列表，V1 在**赋值趟之后**返回 `Error::NotEnoughColumnsForConstants`；
   记录此时已完整，代码断言错误**恰好**是这一种，避免吞掉别的失败。
2. `Circuit::empty(version)` 虽是"为渲染布局而生"，但它是私有的；因此用真实
   1-spend Action（`ActionCircuit::from_action_context`）构造电路。见证值不参与记录，
   所以 fixture 的具体数值不影响行数结论。

---

## 12. 遗留

- ~~**`k_254 = 1` 分支的几何读法**（§5 证据边界）~~：已在 §5 补清——回绕语义 +
  精确下界 `α ≥ p − (t_p + t_q)`，只影响极端 `ivk`（`ivk ∈ [p − (t_p + t_q), q)`）
  的正确性论证，不影响成本结论。
- **逐位权重记账**（§2.3）：`Acc=[2]T` → `2^254 + k` 的指数链未重推。
- **`note_commit` 里的 `CommitDomain::commit`**：`self.R.mul` 是定基（`NoteCommitR`），
  已计入 05 的 5×85 窗口清点，但没有单独展开其 region 布局。
- 下一步自然入口：`synthesize()` 剩下的两块——new note commitment（`sinsemilla_chip_2`
  + `note_commit_chip_new`）与 new address 的见证路径；以及 `q_orchard` 的 4 条约束
  如何把本篇所有中间量收口。
