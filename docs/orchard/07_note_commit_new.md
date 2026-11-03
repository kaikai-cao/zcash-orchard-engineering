# 07 · New Note Commitment：`cm_new` 与 `rho_new = nf_old`

上游固定版本 `616a669d`；`halo2_gadgets 0.6.0` + `halo2_proofs 0.4.0`（按 Orchard
`Cargo.lock`）。本篇接在 `05_nf_old.md`（定基乘 / `NullifierK`）与 `06_variable_base_mul.md`
（变基乘）之后，把 **new note commitment** 从"窗口数"推进到"真实行布局 + Selector + 约束数"。

它要回答的核心问题是：`05` 里清点出的 `NoteCommitR` 85 窗口，究竟在这份电路里占多少行、
由哪些 Selector 承担；以及 old / new 两套 NoteCommit 在成本结构上是否真的一致。

---

## 0. 调用链与两条边界

`circuit.rs` L802–840 的数据流（与 `06` §1 的调用链是同一个 Action 的下游）：

```text
g_d_new, pk_d_new  ← NonIdentityPoint::new（外部见证点）
v_new              ← NoteValue 见证（L533 解构得到）
rho_new = nf_old.inner().clone()   ← 复用 nf_old 的 cell（L805）
psi_new            ← assign_free_advice(advices[0])（L808）
rcm_new            ← ScalarFixed::new（L814，延迟到实际乘法才分配电路）
        │
        ▼  gadget::note_commit( sinsemilla_chip_2, ecc_chip, note_commit_chip_new,
        │                    g_d_new, pk_d_new, v_new, rho_new, psi_new, rcm_new )
        ▼
cm_new = M + [rcm_new]·R          ← CommitDomain::commit
cmx   = cm_new.extract_p()        ← 取 x 坐标
constrain_instance(cmx, primary, CMX)   ← 绑公开输入 CMX（L839）
```

**边界一｜new note commitment 不是第二次地址变基乘。（关键，勿误读）**
`g_d_new` / `pk_d_new` 是 `NonIdentityPoint::new`（L782–800）产出的**纯见证点**，
只做 on-curve / 非恒等检查，**不再执行一次 `[ivk]g_d`**。整个 Action 里唯一的变基乘是
`06` 讲的 `[ivk]g_d_old`（137 行）。把 new note commitment 误当成"第二次地址派生"会凭空
多出一份 137 行的成本，也违背协议（new note 的地址本来就是发送方直接给出的）。

**边界二｜`rho_new` 是 `nf_old` 的 cell 复用，不是一次新 Nullifier 计算。（关键，勿误读）**
`rho_new = nf_old.inner().clone()`（L805）只是把 `derive_nullifier` 已经算好的那个
nullifier cell **按引用复用**（clone 不分配新 cell、不加门）。后面 §5 / §6 会追这个复用
产生了哪些 copy constraint。它**不是** `05` 的 nullifier 链路重跑一遍。

---

## 1. 输入见证如何进入电路

| 输入 | 进入方式 | 代码 | 是否新增电路 |
|---|---|---|---|
| `g_d_new` | `NonIdentityPoint::new` 见证（on-curve + 非恒等） | `circuit.rs` L782–789 | 否（1 行：`witness g_d_new_star`，`S6×1`，行 1766） |
| `pk_d_new` | 同上 | L793–800 | 否（1 行：`witness pk_d_new`，`S6×1`，行 1765） |
| `v_new` | `NoteValue` 见证 cell（L533 解构，`assign_free_advice` 类） | L533 / L546 | 否 |
| `rho_new` | `nf_old.inner().clone()`——**直接复用 nullifier cell** | L805 | 否（0 行；仅后续 copy） |
| `psi_new` | `assign_free_advice(config.advices[0], self.psi_new)` | L808–812 | 否 |
| `rcm_new` | `ScalarFixed::new(ecc_chip, …)`——**延迟** | L814–818 | 是，但推迟到 `domain.commit` 才发生（§4） |

要点：`rcm_new` 在 `ScalarFixed::new` 阶段**不分配任何乘法电路**（`ScalarFixed` 只是把
标量包成一个待乘的见证）；真正的 85 窗口定基乘在 `gadget::note_commit` 内部的
`CommitDomain::commit` 里才展开。`05` 末尾把这个延迟性质记进了 `NoteCommitR` 的清点，
本篇 §4 把它落到真实行数。

---

## 2. Canonicity：8 个 MessagePiece 与 7 类门

