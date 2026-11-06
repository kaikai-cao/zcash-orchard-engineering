# 29. 真实 Zcash PCZT 环境性能基线与 F4-A 外部有效性边界验证（预注册）

**状态**：**预注册，测量前冻结**（2026-11-06）。**本文件提交时，测量一轮都没有跑**；
服务未启动、快照未恢复、没有发送任何交易、没有构建或部署任何 F4-A 代码。
实测结果**另起 §14 追加**，本预注册正文（§0–§13）届时**一字不改**。

**本实验的身份（必须整句引用）**：

> **真实 Zcash PCZT 环境性能基线与 F4-A 外部有效性边界验证**

**不是**：

> ~~F4-A 的真实端到端 A/B 实验~~

**上游**：[`28_real_e2e_readonly_audit.md`](28_real_e2e_readonly_audit.md)（只读审计，证伪了
"直接拿现有环境做 F4-A A/B"的前提）。用户据此确认**走路线 A，不启动路线 B**。

**与 F4-A 定稿的关系（本轮冻结纪律）**：

| 约束 | 状态 |
| --- | --- |
| `26_f4a_action_scaling_prereg.md`（v1.0 FROZEN + errata-2） | **不动**（本轮未读改、未提交任何针对它的变更） |
| `f4a-closed-2026-11-05` 标签、INDEX 的 F4-A 结论区、README 的 F4-A 行 | **不动** |
| doc 28 与本文 | **独立阶段材料**，本轮**不挂进 F4-A 冻结结论区**；待本阶段实测结束后统一补一个隔离的索引分区 |
| H8、F4-B | 仍暂缓 |
| 路线 B | **明确不启动**，定位见 §6.4 |

---

## 0. 冻结清单（用户指定的七项，逐项指向正文）

| # | 要冻结的项 | 正文 | 一句话 |
| --- | --- | --- | --- |
| 1 | 精确时间窗口 | §3 | `T_build = create + sign + prove + extract`，终点是 `pczt_extract` 返回；broadcast / mine / confirm 只作门禁 |
| 2 | 固定交易场景 | §8 | 沿用历史已跑通的那**一个** PCZT 场景，参数逐项钉死；不贴任何 spend/output 数标签 |
| 3 | PID 精确识别方法 | §5.2 | `pgrep -x` + `/proc/PID/exe` + SHA256 三重确认，禁用历史 fallback |
| 4 | RSS 采样规则 | §5.3–§5.5 | 只在 `pczt_prove` 窗口内采样、50 ms、分级判据，噪声大就报"不可分辨" |
| 5 | 20 次重复 | §9 | 20 个**有效**轮次、单臂、不交错、warm-up 1 轮不计入 |
| 6 | 成功 / 失败排除规则 | §9.3、§10 | 三门禁 + 形态 + PID 恒定；失败补足但不得拼接批次 |
| 7 | 允许与禁止得出的结论 | §7 | 允许占比与噪声范围；禁止任何 "F4-A 在真实 Zcash 中加速 X%" |

**另外冻结三条全局纪律**（贯穿全文，违反即整批作废）：

1. **只测现有官方预编译环境**：`zallet-zaino 0.1.0-beta.3` / `halo2_proofs 0.3.2` / 现有 Zebra regtest /
   现有快照 / 现有 PCZT 调用链。**不装 Rust、不拉源码、不移植 F4-A、不重新构建 prover、不改 prover 配置**。
   唯一允许新增的代码是**测量脚本**（Python，从 `5e46712` 的 `pczt_bench_pool.py` 派生，改动清单见 §11.3）。
2. **绝对毫秒只在本批次内解释**：不得与 2026-10-29 的历史批次、也不得与 doc 24/25/26 的 harness 毫秒
   并列、算百分比或做显著性比较（doc 26 G2 已定证同指纹同构建也会漂 13%）。今日只读复核又发现
   **WSL 内核已从 `6.18.54.1` 变为 `6.18.58.1`**，这是新的跨会话漂移证据（§2.2）。
3. **不产出任何 F4-A 端到端加速数字**——包括带 E3 标签的"推算上界"。doc 28 §7 的路线 C 在本阶段
   **明确不采用**。

---

## 1. 实验目的

**目的**（用户冻结的原文口径）：

> 测量真实 PCZT / Zcash 交易流程的实际时间组成、proof 阶段占比和进程级内存行为，
> 并建立 F4-A 结果与真实系统之间的边界。

具体回答三个问题：

| # | 问题 | 由哪一节回答 |
| --- | --- | --- |
| Q1 | 在当前真实 regtest 环境里，构造一笔完整可广播交易要多久？四段各占多少？ | §4 |
| Q2 | wallet 侧证明阶段（`pczt_prove`）在这个构造窗口中占多大比例？ | §4.3 |
| Q3 | `pczt_prove` 窗口内 `zallet-zaino` 的进程级 RSS 基线是多少、噪声范围多大？该噪声能否分辨 6 MiB 量级？ | §5 |

**明确不回答**：F4-A 让真实 Zcash 交易快了多少、F4-A 在真实环境的内存代价是多少。
原因不是"数据不够"，而是**现有二进制里没有 F4-A，因而没有 B 臂**（§2.3、§6.1）。

**与已完成层次的分工**（沿用 doc 28 §3.2 的边界）：

| 层次 | 状态 | 本轮 |
| --- | --- | --- |
| selector compression 机制 / Halo2 evaluator / quotient | 已完成（doc 22–27） | ❌ 不重做 |
| Orchard proving harness / 多 Action 标度 | 已完成（doc 24–26） | ❌ 不重做 |
| **真实 Zcash 交易流程的单臂基线** | 未做 | ✅ **本轮唯一内容** |
| 真实交易流程的 F4-A A/B | 未做，且当前环境**不具备条件** | ❌ 属路线 B，不启动 |

---

## 2. 环境版本（冻结，测量前逐项复核）

### 2.1 组件与指纹

