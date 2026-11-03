# 08 · Orchard Action 级电路成本地图（实测）

> 范围：单个 Orchard Action（`synthesize_base` 一次调用），版本 `PostNu6_3`，
> `floor_planner::V1` 两趟布局。所有数字来自 `results/halo2/synthesize/layout_v3.txt`
> （`layout_rows` 记录器实测）与 `gate_table.txt`（`dev::CircuitGates::collect` 解析），
> 非估算。
>
> 上游固定 `D:\Research\orchard-upstream` commit `616a669d`。
> 实验仓库 `D:\Research\zcash-orchard-engineering`。
> 解析脚本：`experiments/transaction_e2e/aggregate_cost.py`（可随 V2/V3 重跑复算）。

---

## 0. 一句话结论（先看这个）

把 Action 拆成模块后，**真正吃布局行数的是 Merkle 路径（~58%）和 NoteCommit（~22%）**，
而你之前逐模块啃的四块（cv_net / nf_old / 变基乘 / old+new NoteCommit）合起来只占约 **34%**。

| 优化目标 | 占比（约束行） | 备注 |
| --- | --- | --- |
| Merkle 认证路径 | **58.0%** | 32 层 × Sinsemilla，单模块最大 |
| NoteCommit（old+new 合计） | **22.2%** | 两侧对称，各 ~399 行 |
| CommitIvk（ivk） | 5.2% | 含 1 个 85-窗口定基乘 |
| 地址派生（变基乘） | 4.3% | 137 核心 + 18 overflow |
| Nullifier（nf_old） | 4.1% | 含 1 个 86 行 base-field-elem 乘 |
| Value commit（cv_net） | 3.2% | 短乘 23 + 全宽 85 窗口 |
| SpendAuthG（花费授权） | 2.4% | 1 个 85-窗口定基乘 |
| 其余（witness/checks/rk/xaddr） | <1% | — |

> 分母 = **约束行 3589**（布局总行 4613 减去 Generator table 1024 行固定预计算）。
> Generator table 是所有固定基的窗口预计算表，属一次性离线工作，不计入每证明的约束行成本。

---

## 1. 模块级成本地图

`布局行` = `rows_used` 求和（不含 0 行 copy/instance 绑定）；`占比` = 布局行 / 3589。

| 模块 | 布局行 | 区域数 | 主导 Selector | 公开输出（Instance） | 私有绑定（copy） | 备注 |
| --- | ---: | ---: | --- | --- | --- | --- |
| **MERKLE** 认证路径 | 2080 | 256 | `S25×832` `S29×832` `S2×128` `S4×64` | `anchor`（Merkle 根）→ `Instance[0]` | — | 32 层，奇偶层交替用 chip_1/chip_2（A0–A4 / A5–A9）；含每层 Sinsemilla + `Check piece decomposition` + `b` 的 5-bit 范围检查 |
| **NC** NoteCommit(old+new) | 798 | 86 | `S19×340` `S2×304` `S3×260` `S7×166` | **new** → `CMX`（`Instance[CMX]`） | **old** → `cm_old`（`constrain_equal`，不公开） | 两侧对称，详见 §3；`rho_new = nf_old` 复用见 §4 |
| **CIVK** CommitIvk | 185 | 14 | `S19×170` `S7×83` `S25×51` `S2×33` `S3×27` | —（ivk 内部） | `ivk` cell → 喂给 ADDR | 含 `ak‖nk` Sinsemilla + `[r]R` 全宽 85-窗口定基乘 + 各类 canonicity |
| **ADDR** 地址派生（变基乘） | 155 | 4 | `S13×125` `S10×124` `S8×8` `S2×13` `S3×13` | — | `pk_d_old = [ivk]g_d_old`（= 见证 `pk_d_old`） | 137 核心 D&A + 18 overflow（详见 `06`） |
| **NF** Nullifier(nf_old) | 149 | 9 | `S18×170` `S7×83` `S23×28` `S2×13` `S3×13` | `nf_old.x` → `Instance[NF_OLD]` | `rho_new = nf_old` 复用进新 NoteCommit | 86 行 base-field-elem 乘（NullifierK，`S18`）+ 37 行 Poseidon permute |
| **CV** Value commit(cv_net) | 114 | 5 | `S19×170` `S7×103` `S18×44` `S8×3` | `cv_net.{x,y}` → `Instance` | `v_net` 内部 magnitude/sign 复制 | `[v]ValueCommitV` 短乘 23 行（`S18`）+ `[rcv]ValueCommitR` 全宽 85 窗口 |
| **SA** SpendAuthG | 87 | 2 | `S19×170` `S7×83` `S8×1` | `rk.{x,y}` → `Instance`（由 `rk` 计算派生） | — | 1 个 85-窗口定基乘 |
| **WIT** 见证加载 | 14 | 14 | `S6×5` `S5×1` | — | `g_d_new/pk_d_new` 等非身份见证点 | 各见证点 1 行 |
| **XADDR** 跨地址检查(NU6.3) | 4 | 1 | `S0×4` | — | 复用 gate #2 填 4 行防 V1 重叠 | 仅 `PostNu6_3` 启用 |
| **RK** | 2 | 1 | `S8×1` | （见 SA） | — | `rk` 完整点加 |
| **CHK** Orchard circuit checks | 1 | 1 | `S0×1` | — | — | 全局开关 |
| **GEN** Generator table | 1024 | 1 | —（Fixed 列） | — | — | 所有固定基窗口预计算表；**不计入每证明约束行** |

