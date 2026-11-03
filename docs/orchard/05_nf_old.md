# `synthesize_base` 第四块：`nf_old`（DeriveNullifier）

上游固定于 `616a669d`。本篇接 `03_synthesize_base.md`（Spend witness / Merkle）与
`04_cv_net.md`（`cv_net` 与两套 range check），覆盖 `synthesize_base` 的模块 5（`circuit.rs`
L668–L682）以及它调用的 `gadget::derive_nullifier`（`gadget.rs` L154–L206）。

本篇额外做了一件前两份文档没做的事：**从真实 `ConstraintSystem` 里逐门测出多项式次数**，
把"`cs.degree() = 9` 来自哪"从推断变成可列举的证据（§6）。结论比预期更强：9 次约束有 **4 条**，
分布在 **3 个门**上，其中一条与范围检查无关。

---

## 0. 协议公式与代码的对应

$$\boxed{\;nf_{\text{old}} = \mathrm{ExtractP}\Big(cm_{\text{old}} + \big[\mathrm{Poseidon}(nk,\rho_{\text{old}}) + \psi_{\text{old}}\big]\mathcal{K}^{\text{Orchard}}\Big)\;}$$

四步计算 + 一次公开绑定 + 一次回喂：

| # | 步骤 | 代码 | gadget / 固定基 | 行号 |
|---|---|---|---|---|
| 1 | $h = \mathrm{Poseidon}_{P128Pow5T3}(nk,\rho)$ | `poseidon_hasher.hash(msg)` | `PoseidonChip`，`ConstantLength<2>`，WIDTH=3 / RATE=2 | `gadget.rs` L172–L182 |
| 2 | $a = h + \psi$ | `add_chip.add(&hash, psi)` | `AddChip`（域加法） | L184–L190 |
| 3 | $P = [a]\mathcal{K}$ | `nullifier_k.mul(scalar)` | `FixedPointBaseField<NullifierK>` | L192–L202 |
| 4 | $cm + P$ | `cm.add(&product)` | **完整加法**（`Point::add`） | L204–L205 |
| 5 | 取 X 并绑公开输入 | `constrain_instance(..., NF_OLD)` | — | `circuit.rs` L681 |
| 6 | **X 回喂给新 note** | `let rho_new = nf_old.inner().clone()` | — | `circuit.rs` **L805** |

`ExtractP` 就是 `Point::extract_p()`（取 $x$ 坐标），紧跟在第 4 步的 `.map()` 里，不是独立区域。

### 0.1 三个容易被忽略的事实

**F15｜第 4 步是完整加法，不是不完整加法。** `cm` 的类型是 `Point`，`Point::add` 走
`chip.add` = `add_config`（`ecc.rs` L345–L359；不完整加法是另一个方法 `add_incomplete`，
L365–L377）。实测差别很大：门 `complete addition` 有 **12 条**约束、最高 **6 次**
（`3a`–`3d` 四条），而门 `incomplete addition` 只有 **2 条**、最高 **4 次**。
选完整加法是必需的——`cm_old + P` 可能落在恒等点。

**F24｜`nf_old` 不是叶子输出。** 它一边绑到 `Instance[NF_OLD]`（L681），一边在 **L805** 被
`.clone()` 成 `rho_new`，喂给新 note 的 `note_commit`（L818–L830）。也就是说
$$\rho^{\text{new}} = nf^{\text{old}}$$
是**同一段电路内部**的等式，不需要额外的公开输入或 witness 去对齐。这条把 Action 的两侧
（Spend 与 Output）钉在一起：想改 output 的 $\rho$，必须先改 input 的 nullifier，而后者又被
`Instance` 钉死。对成本的影响也直接：`nf_old` 的 X 坐标同时是"公开输出"和"下游输入"，
所以它必须作为 `AssignedCell` 保留（`X` 类型），不能只做一次性约束。