| 项 | 冻结值 | 复核入口 |
| --- | --- | --- |
| 节点 | `zebrad 6.2.3`，regtest，RPC `127.0.0.1:18232`（回环免鉴权） | `binfingerprint_20261029_154419.txt` |
| 节点 SHA256 | `baf0b2cd31110c7fe7c05809fd7323d07548489be78d31caeccc91e1469cc6a7` | 同上 |
| 钱包 launcher | `zallet 0.1.0-beta.3`，SHA256 `523bf86a14cba2dfb1ea048c3a3d6ccc68bde0dbb92da60d44ce12568dea1c5a` | 同上 |
| **实际 prover 进程** | `zallet-zaino 0.1.0-beta.3`，`/home/dell/z3-regtest/bin/zallet-zaino`，SHA256 `e602e4a3d4e919c155b8f05dae29f0b9311631f167eaf25455166edf268208a7` | 同上；测量时用 `/proc/PID/exe` 现取现核 |
| 钱包 RPC | `127.0.0.1:40232`，cookie `~/z3-regtest/data/zallet/.cookie` | `pczt_bench_pool.py` |
| 链快照 | `z3_regtest_h32297_20261029_145710.tar.gz`（13 167 422 B） | `snapshot_restore.sh --check` 已通过：1058 条目、`wallet.db` integrity ok、`blocks(MAX,COUNT)=(32297,32297)`、账户 `bench` |
| 网络升级 | Overwinter–Canopy 高度 1，NU5–NU6.3 高度 2；ZIP 212 grace end = 32257，快照 32297 已越过 | `pczt_pool_v2/REPORT_v2.md` §1.2 |
| 账户 UUID | `5fd7056a-3e6e-455f-b75d-d80b0556fea9` | `pczt_bench_pool.py` |
| 收款 UA | `SAP_UA`（同一 sapling UA，循环外解析一次复用，避免透明地址 gap limit 16） | 同上 |

### 2.2 今日只读复核发现的环境漂移（必须记录，不得静默）

| 项 | 2026-10-29 指纹记录 | 2026-11-06 只读复核 | 处置 |
| --- | --- | --- | --- |
| WSL2 内核 | `6.18.54.1-microsoft-standard-WSL2` | **`6.18.58.1-microsoft-standard-WSL2`** | 记为**新的跨会话条件**；历史 1415.2 ms 只作方向性参照，禁止并列 |
| 服务状态 | 运行中（测量时） | **全部停止**，18232 / 40232 / 18233 无监听 | 测量前按 §11 门禁启动，不得在"部分服务"状态下开跑 |
| `taskset` | `/usr/bin/taskset` 可用但脚本未用 | 仍可用 | 本轮按 §11.2 固定并记录 |
| `python3` | 可用 | `/usr/bin/python3` 可用 | 无 numpy 依赖（统计口径见 §4.4） |

> 三个二进制文件仍在 `~/z3-regtest/bin/`，mtime 与指纹记录一致；
> **二进制未变，变的是宿主内核与会话**。这正是"跨会话绝对毫秒不比较"的实例。

### 2.3 版本鸿沟（A/B 不可切换的定证，路线 B 不启动的依据）

| 对象 | 版本 | F4-A |
| --- | --- | --- |
| 真实 `zallet-zaino` 二进制 | 二进制串：`halo2_proofs-0.3.2` ×30、`orchard-0.15.3` ×14；含 `ironwood_v3` / `NU6_3` / `compress_selectors` | **否**：`ORCHARD_F4A`、`f4a_impl`、`Ast::Memo`、`structural_mul_eliminations` 全部零命中 |
| F4-A 补丁 | `vendor/halo2_proofs-0.4.0-f4a.patch`，834 行 / 6 文件，运行期 `ORCHARD_F4A=1` 门控 | 只在 orchard harness 的 vendored crate 里 |
| F4-A harness | `orchard 0.16.0 @ 616a669d` + `[patch.crates-io] halo2_proofs = vendor/halo2_proofs-0.4.0` | doc 24–26 的全部数字出自这里 |

> 二进制串检索是**线索**不是源码级证明；但"没有门控符号"足以否定"同一份代码 env 一开一关即可 A/B"。
> WSL 无 `rustc` / `cargo`、无 zallet 源码 checkout、本地无 0.3.2 源码可比对。

---

## 3. 调用链与精确时间窗口

### 3.1 调用链（冻结，与历史已跑通的顺序一致）

```
pczt_create([ACCOUNT, [{address: SAP_UA, amount: 3}], 1,
             "AllowRevealedAmounts", "orchard"])        ┐
  → pczt_inspect([pczt])            形态诊断，不计时    │
  → pczt_sign([pczt, policy, False])                   │  T_build
  → pczt_prove([signed.pczt], timeout=1800)            │  （主指标）
  → pczt_extract([proved.pczt])     ← 终点，得到 tx     ┘
  ───────── 以下仅为正确性门禁，不进入任何时间指标 ─────────
  → zebra sendrawtransaction([hex])
  → zebra generate 2                （只认链高增量，忽略假失败返回值）
  → zebra getrawtransaction(txid,1).confirmations ≥ 1
```

`pczt_inspect` 是只读诊断 RPC，位于 create 与 sign 之间，**不计入 `T_create`**，且不得改动 PCZT 内容。
前后置的 `wait_sync()`（判据 `wallet_tip >= max(node_tip, zebra.blocks)`）**也不计入**任何计时窗口。

### 3.2 主指标与三层时间

| 记号 | 定义 | 说明 |
| --- | --- | --- |
| `T_create` / `T_sign` / `T_prove` / `T_extract` | 各 RPC 的 `time.monotonic()` 墙钟（毫秒） | 四段单独报告，取自同一时钟 |
| **`T_build`** | `T_create + T_sign + T_prove + T_extract` | **主指标**；终点 = 获得完整可广播 transaction |
| `T_proof` | `= T_prove` | **钱包侧证明阶段的 RPC 墙钟**，见 §3.3 的三条限制 |
| `T_other` | `= T_build − T_prove = T_create + T_sign + T_extract` | 用户原方案的 "Other"；历史量级约 **73–78 ms**，不是示例里的 800 ms |
| `T_wall` | 从 `pczt_create` 调用前到 `pczt_extract` 返回的整体墙钟 | **核对项**，见 §3.4 |
| `prove_share` | `T_prove / T_build` | 口径见 §4.5（两种，分开标注） |

**明确排除**：`broadcast`、`mine`（历史中位 522.8 ms）、`confirm`、前置/后置 `wait_sync`。
历史 `total_ms = 2155.5 ms` 含这些项，**不得当作本实验的 `T_build`**，也不得反向把本实验的 `T_build`
与历史 `total_ms` 比较。

### 3.3 `T_proof` 不是 harness 的 `create_proof`（防止口径偷换）

1. 它是 `pczt_prove` 这条 **JSON-RPC 的墙钟**，含请求/响应与 PCZT 序列化，不是进程内插桩。
2. 该 RPC 内部还可能包含同一 PCZT 里的其它证明（历史上 `sapling_proven` 也会返回 true，
   即便 `sapling.spends=0`），**不等于** F4-A 作用的 quotient 阶段耗时。