`gadget::note_commit`（`note_commit.rs` L1594–1797）先把消息切成 8 个 `MessagePiece`，
再做 5 个字段的 canonicity + 2 个 y 坐标 canonicity。

### 2.1 消息位串（共 1090 bit = 109 个 10-bit 字）

| Piece | 内容 | 位数 |
|---|---|---|
| `a` | `x(g_d)` 的 bit 0..250 | 250 |
| `b` | `b_0`(x g_d 250..253, 4) ‖ `b_1`(x g_d 254, 1) ‖ `b_2`(g_d 的 ỹ, 1) ‖ `b_3`(x pk_d 0..3, 4) | 10 |
| `c` | `x(pk_d)` 的 bit 4..254 | 250 |
| `d` | `d_0`(x pk_d 254, 1) ‖ `d_1`(pk_d 的 ỹ, 1) ‖ `d_2`(v 0..7, 8) ‖ `d_3`(v 8..57, 50) | 60 |
| `e` | `e_0`(v 58..63, 6) ‖ `e_1`(rho 0..3, 4) | 10 |
| `f` | `rho` 的 bit 4..254 | 250 |
| `g` | `g_0`(rho 254, 1) ‖ `g_1`(psi 0..8, 9) ‖ `g_2`(psi 9..248, 240) | 250 |
| `h` | `h_0`(psi 249..253, 5) ‖ `h_1`(psi 254, 1) ‖ `h_2`(4 个零 padding) | 10 |

`g_d` 部分 = 256（x 255 + ỹ 1）、`pk_d` 部分 = 256、`v` = 64、`rho` = 255、`psi` = 255，
再加 `h_2` 的 4 个零 padding，合计 **1090 bit = 109 个 10-bit 字**。这与 §3 的
`S25×109` 完全吻合——不存在"估算"，是布局实测出来的字长。

### 2.2 门清单（old = `S34–S43` / new = `S45–S54`，来自 `gate_table.txt`）

`NoteCommitChip::configure` 被调用**两次**（`circuit.rs` L479–485）：先 `old_note_commit_config`
拿 `S34–S43`，后 `new_note_commit_config` 拿 `S45–S54`。两套实例结构**逐字节相同**，差别只在
调用哪一组 selector 与哪一套 Sinsemilla 实例（§3）。

| 门 | 约束数 | 次数 | selector（old / new） | 作用 |
|---|---|---|---|---|
| `NoteCommit MessagePiece b` | 3 | 3 | S34 / S45 | `b` 的 bool + 分解 |
| `NoteCommit MessagePiece d` | 3 | 3 | S35 / S46 | `d` 的 bool + 分解 |
| `NoteCommit MessagePiece e` | 1 | 2 | S36 / S47 | `e` 的分解 |
| `NoteCommit MessagePiece g` | 2 | 3 | S37 / S48 | `g_0` bool + 分解 |
| `NoteCommit MessagePiece h` | 2 | 3 | S38 / S49 | `h_1` bool + 分解 |
| `NoteCommit input g_d` | 5 | 3 | S39 / S50 | x(g_d) 分解 + canonicity |
| `NoteCommit input pk_d` | 4 | 3 | S40 / S51 | x(pk_d) 分解 + canonicity |
| `NoteCommit input value` | 1 | 2 | S41 / S52 | value 分解 |
| `NoteCommit input rho` | 4 | 3 | S42 / S53 | rho 分解 + canonicity |
| `NoteCommit input psi` | 5 | 3 | S43 / S54 | psi 分解 + canonicity |
| `y coordinate checks` | 7 | 3 | S44 / S55 | y(g_d)、y(pk_d) 各一次 |

> 注意 `y coordinate checks`（源里 `q_y_canon`）被 `y(g_d)`、`y(pk_d)` 各调用一次，所以
> 每个 NoteCommit 有 **2 个**该门 region（每个 2 行）。其余 input 门每个 2 行、`e`/`value`
> 各 1 行、`b/d/g/h` 各 2 行（分解门本身）。这些 region 在 `layout_v3.txt` 里都出现**两次**
> （old / new），selector 编号直接区分两侧。

---

## 3. Sinsemilla：一次承诺、109 字、侧旁布局

`CommitDomain::commit` 把 §2.1 的 8 个 piece 作为一个 `Message` 做**一次**
`NoteCommit^Orchard` 承诺，内部是 109 个 10-bit 字的处理。