**F16｜`\psi` 在电路里是纯 witness，没有任何子电路。** host 侧 $\psi = \mathrm{PRF^{expand}_{PSI}}(rseed, \rho)$
（`note.rs` L147–L149 由 `rseed`+`rho` 派生，L381–L383 是 `Note::psi()` 入口），`circuit.rs` L273
把它塞进 `Circuit::psi_old`。它只被第 2 步的 `AddChip` 消费一次，不参与任何哈希或分解。
它的作用是把 note 的 `rseed` 绑进 nullifier：只泄露 $(\rho, cm, nk)$ 不足以算出 $nf$。

**F17｜$\rho$ 与 $\psi$ 的分工不同。** $\rho$ 进 Poseidon（成为 nullifier 的"位置指纹"），
$\psi$ 只做加法（把 `rseed` 掺进去）。两者都在 host 侧算好，电路里只见证结果。

---

## 1. 列落点

沿用 `02_configure.md` §2 的列角色表，这一段恰好把 10 列里的 5 列串起来：

| 步骤 | 列（advice） | 说明 |
|---|---|---|
| Poseidon state | `[6] [7] [8]` | `state = advices[6..9]`（`circuit.rs` L425） |
| Poseidon S-box | `[5]` | `partial_sbox = advices[5]`，与 `anchor` 同列不同行 |
| AddChip | `a=[7] b=[8] c=[6]` | `AddChip::configure(meta, [7], [8], [6])`，输出在**左**列 |
| 标量复制 | `[6] → [4]` | `copy_decompose` 把 $a$ 复制进 `window` 列 |
| 85 窗口 / running sum | `[4]` | `RunningSumConfig.z == window`（`04_cv_net.md` §1） |
| 末窗口 / $u$ | `[5]` | 短标量路径把 $z_{21}$ 借放到 `u`；base-field 路径 85 窗口全部留在 `[4]` |
| 完整加法 | `[0] [1] [4] [5]` … | `add_config` 的 x/y/λ 列 |

一个细节值得记：AddChip 的输出 `c` 排在输入 `a,b` **左边**（6 号列），原因见 `02_configure.md`
（`mul` 的 $z,\lambda_1$ 与 `add` 输出同行，不能撞列）。所以 `nf` 这一段里，"$h+\psi$ 的和"
落在 `advices[6]`，而 `advices[6]` 同时是 `enable_spend` 的列——两者不同行，不冲突。

---

## 2. 三种标量乘的最终对照

这是本篇最该带走的一张表。三者的差别不在"窗口数"（后两者同为 85），而在**输入域**与
**规范性要求**：

| 类型 | 输入 | 分解方式 | 规范性 | 窗口 | 实测最高次数 |
|---|---|---|---|---|---|
| `ShortScalar` | 64-bit `magnitude` + `sign` | `RunningSumConfig`，strict，$z_{22}=0$ | $m<2^{64}$ + $s^2=1$ | **22** | 门 `range check` 9；`Short fixed-base mul gate` 3 |
| `FullScalar` | Pallas **Scalar** | `decompose_word` 直接切 | **不要求**唯一整数表示 | **85** | `Full-width fixed-base scalar mul` **9** |
| `BaseFieldElem` | Pallas **Base** | `RunningSumConfig`，strict，$z_{85}=0$ | **额外**证 $0\le\alpha<p$ | **85** | `range check` 9 + `Canonicity checks` 5 |

三个数都核过源码：

- `FIXED_BASE_WINDOW_SIZE = 3`（`fixed_bases.rs` L50），`NUM_WINDOWS = L_ORCHARD_SCALAR.div_ceil(3)`
  = ⌈255/3⌉ = **85**（L56），`NUM_WINDOWS_SHORT = L_VALUE.div_ceil(3)` = ⌈64/3⌉ = **22**（L59）。
- `L_ORCHARD_BASE = L_ORCHARD_SCALAR = 255`（`constants.rs` L25、L28），且有两个单测断言它们
  等于 `pallas::Base::NUM_BITS` / `pallas::Scalar::NUM_BITS`（L44–L53）。