3. 真实路径恒为 `ironwood.actions=2` / `orchard.actions=0`（padding），与 harness 的
   1/2/4/8 Action Orchard bundle 不是同一形态。

### 3.4 `T_wall` 核对纪律

`T_build` 是四段 RPC 墙钟之和；`T_wall` 还包含 `pczt_inspect`、客户端 Python 开销等。
每轮都记录两者，并报告 `T_wall − T_build`：

- 若 `median(T_wall − T_build) / median(T_build) ≤ 5%`，主表只报 `T_build`，差额写进附录；
- 若 > 5%，**同时报告两个口径**并说明差额来源，不得用 `T_wall` 静默替换 `T_build`，也不得事后把
  `pczt_inspect` 挪出窗口来"抹平"差额（窗口定义已冻结）。

---

## 4. 时间结果（预注册：定义与口径；实测数值见 §14+，本节不改）

### 4.1 每轮必录字段

```
run_id, version, scenario,
create_ms, sign_ms, prove_ms, extract_ms, build_total_ms, wall_ms, other_ms,
prove_share, txid, size_bytes, actions, signed, fee_zat, policy,
extract_success, broadcast_accepted, confirmed, confirmations,
wallet_pid, wallet_exe, wallet_sha256, rss_window_samples, valid
```

| 字段 | 冻结值 / 说明 |
| --- | --- |
| `version` | **恒为 `official_beta3_no_f4a`**（单臂）；本实验**不存在 `f4a` 臂** |
| `scenario` | **恒为 `frozen_pczt`**；禁止写成 "1 Spend → 2 Outputs" 等未经本环境证明的标签 |
| `actions` / `signed` | 来自 `pczt_inspect`，只作形态门禁与记录（§8.3） |
| `valid` | 布尔，判定见 §9.2 |
| `txid` | 每轮不同（签名与证明随机性）。**不要求任何两个 txid 相等**，只要求节点接受 |

### 4.2 统计对象

**只对 `valid = true` 的 20 轮**统计；失败轮次另表存放，不混入。
统计 `T_create` / `T_sign` / `T_prove` / `T_extract` / `T_build` / `T_other` / `T_wall` 七个量。

### 4.3 主结果表（实测后填，格式先冻结）

| 量 | n | median (ms) | IQR (ms) | min (ms) | max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `create` | 20 | — | — | — | — |
| `sign` | 20 | — | — | — | — |
| `prove` | 20 | — | — | — | — |
| `extract` | 20 | — | — | — | — |
| **`build_total`** | 20 | — | — | — | — |
| `other` | 20 | — | — | — | — |
| `wall`（核对） | 20 | — | — | — | — |

`min/max` **仅作辅助**，不用于结论；不报标准差代替 IQR，不报 p 值，不做任何假设检验（单臂无对照）。

### 4.4 统计量算法（冻结，避免实现期二选一）

- `median`：`statistics.median`（n=20 偶数，取中间两值平均）。
- `IQR = Q3 − Q1`：用 Python 标准库 **`statistics.quantiles(x, n=4, method='inclusive')`** 的
  第 3 与第 1 个分位点；**不使用** numpy 的 linear 插值（两套定义在小样本上会给出不同数值）。
- 所有时间量单位毫秒，保留 1 位小数；比值保留 4 位小数或百分比 2 位小数。
- 百分位数对 n=20 敏感，只作描述，不构造置信区间、不做显著性判断。

### 4.5 `prove / build_total` 的两种口径必须分开标注（沿用 doc 26 §11.0 纪律）

| 口径 | 记号 | 地位 |
| --- | --- | --- |
| **逐轮比值的中位数** | `median(prove_ms / build_total_ms)` | **主报**，回答"典型一轮里 prove 占多少" |
| 边际中位数之比 | `median(prove_ms) / median(build_total_ms)` | 辅助，仅供核对 |

二者**不可互算**：`median(A)/median(B) ≠ median(A/B)`。报告中必须写明用的是哪一个，
不得用其中一个的数字去印证另一个。历史报告的 95.1% 属**混合口径的历史事实**
（由两个中位数相除得到），本轮**不拿它做比较基准**，只在 §6 里作方向性提及。

### 4.6 单批次纪律

本批次绝对毫秒只在**本批次内**解释。历史数字（`pczt_prove` 稳态中位 1415.2 ms、四阶段 1488.2 ms、
`T_other` 73.0 ms、`mine` 522.8 ms、N=15）只作为**环境合理性检查的方向性参照**：
若本批次量级偏离一个数量级，先查环境门禁 §11；**不得**计算与历史的百分比差异、
不得并列成表当作结论、不得据此声称"复现"或"未复现"。

---

## 5. RSS 结果（预注册：PID、采样、分级判据；实测数值见 §14+，本节不改）

### 5.1 目标（按用户冻结的口径，不得扩大）

> **建立真实 proving 窗口的进程级 RSS 观测基线和噪声范围。**

本实验**没有 F4-A B 臂**，因此：

- **不计算、不报告 `ΔRSS_A/B`**；
- **不报告任何"F4-A 内存代价"数值**；
- **6 MiB 只是一个参照尺度**（来自 doc 25 §实测：1P 采样峰值 `+6.09 MiB`、8P `+6.72 MiB`），
  是**别的实验**在**别的二进制**上的观测值，不是本实验测得的量，也不预设本实验应当看到它。

### 5.2 PID 精确识别（冻结方法）

1. 用 `pgrep -x zallet-zaino` 与 `pgrep -x zebrad` 取候选 PID（`pgrep -x` 精确匹配进程名）。
2. 对每个 PID 取 `readlink -f /proc/<pid>/exe`，要求解析到
   `/home/dell/z3-regtest/bin/zallet-zaino`（节点为 `.../zebrad`）。
3. 对该路径计算 SHA256，必须与 §2.1 的指纹一致；zebrad 同理。
4. 把 `pid → exe → sha256` 写入每轮记录与 `meta.json`；**PID 在整批 20 轮内必须恒定**，
   任何变化判为 `pid_changed`，整批作废（§9.3）。
5. **禁止**使用历史 `find_pids()` 里的 `pgrep -f '/zallet '` fallback：doc 28 §5.2 已复现它会命中
   执行 `pgrep` 的 shell 自身，历史 `memory_timeseries.csv` 的进程归属因此不能无条件采信。
6. `zallet` launcher 只是 dispatcher，**不采集它的 RSS**；主对象是 `zallet-zaino`。

### 5.3 采样规则（冻结）