`约束行合计`（不含 GEN）= 2080+798+185+155+149+114+87+14+4+2+1 = **3589**。

---

## 2. 固定基 85-窗口定基乘的"隐藏大头"

Action 里 **6 次**固定基标量乘，占了布局行的相当比例。它们都用同一套
"window gate（每窗口 2 行、degree 9）+ 每窗口 incomplete addition" 结构：

| 乘法 | 类型 | 窗口门 | 窗口区行数 | 末窗口完整加（S8） |
| --- | --- | --- | ---: | --- |
| `[rcv]ValueCommitR`（cv_net） | 全宽 85 窗口 | `S19×170` | 85 | +2 行 |
| `[r]R`（CommitIvk） | 全宽 85 窗口 | `S19×170` | 85 | +2 行 |
| `[α]SpendAuthG`（SA） | 全宽 85 窗口 | `S19×170` | 85 | +1 行 |
| `[r]R`（NoteCommitR old） | 全宽 85 窗口 | `S19×170` | 85 | +2 行 |
| `[r]R`（NoteCommitR new） | 全宽 85 窗口 | `S19×170` | 85 | +2 行 |
| `[NullifierK]`（nf_old） | **base-field-elem 85 窗口** | `S18×170` | **86** | +2 行 |
| `[v]ValueCommitV`（cv_net） | **短乘 22 窗口** | `S18×44` | 23 | +2 行 |

- 全宽 85-窗口：`S19` 总量 = 850 = 5 次 × 170 ✔（交叉验证无误）。
- `S18` 总量 = 214 = 170（NullifierK）+ 44（ValueCommitV 短乘）✔。
- **洞察**：5 次全宽 85-窗口定基乘的窗口区合计 425 行（占总约束行 11.8%），
  加上各自的末窗口完整加与 incomplete addition（`S7` 每乘 83 行），
  是仅次于 Merkle 与 NoteCommit 的"第三大"成本簇。任何针对固定基乘的
  窗口/lookup 优化，收益面非常广（一次性覆盖 5 处）。

---

## 3. old / new NoteCommit 对照表

两份 NoteCommit 在源码里传**同一个 namespace 字符串**，故 `layout_rows` 不能直接按名区分；
归属依据是 `gate_table.txt` 中 `NoteCommitChip` 出现两次（old = `S34–S44`，new = `S45–S55`）
以及共享 `S19` 的 R-mul 按程序顺序相邻排布（old 503–587 → new 588–672）。

| 项 | old NoteCommit | new NoteCommit | 是否相同 |
| --- | ---: | ---: | --- |
| R-mul（`[r]R` NoteCommitR，全宽 85 窗口） | 85 行（`S19×170`+`S7×83`） | 85 行（同） | **逐字节相同** |
| Sinsemilla 哈希（消息 1090 bit = 109×10-bit） | 110 行（`S25×109`+`S26`，A0–A4） | 110 行（`S29×109`+`S30`，A5–A9） | 结构同，**列组不同**（chip_1 vs chip_2） |
| 输入 canonicity（g_d/pk_d/rho/psi 分解，`S2`/`S3`） | 对称 | 对称 | 对称 1:1 |
| MessagePiece 门（b/d/g/e/h） | `S34–S38` | `S45–S49` | 同结构不同 selector |
| 输入赋值门（g_d/pk_d/value/rho/psi） | `S39–S43` | `S50–S54` | 同结构不同 selector |
| y-canonicity | `S44×2` | `S55×2` | 同结构不同 selector |
| **输出绑定** | `cm_old`（`constrain_equal`，**私有**） | `CMX`（`constrain_instance`，**公开**） | **不对称** |
| **rho 来源** | `rho_old`（独立见证） | `rho_new = nf_old`（**cell 复用**，见 §4） | 不对称 |

- 合计：old ≈ new ≈ **399 行**（combined 798）。
- **关键纠正（呼应 `07`）**：`NoteCommitChip` 门组是 `S34–S44`（old）与 `S45–S55`（new），
  各 11 个门（5 MessagePiece + 5 输入 + 1 y-canonicity）；`07` 初稿写成 `S34–S43`/`S45–S54`
  漏了 `S44`/`S55`（y-canonicity），此处以布局实测为准收紧。