- `NullifierK` 属于 `OrchardBaseFieldBases`（`fixed_bases.rs` L61–L81、L132–L134），
  `FixedPointBaseField::mul` 的分解调用是
  `copy_decompose(region, 0, scalar, true, pallas::Base::NUM_BITS, NUM_WINDOWS)`
  （`base_field_elem.rs` L179–L188）——所以**它确实也是 85 窗口**，你这条更正成立。

### 2.1 为什么 `FullScalar` 可以非规范，而 `BaseFieldElem` 不行

设 $p$（base）与 $q$（scalar）为 Pasta 两个模数，$p=2^{254}+t_p$，$q=2^{254}+t_q$，且
$t_p<t_q$ ⇒ $p<q$（`constants.rs` L18、L22 给出两个 $t$ 值，可逐位比较）。

- **FullScalar**：$a$ 来自 $\mathbb{F}_q$，$[a+q]G=[a]G$。85 个 3-bit 窗口覆盖 255 bit，
  任何 $<2^{255}$ 的整数都能表示；"$a$ 与 $a+q$"给出同一群元素，因此无需钉死代表。
- **BaseFieldElem**：$\alpha$ 来自 $\mathbb{F}_p$，但乘法把窗口当**整数**、在阶为 $q$ 的群里累加。
  于是 $[\alpha+p]\mathcal{K}\ne[\alpha]\mathcal{K}$ 一般成立（差 $[p]\mathcal{K}$，而 $p\not\equiv 0 \pmod q$）。

**关键量化**：85 窗口 + strict 只保证 $\alpha'<2^{255}$，即 $\alpha'\equiv\alpha \pmod p$ 且
$\alpha'<2^{255}$。这样的整数恰有两个候选：$\alpha$ 与 $\alpha+p$（后者可行当且仅当
$\alpha<2^{255}-p=2^{254}-t_p$，而 $t_p\approx 4.56\times10^{37}\approx 2^{125}$，即**几乎所有** $\alpha$）。
所以"缺规范性检查"不是理论瑕疵，而是一个**几乎处处可用**的自由度。规范性检查就是用来砍掉
第二个候选的。

---

## 3. 规范性检查：8 条门约束 + 1 条 13 词 lookup

`base_field_elem.rs` L62–L160（门）+ L255–L393（见证与布局）。

### 3.1 分解与大前提

$$\alpha=\alpha_0+2^{252}z_{84},\qquad z_{84}=\alpha_1+4\alpha_2$$

- $\alpha_1=\alpha[252..254]$（2 bit），$\alpha_2=\alpha[254]$（1 bit）；
- **$\alpha_0$ 是推导值，不是见证值**：门里 `alpha_0 = alpha - z_84 * 2^252`（L83–L87），
  `assign` 里也只 `assign_advice` 了 $\alpha_1,\alpha_2$（L322–L339）。这是本篇对你的提纲
  的一处校正：见证的自由度比"三段都见证"少一个。

### 3.2 逐条（名称与次数均为实测，见 §6）