| 项 | 冻结值 | 理由 |
| --- | --- | --- |
| 采集源 | `/proc/<wallet_pid>/status` 的 `VmRSS`（KB）；同点顺带记 `zebrad` 的 `VmRSS` 供排查 | 与历史口径一致，可比 |
| 采样间隔 | **50 ms**（历史是 0.25 s，对 1.4 s 的 prove 窗口只有 ~5 点，偏粗） | 1.4 s 窗口约 28 点 |
| 采样范围 | 采样线程常驻整批，但**只把落在 `pczt_prove` 窗口内的点计入 RSS 结论** | 用户冻结："只在 prove 开始→结束窗口内采样" |
| 窗口边界 | 主循环在 `pczt_prove` 调用前后各记一个 `time.monotonic()` 时间戳 `t_prove_start` / `t_prove_end`，写入 `rss_windows.csv` | 边界由单调时钟定义，不由采样线程猜测 |
| 落窗判据 | `t_prove_start ≤ t_mono ≤ t_prove_end`（闭区间）；边界外的点标 `phase=outside` 并排除 | 与 RPC 墙钟同源 |
| 交叉校验 | 采样点同时带 `phase` 字段（`in_prove` / `outside`），由主循环置位；相位与时间戳判定不一致时，以**时间戳**为准并记一条 anomaly | 防止线程调度导致相位滞后 |
| 每轮最少点数 | **10 个窗口内点**；不足则该轮 RSS 标 `low_confidence`（时间数据仍有效），不计入 RSS 汇总 | 避免用 1–2 个点冒充峰值 |
| 干扰控制 | 采样线程只读 `/proc`、不做 RPC、不写日志到同步路径；采样开销不单独测量 | 50 ms 一次属轻量 |

### 5.4 明确禁用的两个口径

1. **`VmHWM` / 进程生命周期峰值 / 全局 Peak RSS 一律不报**：wallet 是长驻进程，keygen、同步、
   缓存早已把高水位抬到几百 MB。与 doc 24 §8.3 在 Windows 上遇到的 `PeakWorkingSetSize` 是同一个坑。
   本报告只报"**`pczt_prove` 窗口内采样峰值**"。
2. **不把窗口内 RSS 增量归因给 F4-A 或任何机制**：本实验是单臂观测，没有对照组。

### 5.5 报什么、怎么判定（分级判据，阈值先冻结）

每轮窗口内计算并落盘：

```
rss_first_kb, rss_min_kb, rss_max_kb (= 窗口峰值), rss_median_kb,
delta_window_kb = rss_max_kb − rss_min_kb,  rss_window_samples
```

跨 20 轮汇总：`rss_max_kb` 与 `delta_window_kb` 各自的 median / IQR / min / max，以及
`rss_first_kb` 的极差（轮间基线漂移）。

**噪声量冻结定义为**：

```
N_rss = max_r(rss_max_kb) − min_r(rss_max_kb)      （20 轮窗口峰值的极差，单位 MiB）
```

三级判据（阈值在测量前冻结，事后不得调整）：

| 档 | 条件 | 允许写的结论 |
| --- | --- | --- |
| **R1** | `N_rss ≤ 6 MiB` | "就单臂轮间噪声而言，该环境下窗口采样具备分辨 6 MiB 量级的分辨率；**但仍需 A/B 配对才能把差异归因给某个机制**" |
| **R2** | `6 < N_rss ≤ 18 MiB` | "分辨率处于边缘，不足以可靠分辨 6 MiB 量级" |
| **R3** | `N_rss > 18 MiB`（即 > 3× 参照尺度） | **"该环境下无法用当前进程级采样分辨 6 MiB 量级的机制代价"** |

- 18 MiB = 3 × 6 MiB 是用户 `noise ≫ 6 MiB` 的可判定化，选 3× 而非 2× 或 10× 属**预注册选择**，
  写在测量之前；若实测落在边界附近，只照表报档，不改阈值。
- 历史背景（只作预期管理、不参与判定）：`pczt_cold_v1` 的 161 个采样点里 wallet RSS 从
  231 448 KB 摆到 386 116 KB，跨度 **151 MiB**；参照尺度 6 MiB 约为其 4%。
- **R3 不是失败，是工程结论**，按用户原话写。任何档位都**不得**进一步写成
  "F4-A 没有内存代价" / "F4-A 内存代价为 0" / "F4-A 在真实环境多占 Y MiB"。

---

## 6. 与 F4-A 的关系（必须写清，不得留白）

### 6.1 核心事实（一句话，报告里必须出现）

> **F4-A 没有部署到该二进制，因此本实验不构成 F4-A 端到端加速证据。**

支撑：§2.3 的版本鸿沟 + 二进制里 `ORCHARD_F4A` / `f4a_impl` / `Ast::Memo` 零命中 +
WSL 无工具链与源码。本实验全程没有启用、也没有能力启用 F4-A。

### 6.2 本实验能说的只有一句

> 真实系统中，钱包侧证明阶段（`pczt_prove`）在当前环境、当前场景的**交易构造窗口**中占多大比例。

这句话是**占比的事实描述**，不含任何加速判断、不含任何 F4-A 收益折算。

### 6.3 外部有效性边界（为什么 doc 26 的结论不能直接迁移过来）

| # | 边界 | 依据 |
| --- | --- | --- |
| E1 | crate 版本不同：真实 `halo2_proofs 0.3.2` vs F4-A 的 `0.4.0`；`evaluator.rs` 差异未知，本地无 0.3.2 源码 | doc 28 §3.3 |
| E2 | orchard 版本不同：二进制串 `0.15.3` vs harness `0.16.0`；真实电路与 `PostNu6_3` 是否同形**未做源码级确认** | doc 28 §3.3 |
| E3 | 证明入口不同：`pczt_prove` 是含序列化的 RPC 墙钟，不是 `Bundle::create_proof` 插桩 | §3.3 |
| E4 | 形态不同：真实恒 `ironwood.actions=2`（padding），harness 是 1/2/4/8 Action Orchard bundle | doc 28 §4.2 |
| E5 | 测量层次不同：本实验**单臂基线**，无 A/B、无同会话配对、无 95% CI | §9 |
| E6 | 会话不同：宿主内核已漂移，跨会话绝对毫秒不可并列（doc 26 G2） | §2.2 |

因此本报告**不产出任何 F4-A 端到端加速数字**，包括"按占比折算的上界"、
"假设 F4-A 收益可传递则约 X%"、带 E3 标签的推算区间。doc 28 §7 的路线 C 在本阶段不采用。