| 项 | 实测（old / new） | 依据 |
|---|---|---|
| 哈希 region 行数 | **110 行**（109 字 + 1 收尾） | `layout_v3.txt` L479–480 |
| 哈希门 | `Sinsemilla gate`：`S25×109` + `S26×1`（old）；`S29×109` + `S30×1`（new） | L13–14 |
| 使用的 advice 列 | chip_1（old）→ `A0|A1|A2|A3|A4`；chip_2（new）→ `A5|A6|A7|A8|A9` | L13–14 的列集合 |
| 使用的 fixed 列 | chip_1 → `F12`、`F3`；chip_2 → `F13`、`F4` | L13–14 的列集合 |
| 布局形态 | **侧旁（side-by-side）**：两份哈希落在同一行区间 `137–246`，但占用不相交的列组 | L13–14 同行不同列 |

**为什么能侧旁**：`circuit.rs` L456–457 的注释写明——"Since the Sinsemilla config uses
only 5 advice columns, we can fit two instances side-by-side"。old 用 `sinsemilla_chip_1`
（接 `A0–A4`），new 用 `sinsemilla_chip_2`（接 `A5–A9`），于是两笔记在同 110 行内并排完成，
互不冲突。**这是 old / new NoteCommit 在 Sinsemilla 维度上"同一结构、分占列"的直接证据。**

**查找表**：两侧引用同一张协议固定的 Sinsemilla generator 查找表，但分别承载在独立 fixed 列
（chip_1：`F3`/`F12`；chip_2：`F4`/`F13`）。`F3`/`F4` 是承载该表的两个 fixed 列实例；
`F12`/`F13` 是两实例各自的 `z` 列。每字一次 Q 表 lookup，每 NoteCommit **≈ 109 次**哈希 lookup。

---

## 4. 固定基乘法：`rcm_new` 的 85 窗口 → 89 行（实测，非估算）

`rcm_new` 走 `NoteCommitR` 的**全宽定基乘**（`Full-width fixed-base scalar mul`，`S19`，次数 9）。
这是 `05` 末尾清点表里的 "New note commitment / `NoteCommitR` / `rcm_new`（full）/ 85 窗口"
那一项的真实落地。

| 子 region | 行数 | selector | 行区间（按程序顺序推断 old 在前） |
|---|---|---|---|
| 不完整加法（85 窗口） | **85** | `S19×170`（`S7×83` 是 running sum 列门） | old `503–587` / new `588–672` |
| 末窗口完整加法 | 2 | `S8×1` | old `1687–1688` / new `1695–1696` |
| 最终 `M + [r]R` 完整加法 | 2 | `S8×1` | old `1705–1706` / new `1707–1708` |
| **合计** | **89** | — | 每侧 89 行 |

**窗口与行的对应（呼应 `05` 的边界）**：`S19×170` 落在 **85 行**上，即窗口门在每行启用 **2 次**
（同一窗口门的两次激活，细节在 `full_width.rs`，本篇不展开），而**不是**"2 个 3-bit 窗口压成 1 行"
——85 个窗口确实对应 85 行（1 窗口/行）。这与 `05` 末尾"`532` 窗口 ≈ 乘法区域行数、每窗口一行"
的近似一致，且在 `NoteCommitR` 这里是**精确**的（相邻的 `NullifierK` 85 窗口是 86 行，多 1 行是
running-sum 边界 `z_85 = 0`）。真正可凭的结论是布局实测的 **85 行不完整 + 2 行末窗口 + 2 行最终 =
89 行/侧**，而不是从窗口数外推。

**与 `05` 的对照（同一个 `NoteCommitR`，但注意 `NullifierK` 不同）**：

| | `NullifierK`（`05`） | `NoteCommitR`（本篇，old/new 同） |
|---|---|---|
| 基点 | `FixedPointBaseField<NullifierK>`（常数） | `NoteCommitR`（常数） |
| 标量 | Pallas **Base**（`α`，需额外证 `α<p`） | Pallas **Scalar**（`rcm`，允许非规范） |
| 门 | `Base-field elem fixed-base mul`（`S18`，86 行 z-running-sum） | `Full-width fixed-base mul`（`S19`，85 行） |
| 最高次数 | 9（`range check` × `S18`） | 9（`check x` 独立达到 9） |
| 窗口数 | 85 | 85 |
| 额外检查 | `Canonicity checks`（`S21`，证 `α<p`） | 无（rcm 是 Scalar，直接非规范累加无害） |