| # | 约束 | 表达式 | 次数 | 作用 |
|---|---|---|---|---|
| 1 | `alpha_1_range_check` | $\alpha_1(1-\alpha_1)(2-\alpha_1)(3-\alpha_1)$ | 5 | $\alpha_1\in[0,4)$ |
| 2 | `alpha_2_range_check` | $\alpha_2(1-\alpha_2)$ | 3 | $\alpha_2\in\{0,1\}$ |
| 3 | `z_84_alpha_check` | $z_{84}-(\alpha_1+4\alpha_2)$ | 2 | 三段分解自洽 |
| 4 | `MSB = 1 => alpha_1 = 0` | $\alpha_2\cdot\alpha_1$ | 3 | $\alpha_2=1\Rightarrow\alpha_1=0$ |
| 5 | `MSB = 1 => alpha_0_hi_120 = 0` | $\alpha_2\big(z_{44}-2^{120}z_{84}\big)$ | 3 | 杀掉 $\alpha$ 的 132..251 位 |
| 6 | `MSB = 1 => a_43 = 0 or 1` | $\alpha_2\cdot\mathrm{bool}(z_{43}-8z_{44})$ | 4 | 杀掉 130、131 位 ⇒ $\alpha_0<2^{130}$ |
| 7 | `MSB = 1 => z_13 = 0` | $\alpha_2\cdot z^{\alpha'_0}_{13}$ | 3 | $\alpha_0<t_p$ |
| 8 | `alpha_0_prime check` | $\alpha'_0-(\alpha_0+2^{130}-t_p)$ | 2 | $\alpha'_0$ 的推导正确 |

其中 $z_{44}=\lfloor\alpha/2^{132}\rfloor$、$z_{43}=\lfloor\alpha/2^{129}\rfloor$、
$z_{84}=\lfloor\alpha/2^{252}\rfloor$，三者都是 running-sum 的中间值，由 `copy_advice`
从 `[4]` 列搬进 `canon_advices`（L360–L390）。

于是逻辑链是：

$$\alpha_2=0\Rightarrow\alpha<2^{254}<p;\qquad
\alpha_2=1\Rightarrow \alpha_1=0,\ \alpha_0<2^{130},\ \alpha_0+2^{130}-t_p<2^{130}\Rightarrow \alpha_0<t_p$$

### 3.3 一处需要说准的边界：只有**一条** 13 词 lookup

你的提纲写"13 个 10-bit Lookup 检查其范围"，这是对的，但读者容易误以为 $\alpha_0$ 也走一次。
实际代码只对 $\alpha'_0=\alpha_0+2^{130}-t_p$ 调一次：

```rust
let zs = self.lookup_config.witness_check(
    layouter.namespace(|| "Lookup range check alpha_0 + 2^130 - t_p"),
    alpha_0_prime, 13, false)?;
let alpha_0_prime = zs[0].clone();
(alpha_0_prime, zs[13].clone())
```
（L262–L275）

- `13` 个 10-bit 词 = 130 bit（`PallasLookupRangeCheckConfig = LookupRangeCheckConfig<pallas::Base, {sinsemilla::K}>`，
  `lookup_range_check.rs` L504，`sinsemilla::K = 10`）；
- 第 4 个参数 `strict = false`：**不**在 lookup gadget 内部强制 $z_{13}=0$，而是把它交回来，
  由门第 7 条 `alpha_2 * z_13` 在 $\alpha_2=1$ 时才要求为 0。这就是"条件规范性"的实现方式；
- "$\alpha_0<2^{130}$" 这一半**不是** lookup 做的，而是第 5、6 条两个 **gate 位约束**：
  第 5 条清掉 132..251 位（$\alpha_0<2^{132}$），第 6 条清掉 130、131 位（$\alpha_0<2^{130}$）。
  gate 里的注释确实写了 "13 ten-bit lookups of α_0"，但那是**注释**——代码里
  `alpha_0` 本身只被 running-sum 的 3-bit 窗口约束过，没有 10-bit lookup 链。

### 3.4 行占用

- running sum：$z_0..z_{85}$ = **86 行**（`[4]` 列）；
- coords check：与窗口同行（`x_p,y_p,u` 等），不再额外开行；
- canonicity 区域：**3 行**（offset 0 放 $\alpha$ 与 $z_{84}$，offset 1 放 $\alpha'_0,\alpha_1,\alpha_2$，
  offset 2 放 $z_{13},z_{44},z_{43}$），门在 offset 1 启用；
- lookup 区域：**14 行**（$z_0..z_{13}$）。

---

## 4. 成本结构：这一段只买一次定基乘

值得单独说明，因为它常被误算：`nf_old` 只用到**一次**定基乘（`NullifierK`），没有 Sinsemilla、
没有 variable-base 乘、没有 Merkle。相比之下 `cm_old` 的证明在模块 3（Sinsemilla + NoteCommit）
里已经付过账了，`nf_old` 只是把它**再用一次**（`cm` 作为 `Point` 传进来，直接参与第 4 步加法，
不需要重新算承诺）。

这也是为什么 `nf_old` 在结构上"便宜"：1 次 Poseidon（1 个 permutation）+ 1 次域加法
+ 1 次 85 窗口定基乘 + 1 次完整加法。

### 4.1 补答 `03_synthesize_base.md` 遗留的两个问题

**（a）$\psi$ 为什么走加法而不是再进一次 Poseidon？**
$\psi$ 本身已经是带 `rseed` 的 PRF 输出（`note.rs` L147–L149），把它**加**进
$\mathrm{Poseidon}(nk,\rho)$ 的结果里，就已经把 `rseed` 掺进了标量 $a$；再哈希一次不会增加
"$\psi$ 参与"这件事，却要多付一次 permutation（8 full + 56 partial 轮）。
代价对比是量级差：域加法 1 条门、最高 2 次；Poseidon 一个 permutation 是多轮 + 3 列 state。
安全性上这是协议定义（Zcash spec §4.16 的 DeriveNullifier），不是我在这里证明的——本篇只
负责指出"加法 vs 哈希"在电路成本上不是一个量级。

**（b）为什么只绑 $x$，$y$ 不必绑？**
协议把 nullifier **定义**为 $\mathrm{ExtractP}(\cdot)$，即只取 $x$；`Instance` 里也只有
`NF_OLD` 一行。但这不构成漏洞，因为点本身在电路里是被**完全约束**的：
$cm+[a]\mathcal K$ 由完整加法算出（不是独立见证 $y$），所以 $x$ 一旦定下，$y$ 的符号也定下，
证明者没有第二个自由度可选。对比一下三个公开绑定的对称性差异：

| 公开值 | 绑定行数 | 为什么 |
|---|---|---|
| `cv_net` | 2（x, y） | 链下要做同态金额平衡，需要完整点 |
| `rk` | 2（x, y） | 链下要用它验 SpendAuth 签名，需要完整点 |
| `nf_old` | **1**（x） | 只用于双花检测，协议定义就是 $x$ |

也就是说，"绑几行"由**链下消费者需要什么**决定，而不是由电路算出了什么决定。

---

## 5. 顺带修正 `04_cv_net.md` 的一条表述

`04` 里我写过"整份电路的最高次数来自 `RunningSumConfig`"。这句在**因果**上是过强的，
在**数值**上恰好成立。§6 的实测给出更准确的版本：

> 定基乘的范围检查确实是 9 次，但 9 次另有来源；`cs.degree()=9` 只能说明"存在达到 9 的门"。

`04_cv_net.md` 的 §1 第 2 条与结论表 F10 已按此改写。

---

## 6. 一个可复现的实测：逐门次数

### 6.1 为什么需要它

`cs.degree()` 是所有门的最大值，无法回答"哪个门是 9"。halo2 0.4 的 `Gate` / `polynomials()`
是 `pub(crate)`，拿不到。但 `dev::CircuitGates::collect::<F, C>()`（`dev/gates.rs` L103）
会**重跑一次 `Circuit::configure`**，把每个门的多项式渲染成字符串，且在运算符优先级会
产生歧义的地方**保留括号**（`dev/gates.rs` L140–L157）。这足以还原次数。

### 6.2 方法

```bash
# 1. 导出真实 CS 的全部门与约束（不需要 witness：collect 只调 configure）
cargo run --release --bin gate_degree > results/halo2/synthesize/gates_raw.txt

# 2. 逐门算次数（常量按 0 次、query/selector 按 1 次、乘加按 halo2 Expression 语义）
python scripts/analysis/gate_degree.py results/halo2/synthesize/gates_raw.txt
```

解析器的正确性用两个可手算的样本校验：`range check` 是 8 个一次因子 × selector ⇒ 9；
`alpha_1_range_check` 是 4 个一次因子 × selector ⇒ 5。两者都与手算一致。

产出：`results/halo2/synthesize/gates_raw.txt`（55 个门 / 193 条约束多项式）。

### 6.3 结果：4 条 9 次约束，3 个门

| 门 | 约束 | 次数 | 9 次从哪来 |
|---|---|---|---|
| `range check` | （无名，1 条） | **9** | 8 个一次因子 × `S18` |
| `Running sum coordinates check` | `check x` | **9** | $\big(\sum_{i=0}^{7}k^i F_{3+i}\big)\cdot S_{18}$，$k=z_i-8z_{i+1}$ 为 1 次 |
| `Full-width fixed-base scalar mul` | `check x` | **9** | $\big(\sum_{i=0}^{7}k^i F_{3+i}\big)\cdot S_{19}$，$k=$ `window` 为 1 次 |
| `Full-width fixed-base scalar mul` | `window range check` | **9** | 8 个一次因子 × `S19` |

原文（截断）：

```
range check:
  S18 * ((((((((A4@0 - (A4@1 * 0x8)) * (1 - (A4@0 - (A4@1 * 0x8)))) * (0x2 - ...
Full-width fixed-base scalar mul / window range check:
  S19 * (((((((A4@0 * (1 - A4@0)) * (0x2 - A4@0)) * (0x3 - A4@0)) * (0x4 - A4@0)) * ...
```

其余门的天花板（同为实测）：

| 门 | 最高次数 |
|---|---|
| `complete addition` | 6 |
| `Sinsemilla gate` | 6 |
| `full round` / `partial rounds` | 6 |
| `witness point` / `overflow checks` | 5 |
| `Canonicity checks` | 5 |
| `Short fixed-base mul gate` | 3 |
| `Orchard circuit checks`（`q_orchard`） | 3 |
| `Field element addition: c = a + b` | 2 |

### 6.4 三个结论

**F18｜9 次有两个"形状"，都源于 $H=8$。**

- 范围检查：8 个一次因子（$k,\ 1-k,\ \dots,\ 7-k$）乘 selector ⇒ $8+1=9$；
- 坐标多项式：$\sum_{i=0}^{H-1}k^iF_{3+i}$ 中 $k^7\cdot F$ 是 8 次，乘 selector ⇒ 9。

所以"9"不是某套机制的指纹，而是窗口宽度 8 的算术后果。

**F19｜即使删掉 `range check` 门，`cs.degree()` 仍是 9。**
`Full-width fixed-base scalar mul` 的 `check x` 独立达到 9。这比"不能唯一归因"更强：
9 这个数值对"是否使用 `RunningSumConfig`"**不敏感**。

**F20｜短标量路径的 9 次并不在自己的门里。**
`Short fixed-base mul gate` 最高只有 3 次（`last_window_check`、`sign_check`、`y_check`、
`negation_check`）；短标量的 3-bit 范围约束全部由外部 `range check` 门承担。
`q_mul_fixed_short` 门只负责"末窗口是 1 bit"（`bool_check(z_21)`，`short.rs` L49–L50）、
"$s\in\{\pm1\}$"（$s^2-1$）与条件取负（$s\cdot y_p-y_a$）。
`short.rs` L173–L175 的注释还顺手承认了借列："the last window is not a `u` value; we are
copying it into the `u` column because there is an available cell there"——与 `advices[9]`
借列给 `magnitude` 是同一手法。

---

## 7. 一个 Action 的定基乘清点

6 次长乘 + 1 次短乘：

| 计算 | 固定基 | 标量 | 窗口 | 调用点 |
|---|---|---|---|---|
| Spend authority | `SpendAuthG` | `alpha`（full） | 85 | `circuit.rs` L688–L695 |
| Value commitment (blind) | `ValueCommitR` | `rcv`（full） | 85 | `gadget.rs` L136–L143 |
| Value commitment (value) | `ValueCommitV` | `m·s`（short） | **22** | L130–L134 |
| Old note commitment | `NoteCommitR` | `rcm_old`（full） | 85 | `note_commit.rs` → `sinsemilla.rs` L425 |
| New note commitment | `NoteCommitR` | `rcm_new`（full） | 85 | 同上 |
| IVK commitment | `CommitIvkR` | `rivk`（full） | 85 | `commit_ivk.rs` → `sinsemilla.rs` L491/L512 |
| **Nullifier** | `NullifierK` | $h+\psi$（**base field**） | 85 | `gadget.rs` L195–L201 |

$$5\times85+85+22=\mathbf{532}\ \text{个 3-bit 窗口}$$

其中长乘 $6\times85=\mathbf{510}$，金额 22。

**边界（必须一起说）**：532 是**窗口数**，大致等于这些乘法区域的**行数**（每窗口一行），
但它**不等于**一个 Action 的总行数。未计入：variable-base 乘（$g_d,pk_d$ 各一次）、
Sinsemilla 的 255-bit 分解与 lookup、Poseidon（8 full + 56 partial）、两套 NoteCommit 的
message-piece gadget、每个定基乘末尾的**完整加法**、以及 base-field 那一段的 canonicity 3 行
+ 13×10-bit lookup 14 行。$K=11$（2048 行）装得下一个 Action，但这张表不能用来预测行数占比。

---

## 8. 待答问题

1. **variable-base 乘**（$g_d$、$pk_d_{,old/new}$）：`mul` 的窗口数、`overflow checks`（5 次）与
   "Decompose scalar for complete bits" 的实际行数——这是 `nf_old` 之外唯一还没清点的乘法族。
2. **Poseidon 的行数**：8 full + 56 partial，`full round` / `partial rounds` 门都是 6 次，
   但一个 permutation 占多少行仍未测。
3. **Sinsemilla 的 255-bit 分解**：`Sinsemilla gate` 的 `y check` 是 6 次，`bits` 列的
   lookup 与 `Decompose scalar for complete bits of variable-base mul` 的关系还没对上。
4. **区域级行数**：窗口数只能给出近似。要真做成本归因，需要在 floor planner 之后按
   region 统计行数（`Assignment` 层），而不是从窗口数外推。
5. `nf_old` 的 X 有两条去向：`constrain_instance`（L681）与 `rho_new`（L805）。后者意味着
   `nf_old` 必须活到新 note commitment 合成完，cell 不能被 floor planner 提前回收；
   后续做依赖图或做区域级行数统计时要把它算作"跨模块的长生命周期值"。

---

## 9. 本篇结论

| # | 结论 | 依据 |
|---|---|---|
| F15 | `nf_old` 的最终加法用**完整加法**（12 条约束、最高 6 次），不是不完整加法 | `ecc.rs` L345–L359、实测 `complete addition` |
| F16 | $\psi$ 是 `PRF^expand` 于 `rseed`+`rho` 的结果，电路里是纯 witness，只被 `AddChip` 消费一次 | `note.rs` L147–L149、L381–L383；`gadget.rs` L184–L190 |
| F17 | `NullifierK` 走 `BaseFieldElem`：85 窗口 + 额外规范性；窗口数与 `FullScalar` 相同 | `base_field_elem.rs` L179–L188；`fixed_bases.rs` L56 |
| F18 | 9 次有两个形状（8 因子 × selector、$k^7F\cdot$ selector），都源于 $H=8$ | 实测 `gates_raw.txt` |
| F19 | 删掉 `range check` 门后 `cs.degree()` 仍为 9（`check x` 独立达到 9） | 同上 |
| F20 | `Short fixed-base mul gate` 最高 3 次；短标量的范围约束全在外部 `range check` 门 | `short.rs` L39–L74；实测 |
| F21 | base-field 规范性由 8 条门约束 + **1 条** 13×10-bit lookup 组成；$\alpha_0<2^{130}$ 由位约束而非 lookup 完成 | `base_field_elem.rs` L62–L160、L255–L275 |
| F22 | $\alpha_0$ 是门内推导值（$\alpha-z_{84}2^{252}$），不是见证值 | `base_field_elem.rs` L83–L87、L318–L339 |
| F23 | 一个 Action 有 $6\times85+22=532$ 个定基乘窗口；该数不等于总行数 | 上表 7 行调用点 |
| F24 | $\rho^{\text{new}}=nf^{\text{old}}$ 在电路内部完成（L805），`nf_old` 不是叶子输出 | `circuit.rs` L805、L818–L830 |