### 6.4 路线 B 的定位（用户冻结）

> **B 是未来独立工程，不属于 F4-A 当前阶段。**

它实际意味着六步：`源码获取 → 版本兼容性审计 → halo2 0.4 → 0.3 移植/重实现 →
构建真实 zallet → 验证真实电路是否同形 → A/B 回归`。这不是"补一个实验"，
而是与 F4-A 主研究同量级的新工程。本轮不立项、不预研、不做可行性试探。

---

## 7. 允许与禁止得出的结论（用户指定的第 7 项）

### 7.1 允许（正列表，只有这五条）

1. 在**本批次、本配置、本场景**下，`T_build` 与四个 stage 的 median / IQR / min / max。
2. `prove` 在 `T_build` 中的占比（两种口径分开标注，§4.5）。
3. `pczt_prove` 窗口内 `zallet-zaino` 的 `VmRSS` 分布与轮间噪声范围。
4. 按 §5.5 的 R1/R2/R3 档，得出"当前进程级采样能否分辨 6 MiB 量级"的**方法论结论**。
5. 正确性门禁的通过情况（20/20 或失败明细）与环境版本记录。

### 7.2 禁止（负列表）

| # | 禁止措辞 / 推论 | 原因 |
| --- | --- | --- |
| 1 | **"F4-A 在真实 Zcash / 真实交易中加速 X%"**（任何形式：点估计、区间、上界、E3 推算） | 没有 B 臂，§6.1 |
| 2 | "F4-A 在真实环境的内存代价是 Y MiB" / "证明 F4-A 不增加内存" | 没有 B 臂，§5.1 |
| 3 | "已在真实 Zcash 交易流程中验证 F4-A" / "F4-A 具备外部有效性 / 可推广" | 未部署，§6.1 |
| 4 | 把 `pczt_prove` 墙钟等同于 `create_proof` / quotient 耗时 | §3.3 |
| 5 | 把 `fund_source=orchard` 读成 "Orchard Spend"；给场景贴 "1 Spend → 2 Outputs" 等标签 | §8.3、doc 28 §4.2 |
| 6 | 把 broadcast / mine / confirm / `wait_sync` 计入 `T_build`；把历史 `total_ms` 当作 `T_build` | §3.2 |
| 7 | 用 `VmHWM` / 全局峰值 RSS 充当机制代价；把窗口内增量归因给某机制 | §5.4 |
| 8 | 把本批次绝对毫秒与 2026-10-29 历史批次、或 doc 24/25/26 的 harness 毫秒并列、算百分比 | §0 纪律 2、§4.6 |
| 9 | 用 `median(prove)/median(build)` 冒充 `median(prove/build)` | §4.5 |
| 10 | 把 R3"无法分辨 6 MiB"写成"F4-A 内存代价为 0 / 不存在" | §5.5 |
| 11 | 删掉 E1/E2/E3 后又偷偷按 action 数分组比较，声称"趋势传递" | §8.3 |
| 12 | 把单批 20 轮的 IQR 写成置信区间或显著性结论 | §4.3、§4.4 |

---

## 8. 固定交易场景（用户指定的第 2 项）

### 8.1 参数冻结（与历史 N=15 完全同参，便于同口径合理性检查）

| 参数 | 冻结值 | 来源 |
| --- | --- | --- |
| `rounds` | **20**（有效轮次）+ 1 轮 warm-up | 用户指定 |
| `send_zec` | **3.0** | `pczt_pool_v2/meta.json` |
| `minconf` | **1** | 同上 |
| `policy` | **`AllowRevealedAmounts`** | 同上；跨池注资会揭示金额，是协议语义 |
| `fund_source` | **`orchard`**（参数名；实际命中 ironwood 池） | 同上 |
| 收款方 | 同一个 `SAP_UA`，循环外解析一次复用 | 脚本注释：避免透明地址 gap limit 16 |
| `mine_blocks` | **2**（只在正确性门禁里） | 同上 |
| `pczt_prove` timeout | 1800 s | 同上 |

**不得**在测量期间调整任何参数；若确需调整，整批作废并按新预注册重跑（§9.3）。

### 8.2 资金预检（本次新发现，必须写进门禁）

脚本自带全轮次预检：`need_total = (send_zec + 0.001) × 10^8 × rounds = 60.02 ZEC`。
历史批次的 ironwood 起始余额只有 **51.87 ZEC**（`pczt_pool_v2`）与 **28.74 ZEC**（`pczt_cold_v1`），
均**不足以支撑 20 轮 × 3 ZEC**；快照 h32297 的当前余额在服务未启动时无法读取，故列为 G0 门禁项。

- G0 必须实测 `z_getbalanceforaccount` 的目标池余额并写入 `meta.json`。
- 若 `< 60.02 ZEC`：**允许且只允许一次** `python3 /mnt/d/Research/z3/wsl/fund_pool.py --fund-zec 40`
  跨池注资（历史 sapling 池有 79–102 ZEC 可供），注资后把注资金额与两侧余额写入 `meta.json`。
- 注资属**测量前的链准备**，不计入任何轮次；注资带来的挖块与链高推进如实记录。
- 注资完成后**重新执行 warm-up 与 G0**；注资前的任何轮次都不得并入 20 轮。
- **不允许**改用更小的 `send_zec` 来规避余额不足——那会引入与冻结场景无关的新变量。

### 8.3 形态门禁（不造 E1/E2/E3）

按用户决定，**删除 E1/E2/E3 场景矩阵**。本阶段只有**一个**场景：当前真实、可重复构造的 PCZT 场景。

- 每轮 `pczt_inspect` 记录 `ironwood.actions`、`orchard.actions`、`signed`、`fee_zat`。
- 主形态门禁：`ironwood.actions == 2` 且 `orchard.actions == 0`（历史 10 条日志全一致）。
  出现其它值 → 该轮标 `shape_changed`，不计入主统计，并在报告中单独列出。
- `signed = 1` 或 `2` **不作为分组依据**：历史单因子结论是两者 prove 只差 0.5%、
  padding dummy 与真实 spend 同价（`pczt_pool_v2/REPORT_v2.md` §0.3）。本实验**不重新检验**该结论，
  只把 `signed` 作为记录字段。
- **禁止**按 action 数、spend 数、收款地址数分组比较并声称"规模趋势"。
- **禁止**把 `fund_source="orchard"` 读成 "Orchard Spend"；它实际命中 ironwood 池
  （`target_pool()` 注释与 `pczt_orchard2.log` 余额均可复核）。

### 8.4 warm-up