- **隐藏成本洞察**：NoteCommit 的 798 行里，R-mul(170) + Sinsemilla(220) = 390 行只是显式部分；
  其余 ~408 行是**逐 note 的 (g_d, pk_d, rho, psi) canonicity 分解**——这部分几乎与
  "R-mul + 哈希" 同级，是优化时容易忽略的暗成本。

---

## 4. 跨模块长生命周期 cell（单独成列）

这些 cell 在一个 Action 内被多个模块共享，是"公开/私有绑定"的本质来源：

| Cell | 产生模块 | 消费模块 | 绑定类型 | 成本 |
| --- | --- | --- | --- | --- |
| `rho_new` | **= `nf_old`**（克隆 `nf_old.inner()`） | new NoteCommit 的 `rho` 输入 | **copy constraint**（复用，零新分配） | 0 行 + 1 跨模块 copy |
| `cm_old` | old NoteCommit 输出 | 私有绑定（不公开） | copy（`constrain_equal`） | 0 行 + 2 copy（行 347） |
| `CMX` | new NoteCommit 输出 | 公开输入 | `constrain_instance` | 0 行 + 1 instance |
| `cv_net.{x,y}` | cv_net | 公开输入 | `constrain_instance` | 0 行 |
| `nf_old.x` | nf_old | 公开输入（被花掉的旧 note） | `constrain_instance` | 0 行 |
| `rk.{x,y}` | SA（`rk = [α]G + …`） | 公开输入 | `constrain_instance` | 0 行 |
| `anchor` | MERKLE（根） | 公开输入 | `constrain_instance` | 0 行 |
| `ivk` | CIVK | ADDR（地址派生） | 内部 cell（非公开） | 0 行 |
| `pk_d_old` | 见证点 = `[ivk]g_d_old`（ADDR 输出） | 消费方验证 | copy（`constrain_equal`） | 0 行 |

> **核心不对称**：old NoteCommit 的输出 `cm_old` 只做**私有** copy（链下验证者用，不进公开输入）；
> new NoteCommit 的输出 `CMX` 进**公开** instance（链上可见）。这正是 Orchard "花掉旧、创建新"
> 语义在电路里的体现——旧承诺需可被证明存在但不必公开，新承诺必须公开以便后续花费。
> `rho_new = nf_old` 的复用意味着：**new note 的随机性来源直接绑定到刚算出的 nullifier 的 cell**，
> 不产生新行，只新增 1 条跨模块 copy constraint。

---

## 5. 证据边界 / 方法注记

- **行数 ≠ 证明成本**：本表是 `floor_planner::V1` 的**布局行数**地图，反映的是区域占用的
  advice 行与 selector 激活次数，不是 MSM/FFT 级别的证明时间。V1 会把小区域重排填充，
  因此 `COPIES 2822`、`REGIONS 396` 等都是布局结果，不能直接换算成 prover 时钟周期。
- **old/new 拆分依据**：两份 NoteCommit 共用 namespace，拆分靠 `gate_table.txt` 的
  `NoteCommitChip` 双实例 + 共享 `S19` 的相邻行区间，非名字。若上游改 namespace 或调整
  R-mul 顺序，需重新核对 `layout_v3.txt` 行区间。
- **canonicity 对称性**：§3 的 canonicity 行数按"逐 note 对称 1:1"归因；布局里 old/new 的
  分解区域交错排布、且共用 `S2`/`S3`（不区分列组），未逐区域映射，故只给 combined 与对称结论。
- **Generator table**：1024 行固定基窗口表为离线预计算，未计入占比分母；若按"总布局行"口径，
  各项占比需整体下调 ~22%（被 GEN 稀释）。
- **复算**：`aggregate_cost.py` 直接解析 `layout_v3.txt`，重跑即得；模块映射规则在脚本
  `RULES` 中集中维护，新增模块改一处即可。

---

## 6. 与既有文档的衔接

| 文档 | 覆盖模块 | 本图对应 |
| --- | --- | --- |
| `04_cv_net.md` | Value commit | §1 CV 行（114）、§2 `[v]V` 短乘 + `[rcv]R` 全宽 |
| `05_nf_old.md` | Nullifier | §1 NF 行（149）、§2 NullifierK 86 行 |
| `06_variable_base_mul.md` | 地址派生 | §1 ADDR 行（155 = 137+18） |
| `07_note_commit_new.md` | new NoteCommit | §3 对照表 + §4 `rho_new=nf_old` |
| **本图 `08`** | Action 汇总 | 把上述四块 + Merkle/CIVK/SA 拼成总图 |