两者**窗口数都是 85、最高次数都是 9**，但门不同（`S18` vs `S19`）、输入域不同（Base vs
Scalar）、是否要 canonicity 不同。这正是 `05` 强调的："差别不在窗口数，而在输入域与规范性"。

---

## 5. 输出绑定：`cm_new → extract_p → CMX`

`circuit.rs` L836–839：

```rust
let cmx = cm_new.extract_p();                              // 取 x 坐标（ExtractedNoteCommitment）
layouter.constrain_instance(cmx.inner().cell(), config.primary, CMX)?;  // 绑公开输入 CMX
```

- `cm_new` 是 `CommitDomain::commit` 返回的 `Point`（`M + [rcm_new]·R`）。
- `extract_p()` 取该点的 x 坐标，作为 note commitment 的提取承诺 `cmx`。
- `constrain_instance` 把 `cmx` 绑到公开输入 `CMX`——这是 new note commitment 对**公开**
  输入的唯一贡献。

**old 侧的对照**：old 不绑公开输入，而是 `derived_cm_old.constrain_equal(&cm_old)`
（`circuit.rs` L777），把派生承诺 copy 到见证的 `cm_old` cell（2 条 copy、region
`cm_old equality` 在 `layout_v3.txt` L12，行 347）。即 old 的 `cm_old` 是**私有**见证，new 的
`cmx` 是**公开**输入——这正是 Action 里"花掉旧 note、创建新 note"在公开性上的不对称。

---

## 6. old / new 对照表（成本结构是否真的一致）

| 维度 | old NoteCommit | new NoteCommit | 是否相同 |
|---|---|---|---|
| 约束结构（门集合） | 同一 `NoteCommitChip` 实例 A（`S34–S43`, `S44` y） | 同一 `NoteCommitChip` 实例 B（`S45–S54`, `S55` y） | **是**（逐字节同结构，仅 selector 编号不同） |
| Sinsemilla advice 列 | `sinsemilla_chip_1` → `A0–A4` | `sinsemilla_chip_2` → `A5–A9` | 否（分占列，侧旁布局） |
| Sinsemilla 查找表 / Fixed 列 | 同一 Q 表；`F3`/`F12` | 同一 Q 表；`F4`/`F13` | 表同，列实例不同 |
| ECC 固定列（`NoteCommitR`） | `ecc_chip` 共享：`F10/F11/F5/F6/F7/F8/F9` | 同上（同一 `ecc_chip`） | **是**（全宽定基乘复用同一组 ECC 固定列） |
| `rcm` 的 85-window 实际行数 | 89 行（85 不完整 + 2 末窗 + 2 最终） | 89 行（同上） | **是**（布局里两份，L485–486） |
| 哈希 `hash_to_point` 行数 | 110 行（`S25/S26`） | 110 行（`S29/S30`） | **是**（侧旁于同 110 行） |
| 输出绑定 | `constrain_equal(cm_old)`：2 copy、私有 | `constrain_instance(CMX)`：公开 | 否（公开性不对称） |
| `rho` 来源与 copy | `rho_old` = 独立 witness | `rho_new = nf_old`：复用 nullifier cell | **否**（见下） |

### `rho_new = nf_old` 的 cell 复用产生了哪些 copy constraint

`rho_new` 是 `nf_old.inner().clone()`——同一个 `AssignedCell`，**零新分配**。当 new NoteCommit
消费它时，halo2 在"异行引用"处自动产生 copy：`RhoCanonicity::assign`（`note_commit.rs`
L1110–1129）里 `rho.copy_advice(|| "rho", …, col_l, 0)` 把 nf_old 这个 cell 拷进 new
NoteCommit 的 canonicity region（region 名 `NoteCommit input rho`，`layout_v3.txt` L176/L179，
每行 1 次 copy）；此外 Sinsemilla 消息的 `e_1`（rho 低 4 bit）与 `f`（rho bit 4..254）处理
也会把该 cell 拷入哈希 region。

因此 **nf_old 的 cell 在 nullifier 派生之外，额外获得了进入 new NoteCommit 的 copy 约束**；
old 侧的 `rho_old` 是独立 witness（其 `NoteCommit input rho` 的 copy 来源是 `rho_old` 自身，
不引用 nf_old）。换句话说，"复用"省掉的是**一次 rho 的见证与一次 Nullifier 重算**，代价是把
nf_old 这个长生命周期 cell 又多绑了几条 copy——它正是 `05` 末尾说的"跨模块长生命周期值"，在
Action 级成本图里要单独记账。