正式计数开始前，在**同一进程、同一配置**下跑 **1 轮 warm-up**（完整含门禁），其数据落盘但
**不计入 20 轮、不进入任何统计**。理由：历史报告 §0.4 定证进程级冷启动首轮 prove 比同进程
稳态高约 142 ms（+10%）。warm-up 之后若进程重启，整批作废。

---

## 9. 20 次重复、有效运行与排除规则（用户指定的第 5、6 项）

### 9.1 "独立运行"的定义

一轮 = 一次完整 `create → sign → prove → extract` + 三道正确性门禁，使用 wallet 当前可用 note
构造一条**新交易**（txid 各不相同），PCZT 不复用。20 轮在同一 wallet 进程内**连续**执行，
不重启、不恢复快照、不改参数、轮间不插入其它负载。

**单臂基线，不做 A/B 交错、不做 ABBA、不做配对统计**（没有 B 臂，交错无意义）。

### 9.2 有效运行（`valid = true`）需同时满足

1. 三道正确性门禁全部通过（§10）；
2. 四个 stage 计时完整、`T_wall` 有值；
3. 形态门禁通过（`ironwood.actions == 2`、`orchard.actions == 0`）；
4. wallet PID 与批次开始时一致，且 `/proc/PID/exe` + SHA256 复核通过（§5.2）；
5. 无未预期异常（RPC error、超时、脚本异常）；
6. 该轮不属于 warm-up，也不属于注资前的准备轮次。

### 9.3 排除与补足（失败即记录，不静默丢弃）

| 失败码 | 含义 | 处置 |
| --- | --- | --- |
| `extract_failed` | `pczt_extract` 异常或 `txid`/`hex` 为空 | 写 `invalid_runs.csv`，不计入统计 |
| `broadcast_rejected` | `sendrawtransaction` 返回 error | 同上 |
| `not_confirmed` | 60 s 内 `confirmations` 未 ≥ 1 | 同上 |
| `shape_changed` | actions 非 2 / orchard.actions 非 0 | 同上，并单独报告形态 |
| `pid_changed` | wallet 进程重启或 exe/SHA 不符 | **整批作废**（见下） |
| `timing_incomplete` | 任一 stage 计时缺失 | 写 `invalid_runs.csv`，不计入统计 |
| `rpc_error` / `timeout` | 其它异常 | 同上 |

- 失败轮次在同一批次内**顺延补足**，直到 20 个有效轮次；总尝试上限 **26 轮**
  （20 有效 + 最多 6 次失败），超过则整批作废并报告，不无限重试。
- **无异常不得剔除**：不得因为某轮"看起来偏慢/偏快"而事后删除；只有上表预定义失败码可排除。
- **整批作废条件**（不允许拼接两个批次凑够 20）：进程重启、快照恢复、注资发生在计数轮次之间、
  参数变更、CPU 亲和在中途被改动、跨天/跨会话继续。作废后按 §11 重新起环境，全部 20 轮重跑。
  依据是 doc 26 G2：跨批次绝对毫秒不可并列。

---

## 10. 正确性门禁（用户指定：只作门禁，不进主时间指标）

每轮记录三项布尔值，全部为真才 `valid`：

| 门禁 | 判据 | 计时 |
| --- | --- | --- |
| `extract_success` | `pczt_extract` 返回非空 `txid`、非空 `hex`、`stored` 为真 | **计入** `T_extract` |
| `broadcast_accepted` | zebra `sendrawtransaction([hex])` 无 error | **不计入** `T_build` |
| `confirmed` | `getrawtransaction(txid,1).confirmations ≥ 1`（`generate 2` 后轮询 ≤ 60 s） | **不计入** `T_build` |

- `generate` 返回 `failed to send mined block to gossip task` 是 beta.3 的**假失败**，
  只认链高增量，不看返回值（历史踩坑记录）。
- 同步判据用 `wallet_tip >= max(node_tip, zebra.blocks)`，忽略 `locked` 字段。
- txid 天然不同（签名与证明随机性），**不要求任何两个 txid 相等**；要求的是语义一致 + 节点接受。
  这与 harness 里"proof 与 `h` 指纹逐位不变"是两种不同口径，不得互相套用。

---

## 11. 环境门禁 G0（测量开始前执行，失败则不进入 20 轮）

### 11.1 启动与校验（按顺序，全部只读或既有脚本）

```bash
SNAP=/mnt/d/Research/z3/snapshots/z3_regtest_h32297_20261029_145710.tar.gz
bash /mnt/d/Research/z3/wsl/snapshot_restore.sh --check "$SNAP"     # 只读深校验，须通过
bash /mnt/d/Research/z3/wsl/snapshot_restore.sh --yes  "$SNAP"     # 恢复（若数据根已一致可跳过并记录）
bash /mnt/d/Research/z3/wsl/regtest_run.sh                          # 起 zebrad + zallet-zaino
```

G0 检查清单（逐项落盘到 `meta.json`）：

1. 快照 `--check` 通过；链高 ≥ 32297（ZIP 212 grace end = 32257）。
2. `zebrad` 与 `zallet-zaino` 均在运行，18232 / 40232 可连；`getwalletstatus` 同步判据成立。
3. 按 §5.2 解析两个 PID 的 `/proc/PID/exe` 并核对 SHA256，与 §2.1 一致。
4. 记录 `uname -r`、二进制 mtime、cookie 存在性、三个二进制的现取 SHA256。
5. 资金预检（§8.2）：目标池余额 ≥ 60.02 ZEC；不足则先注资再回到本清单第 2 步。
6. `pczt_inspect` 在一轮 warm-up 上确认 `ironwood.actions=2` / `orchard.actions=0`。

### 11.2 CPU 控制（单配置，冻结）

- 启动后对两个 PID 执行 `taskset -pc 0x1 <pid>`（`zebrad` 与 `zallet-zaino` 共用同一固定核），
  再 `taskset -pc <pid>` 回读，把 mask 写入 `meta.json`。
- `RAYON_NUM_THREADS` **不主动设置**（需改启动脚本，属于改变现有环境），如实记录默认值。
- 若 `taskset` 失败或 CPU 数不足：记录实际状态、在结论里加"CPU 未完全固定"限制，
  **不中止实验**（单臂基线不做因果比较），但**不得**在批次中途改动 mask。
- gate 阶段（broadcast / mine / confirm）不计时，节点进程争抢不影响主指标。

### 11.3 测量脚本允许改动的范围（冻结，防止实施期偷改场景）

从 `git show 5e46712:experiments/e2e_testnet_tx/pczt_bench_pool.py` 派生一份测量脚本，
**只允许以下五类改动**，其余（尤其 RPC 调用形态与交易参数）保持原样：

1. 把 `T_build` 窗口切到 `create+sign+prove+extract`，另记 `T_wall`（§3）。
2. 增加每轮字段（§4.1）与 `valid` 判定、`invalid_runs.csv`（§9）。
3. `find_pids()` 改为 `/proc/PID/exe` + SHA256 精确识别（§5.2）。
4. 采样间隔 0.25 s → 50 ms，增加 `phase` 标记与窗口边界时间戳、窗口汇总（§5.3–§5.5）。
5. `summary.csv` 增加 IQR，并把两种 `prove_share` 口径分开输出（§4.4–§4.5）。

**不得**改动：RPC 方法名与参数顺序、`send_zec` / `minconf` / `policy` / `fund_source` /
`mine_blocks`、收款 UA、账户 UUID、阶段顺序。脚本副本与所用 commit 号写入 `meta.json`。

---

## 12. 产物清单与冻结记录

### 12.1 产物（实测时生成，目录 `results/real_e2e_baseline/run_<ts>/`）

| 文件 | 内容 |
| --- | --- |
| `meta.json` | 环境、内核、三个二进制 SHA256、两个 PID/exe、taskset mask、冻结参数、余额与注资记录、warm-up 轮次、脚本 commit、本预注册文件的 SHA256 |
| `rounds.csv` | 20+ 行，字段见 §4.1 |
| `stage_timings.csv` | 长表 `run_id, stage, ms` |
| `summary.csv` | median / IQR / min / max / n（§4.3） |
| `rss_timeseries.csv` | `t_wall, t_mono, run_id, phase, wallet_pid, wallet_rss_kb, zebrad_rss_kb` |
| `rss_windows.csv` | 每轮 `t_prove_start/end`、窗口统计、`low_confidence` 标记、`N_rss` 与 R1/R2/R3 档 |
| `gate.csv` | 每轮三门禁布尔值与 `confirmations` |
| `invalid_runs.csv` | 失败轮次与失败码（含 warm-up 轮标记） |
| `environment.txt` / `build.txt` | `uname -r`、指纹回读、启动日志摘要 |

### 12.2 冻结记录

| 项 | 值 |
| --- | --- |
| 文档状态 | **预注册 · 测量前冻结**（2026-11-06） |
| 冻结方式 | **单独提交**（本文件一个 commit，不夹带实验代码或结果）；commit id 与文件 SHA256 在提交后由会话记录，并在实测的 `meta.json` 里回引 |
| 本轮实际执行的操作 | 只读：读文档、`git show` 历史脚本、`pgrep`/`ss`/`uname` 只读状态复核。**未起服务、未恢复快照、未发交易、未跑任何轮次** |
| 预注册正文 | §0–§13，**实测后一字不改** |
| 结果追加 | 另起 **§14 起**追加；若需改判据，只能新起勘误节并注明版本，不得就地改写 |
| 冻结材料是否受影响 | doc 26 v1.0 FROZEN、errata-1/2、INDEX 的 F4-A 结论区、README 的 F4-A 行、tag `f4a-closed-2026-11-05`：**全部未动** |

---

## 13. 结果追加规则（预注册正文的最后一条）

- §1–§7 是**最终报告主干**（实验目的 / 环境版本 / 调用链 / 时间结果 / RSS 结果 / 与 F4-A 的关系 /
  禁止外推）；§8–§12 是支撑它们的控制项。
- 实测后从 **§14** 开始追加：§14 环境与 G0 复核、§15 时间结果、§16 RSS 结果与 R 档判定、
  §17 正确性门禁汇总、§18 与 §7 允许/禁止清单的逐条对账、§19 结论（只写允许的五条）。
- 追加内容**不得**改写 §0–§13 的任何数字、阈值、判据与措辞；`N_rss` 阈值、20 轮、50 ms、
  `send_zec=3`、三档判据、禁止措辞清单均已在测量前固定。
- 若实测暴露预注册缺陷（例如 50 ms 采样拖慢 prove、资金规则不可行），**照原判据执行并如实报告**，
  缺陷写进勘误节，不在结果页里悄悄改口径。

---

## 14. Errata-1：资金预算勘误与重新执行条件（v1.0-errata-1，2026-11-06）

**性质**：按 §12.2 / §13 允许的**追加勘误通道**新增，§0–§13 **一字未改**。
本节只改一件事——**正式批次启动前的资金准备额度与 G0 余额判据**；
`T_build` 窗口、固定场景、PID 三重识别、50 ms prove 窗口 RSS、20 个有效轮次、
统计量算法、`N_rss` 三档阈值、§7 允许/禁止清单**全部不变**。
触发原因：Phase 1/2 实测（`4f7983d`）暴露 §8.2 的预算漏算，见 §14.2。

### 14.1 原规则错在哪

§8.2 把 `need_total = (send_zec + 0.001) × 10^8 × rounds = 60.02 ZEC` 当作
**启动 warm-up 之前**的余额门槛，但它只覆盖了 **20 个有效正式轮次**。
执行顺序实际上是：

```
恢复快照 → 注资 → G0 → 1 轮 warm-up（不计入统计）→ 20 个有效轮次
```

warm-up 同样是一次完整的 `create → sign → prove → extract → broadcast → mine → confirm`，
同样从 ironwood 池净支出一笔 3 ZEC 的转账。因此测量前必须保证的余额是
**「1 warm-up + 20 valid rounds」**，不是 20 轮。

| 项 | 原式（§8.2） | 勘误后 |
| --- | --- | --- |
| 单轮冻结需求（含手续费余量） | `3.001 ZEC = 300 100 000 zat` | 不变 |
| 覆盖轮数 | 20 | **21**（1 warm-up + 20 valid） |
| G0 启动门槛 | `60.02 ZEC = 6 002 000 000 zat` | **`63.021 ZEC = 6 302 100 000 zat`** |
| warm-up 放行门禁 | 无 | **warm-up 后实测余额 ≥ `60.02 ZEC`** |

`3.001 = send_zec(3.0) + 冻结手续费/安全余量(0.001)`；实测每轮 ironwood 净流出
**3.00020 ZEC**（3.00000 转出 + 0.00020 手续费），保守量覆盖得住。

### 14.2 实测证据（`4f7983d`，只作缺陷证据，不作正式统计）