> **方法说明（证据边界）**：两份 NoteCommit 调用在源码里传的是**同一个 namespace 字符串**
> `"g★_d || pk★_d || …"`（`circuit.rs` L763 与 L823 完全相同），所以 `layout_rows` 记录器
> 产出的 region 名无法靠字符串区分 old / new。本篇的拆分依据两条独立证据：① `gate_table.txt`
> 里 `NoteCommitChip` 的门出现两次（`S34–S43` 与 `S45–S54`），对应两次
> `NoteCommitChip::configure`（`circuit.rs` L479 / L484），据此把 selector 编号映射到 old / new；
> ② 共享的 `S19` R-mul 不区分 selector，按程序顺序（old 先于 new）与相邻排布（503–587 →
> 588–672）推断归属。布局文件里所有"翻倍"的 region 即 = 两侧之和，单侧 = 文件值 ÷ 2。

---

## 7. 结论清单（F 编号接 `06`）

| # | 结论 | 依据 |
|---|---|---|
| F37 | new note commitment **不是**第二次地址变基乘；`g_d_new`/`pk_d_new` 是纯见证点 | `circuit.rs` L782–800 |
| F38 | `rho_new = nf_old.inner().clone()` 是 cell 复用，不是重算 Nullifier | `circuit.rs` L805 |
| F39 | new note 消息 = 1090 bit = 109 个 10-bit 字，哈希 `S25×109`（old）/ `S29×109`（new） | `layout_v3.txt` L13–14，与位串手算一致 |
| F40 | 两套 Sinsemilla 侧旁布局：chip_1 `A0–A4`/`F3,F12`，chip_2 `A5–A9`/`F4,F13` | `circuit.rs` L458–485；`layout_v3.txt` L13–14 |
| F41 | `rcm_new` 的 85 窗口 → **89 行**（85 不完整 `S19×170` + 2 末窗 `S8` + 2 最终 `S8`），old/new 同 | `layout_v3.txt` L165/L175/L303/L310/L318/L320 |
| F42 | old/new NoteCommit 约束结构逐字节相同（同 `NoteCommitChip`，selector `S34–S43` / `S45–S54`）；差异仅在 Sinsemilla 列组、输出绑定、rho 来源 | `gate_table.txt`；`layout_v3.txt` |
| F43 | new 输出绑 `CMX`（公开），old 绑 `cm_old`（私有 `constrain_equal`，2 copy，行 347） | `circuit.rs` L777 / L839 |
| F44 | `rho_new=nf_old` 给 nf_old cell 新增进入 new NoteCommit 的 copy 约束（canonicity + 消息），old 的 `rho_old` 不引用 nf_old | `note_commit.rs` L1110–1129；`layout_v3.txt` L176/L179 |

---

## 8. 与 `05` / `06` 的连接，以及下一步

- 与 `05`：`NoteCommitR`（本篇）和 `NullifierK`（`05`）同为 85 窗口、同为 9 次，但门不同
  （`S19` Full-width vs `S18` Base-field-elem）、输入域不同（Scalar vs Base）。`05` 末尾的
  "532 窗口"清点里，"Old/New note commitment" 各占一份 `NoteCommitR` 的 85 窗口——本篇把其中
  **new** 那份从"窗口数"做实成"89 行"。
- 与 `06`：`06` 的 137 行变基乘是 Action 里唯一的 `[ivk]g_d`；本篇确认 new note commitment
  **不**复用它（边界一），两者在成本图上属于不同模块。
- 与 `06` Fix #1：`06` 已限定 `ScalarVar::from_base` 零成本只覆盖 `ivk→pk_d_old`，`CommitIvk`
  的 Sinsemilla + `rivk` 定基乘需单独记账。本篇的 new note commitment 也有自己的 Sinsemilla
  哈希（`S25/S29`）与 `rcm_new` 定基乘（`S19`），同样是**独立于 `CommitIvk`** 的一笔成本，不能
  因为共用 Sinsemilla 实例就混为一谈。

**下一步（待 `07` 冻结后）**：把 `cv_net`（04）、`nf_old`（05）、变基乘（06）、old/new NoteCommit
（本篇）汇总成一张 **Action 级电路成本地图**——按"模块 × 行数 × Selector 次数 × 公开/私有绑定"
而不是孤立地解释某个 Gadget。届时 `rho_new=nf_old`、`cm_old`/`cmx` 这类跨模块长生命周期 cell
要单独成列。