| 观测 | 值 | 说明 |
| --- | --- | --- |
| 恢复快照后 ironwood | `11.2482 ZEC` | 服务在线后首读 |
| 一次 `--fund-zec 40` 后 | `51.87245 ZEC` | 实际到账 `+40.62425`（40 主输出 + 0.62425 找零落进 ironwood） |
| 距 `60.02` 缺口 | `8.14755 ZEC` | 原门槛已不满足 |
| 距 `63.021` 缺口 | `11.14855 ZEC` | 勘误门槛下缺口更大 |
| warm-up 后 ironwood | `48.87225 ZEC` | 净 `−3.00020 ZEC`；sapling `+3.00000` |
| warm-up 后 sapling | `82.3748 ZEC` | 与净流出对账一致 |

结论：**不是「补 8.15 ZEC」就能修好**——那只补上 20 轮口径的缺口，仍遗漏 warm-up 消耗。
因此按用户决定，**不补差、不在当前链状态上继续**，改为恢复快照后整批重跑。

### 14.3 修正后的冻结规则（本节是新的执行依据，覆盖 §8.2 与 §11.1 第 5 项）

```
恢复原始快照（h=32297）
  → ① 记录初始余额（两侧池，写入产物）
  → ② 执行且仅执行一次：python3 /mnt/d/Research/z3/wsl/fund_pool.py --fund-zec 55
  → ③ 记录实际到账余额（不得假定到账额）
  → ④ 执行完整 G0：G0-G 判据改为 实测 ironwood ≥ 63.021 ZEC
        （= 1 × warm-up 需求 + 20 × valid-round 需求 + 冻结手续费预算）
  → ⑤ G0 必须 8/8 PASS，否则停
  → ⑥ 重跑 1 轮 warm-up，并核验 warm-up 后实测余额 ≥ 60.02 ZEC
  → ⑦ 只有 ⑥ 通过，才启动正式 20 个有效轮次
```

三条硬约束：

1. **`55 ZEC` 是测量前的一次性准备动作，不是统计变量**：不进任何时间窗口、不与任何性能量做比较、
   不假定它必然到账多少。**G0 判据永远是「实测余额 ≥ 63.021 ZEC」**，注资金额本身不是判据。
   选 55 而不是精确补到 63.021，是测量前选定的安全余量，理由写在本节，事后不改。
   （快照 sapling 池 119.998 ZEC，足以支撑该次跨池注资。）
2. **只允许一次注资**：G0 通过后、warm-up 后、20 轮期间**任何时刻都不得再注资**；
   不得降 `send_zec`、不得改 `minconf`/`policy`/`fund_source`/收款 UA/挖块数。
   违反即按 §9.3 整批作废，不拼接。
3. **warm-up 放行门禁是新增的最终余额门禁**：warm-up 成功后实测 ironwood 仍须 ≥ `60.02 ZEC`
   （= 正式 20 轮冻结需求）。它独立于 G0-G，二者不得互替、不得用 `55` 的名义值推算。

### 14.4 旧批次 `4f7983d` 的资格（superseded）

| 项 | 判定 |
| --- | --- |
| 身份 | **superseded for batch execution / measurement-chain validation only** |
| 原始字节 | **全部保留、不删不改**（四份 G0 报告、注资日志、warm-up 全部 CSV/JSON、`PHASE12_RAW.txt`、`EXECUTION_NOTES.md`） |
| 仍可引用 | 执行/测量链路事实：WSL2 会回收最后一个会话需常驻 keeper、`taskset -pc 0` 才是正确输入语法（§11.2 的 `0x1` 是输出格式）、`pgrep -x` + `/proc/PID/exe` + SHA256 有效、50 ms prove 窗口采样可覆盖、`T_wall` 与四段求和一致、三门禁链路可用 |
| **不得引用** | `1535.582 ms`、`94.4865%`、`RSS 417888 KB` 及该批任何时间/RSS 数值——**不得当作正式 baseline 统计结果**，不得并入 20 轮，不得与正式批次并列 |
| 不得写成 | 「测量链路验收失败」；恰恰相反，**Phase 1/2 的测量链路验收成功**，停下是因为 G0-G FAIL 与预算缺陷 |

### 14.5 结果节编号顺延（因 §14 已被勘误占用）

§13 原规划的结果追加节整体顺延一位，其它内容不变：

| 原编号（§13） | 实际编号 | 内容 |
| --- | --- | --- |
| §14 | **§15** | 环境与 G0 复核（含单次注资与 8/8 PASS） |
| §15 | **§16** | 时间结果 |
| §16 | **§17** | RSS 结果与 R1/R2/R3 档判定 |
| §17 | **§18** | 正确性门禁汇总 |
| §18 | **§19** | 与 §7 允许/禁止清单逐条对账 |
| §19 | **§20** | 结论（只写允许的五条） |

### 14.6 勘误不允许外溢的部分

- 不改 §0–§13 的**任何**数字、阈值、判据、措辞；原 `60.02 ZEC`、`--fund-zec 40`、
  §11.1 第 5 项、§12.2「§14 起追加」等历史文字**原样保留**，只按本节覆盖执行。
- 不新增性能结论；§7 的 5 条允许 / 12 条禁止**一条不改**；不产出任何 F4-A 加速数字。
- 新增两条禁止措辞（并入引用纪律）：
  ① 不得把 `63.021 ZEC`（启动门槛）与 `60.02 ZEC`（warm-up 后放行门槛）互替；
  ② 不得把 `--fund-zec 55` 或它的实际到账额写成实验结论或性能变量。
- 绝对毫秒仍只在本批次内解释（§0 纪律 2）；`4f7983d` 的毫秒属**另一批次**，不得并列。
- 冻结材料 doc 26 v1.0 FROZEN / errata-1/2、doc 27、INDEX 的 F4-A 结论区、README 的 F4-A 行、
  tag `f4a-closed-2026-11-05`：**全部不动**。

### 14.7 版本与校验记录

| 项 | 值 |
| --- | --- |
| 文档版本 | **v1.0（预注册正文）+ Errata-1（本节）**，2026-11-06 |
| §0–§13 前缀字节数 | `36366` bytes |
| §0–§13 前缀 SHA256 | `c3d8f999e910e471c218777dc62680994c596324829b1eb16703055a6e666de1`（追加后复验须逐位相等） |
| 提交方式 | **单独提交**，只含本勘误与旧批次资格标记，不夹带实验代码或结果 |
| 与正文冲突时 | 以本节为准（§13 勘误通道）；原句保留在 §8.2 / §11.1 作历史记录 |
| 后续改动 | 只允许再起 §21+ 勘误或按 §14.5 追加结果节 |
