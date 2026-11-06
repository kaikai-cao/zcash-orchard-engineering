# 31. 真实 Zcash PCZT 环境 · 显式 1-worker / 全线程 CPU0 对齐基线（预注册）

**状态**：**预注册，测量前冻结（2026-11-06）。本文件提交时一轮都没有跑。**
没有恢复快照、没有启动/重启服务、没有设置任何环境变量、没有改 affinity、没有发交易、
没有构建或部署任何代码。**执行须经用户单独批准；本轮只交付文档。**

**本实验的身份（必须整句引用）**：

> **真实 Zcash PCZT 环境在显式 1-worker / 全线程 CPU 0 配置下的单臂性能基线**

**不是**（三条，任何报告里都不得含糊）：

> ~~F4-A 的真实端到端 A/B 实验~~
> ~~线程并行度对 prove 耗时影响 X% 的因果测量~~
> ~~把冻结 20 轮（doc 29 §16）与本地 1P 拉齐后的可比对照~~

**上游**：[`28_real_e2e_readonly_audit.md`](28_real_e2e_readonly_audit.md)（路线 B 前提被证伪）、
[`29_real_e2e_baseline.md`](29_real_e2e_baseline.md)（默认 28-worker 单臂基线，
其 §21 Errata-2 更正了绑核口径）、
[`30_real_e2e_thread_config_readonly.md`](30_real_e2e_thread_config_readonly.md)
（只读确认：主线程绑核 + 28-worker 并行池，commit `b7a90b0`）。

---

## 0. 为什么单独起这一份，而不是改 doc 29

用户 2026-11-06 决定：**历史结果全部保留，不重跑、不覆盖、不追求数字一致。**

| 对象 | 处置 |
| --- | --- |
| F4-A 原有 1P / 8P 结果（doc 24/25/26/27） | 继续作为历史实验结果，**不动** |
| doc 29 冻结 20 轮（1480.0 ms、94.94% / 95.35%、R3、20/20） | 继续作为**默认 28-worker 配置下**的基线，**不动一个字节的结果** |
| 新发现的线程配置事实 | 以 doc 29 **§21 Errata-2** 追加，不改原始记录 |
| 本实验 | **独立预注册 + 独立结果目录 + 独立提交**；不改 doc 29 的任何判据与结果 |
| 真实环境 F4-A A/B（路线 B） | **不启动**：真实二进制 `halo2_proofs 0.3.2` vs 本地补丁 `0.4.0`，线程统一不解决版本差异，仍需独立构建工程。H8 / F4-B 保持暂缓 |

---

## 1. 实验目的（单臂描述性，不是 A/B）

在**同一官方预编译环境**、**同一 PCZT 场景**、**同一统计口径**下，把并行配置从
「未设 `RAYON_NUM_THREADS` + 仅主线程 CPU 0」改为
「启动前 `RAYON_NUM_THREADS=1` + 全线程 CPU 0」，测出该配置下的：

1. `T_build` 与四个 stage 的 median / IQR / min / max；
2. `prove` 在 `T_build` 中的占比（两种口径分开标注）；
3. `pczt_prove` 窗口内 wallet 的 `VmRSS` 分布与 R1/R2/R3 档；
4. 正确性门禁通过情况；
5. **配置本身的 E1-M 证据**（逐线程 affinity、env、线程数、启动期 worker 组规模）。

**附带目的**：为本地 F4-A 的 1P（`--mask 0x1` + `RAYON_NUM_THREADS=1`，
见 `results/transaction_e2e/baseline/environment.md` §2）提供一个**并行度更接近**的真实环境参照点。
注意只是「更接近」，不是「已对齐」：OS、CPU 拓扑、Halo2 版本、调用链都不同（§7）。

---

## 2. 冻结配置（测量前逐项钉死）

### 2.1 唯一允许改变的量：并行配置

| 项 | doc 29 冻结 20 轮 | **本批次** |
| --- | --- | --- |
| `RAYON_NUM_THREADS` | 未设置 | **启动前注入 `1`** |
| CPU affinity | `taskset -pc 0 <pid>`（仅主线程） | **启动命令外层 `taskset -c 0`**，全线程继承 |
| Rayon 全局池 | 默认 28 workers | **目标 1 worker**（由 env + 全线程单核 pin 共同约束；实测口径见 §6.2 观察项，不宣称已证明确切数量） |
| tokio 运行时 | 随 `available_parallelism()=28` | 随 `available_parallelism()=1` 收缩 |
| Halo2 / Orchard / zallet-zaino / zebrad | `0.3.2` / `0.15.3` / beta.3 / 6.2.3 | **不变** |
| PCZT 场景、参数、调用链、统计量 | 见 §3–§5 | **不变** |
| RSS 采样、PID 三重识别、门禁 | 见 §5–§6 | **不变**（只把 G0 的 CPU 判据换成逐线程版） |

**必须写明的耦合限制（E1-S，来自 `rayon-core 1.13.0` 的
`get_num_threads()` → `std::thread::available_parallelism()`）**：
全线程 pin 到 CPU 0 本身就会让 `available_parallelism()` 返回 1，即使不设 env 也只会创建 1 个 worker。
因此本批次**无法区分**「env 生效」与「affinity 生效」，也**无法分离** Rayon 池与 tokio 运行时的变化。
本实验冻结的是**一个联合对齐配置**，不是「Rayon 线程数单因子干预」。§7 的禁止清单由此派生。

### 2.2 注入方式（必须在进程启动前，且不得改任何脚本文件）

`snapshot_restore.sh --yes` 自己会「停服 → 备份 → 解包 → `setsid nohup` 重启两个服务」
（`wsl/snapshot_restore.sh:205–214`）。它启动的子进程继承调用 shell 的环境变量与 affinity，
因此**不需要改脚本、也不需要 `regtest_run.sh`**（后者 `pkill -9` 并额外挖块）：

```bash
SNAP=/mnt/d/Research/z3/snapshots/z3_regtest_h32297_20261029_145710.tar.gz
bash /mnt/d/Research/z3/wsl/snapshot_restore.sh --check "$SNAP"      # 只读深校验
RAYON_NUM_THREADS=1 taskset -c 0 \
  bash /mnt/d/Research/z3/wsl/snapshot_restore.sh --yes "$SNAP"
```

- `RAYON_NUM_THREADS=1` 与 `taskset -c 0` **同时**作用于由该 shell 派生的 **zebrad 与 zallet-zaino**；
  zebrad 不在 `T_build` 内，但 broadcast / mine / confirm 门禁可能变慢，须记录、不计入指标。
- **禁止**事后 `export`、`/proc` 写入、或对已运行进程补 `taskset -pc 0 <pid>`（只改主线程，已被 doc 30 证伪）。
  若确需在启动后修正，只能用 `taskset -a -pc 0 <pid>`，并在 `meta.json` 记录为偏差。
- 批次中途**不得改 mask、不得改 env、不得重启服务**；重启即整批作废（§6.4）。
- 恢复脚本会把当前 `data/zebra-state` / `data/zallet` 移到 `<dir>.bak.<时间戳>` **保留后再解包**，
  不删除。`results/real_e2e_baseline/**` 的冻结结果文件不受影响（它们是磁盘上的 CSV/JSON，
  不是链状态）。这一点如实写入 `meta.json`，避免把「恢复快照」误读成「覆盖历史结果」。
- 保持 WSL2 常驻 keeper（最后一会话退出会回收实例），沿用 doc 29 §15.3(3) 已验证做法。

### 2.3 与「全线程 CPU 0」等价的 G0 判据

`meta.json` 与 G0 报告必须记录，且 G0-D 判据为**逐线程**：

```text
/proc/<wallet_pid>/task/*/status 中每一个 tid 的 Cpus_allowed_list == "0"
/proc/<zebrad_pid>/task/*/status 中每一个 tid 的 Cpus_allowed_list == "0"
Threads 总数、comm 分布、/proc/<pid>/environ 中 RAYON_NUM_THREADS=1
```

任何线程出现 `0-27` → **G0 FAIL，整批不启动**。只读 `/proc/PID/status` 不算通过。

---

## 3. 调用链与精确时间窗口（与 doc 29 §3 逐字同义）

```text
pczt_create → pczt_sign → pczt_prove → pczt_extract   [计时窗口 T_build]
  → sendrawtransaction → generate → confirmations      [仅正确性门禁，不计入 T_build]
```

- `T_build = create + sign + prove + extract`，终点是 `pczt_extract` 返回。
- 另记 `T_wall` 作核对；`T_wall − T_build` 超 5% 须报告，不静默改口径。
- broadcast / mine / confirm / `wait_sync` **不计入** `T_build`。

---

## 4. 固定交易场景（与 doc 29 §8.1 完全同参）

| 参数 | 冻结值 |
| --- | --- |
| `rounds` | **20**（有效轮次）+ **1** 轮 warm-up |
| `send_zec` | **3.0** |
| `minconf` | **1** |
| `policy` | `AllowRevealedAmounts` |
| `fund_source` | `orchard`（参数名，实际命中 ironwood 池） |
| 收款方 | 同一个 `SAP_UA`，循环外解析一次复用 |
| `mine_blocks` | **2**（只在正确性门禁里） |
| `pczt_prove` timeout | 1800 s |

- 主形态门禁：`ironwood.actions == 2` 且 `orchard.actions == 0`；出现其它值标 `shape_changed` 并不计入主统计。
- **禁止**按 action 数 / spend 数分组比较并声称规模趋势；`signed` 只作记录字段。
- **禁止**把 `fund_source="orchard"` 读成 "Orchard Spend"。
- 测量期间**不得**调整任何参数；确需调整 → 整批作废并按新预注册重跑（§6.4）。

---

## 5. 资金、注资与余额门禁（沿用 doc 29 §14 Errata-1 已实测路径）

```text
单轮冻结需求 = 3.001 ZEC = 300 100 000 zat
覆盖轮数     = 21（1 warm-up + 20 valid）
G0 启动门槛  = 63.021 ZEC = 6 302 100 000 zat
warm-up 后放行门槛 = 60.02 ZEC = 6 002 000 000 zat
实测每轮 ironwood 净流出 = 3.00020 ZEC（doc 29 §15.2，20 轮闭合已验证）
```

- 恢复快照后先实测 `z_getbalanceforaccount` 的 ironwood 余额，写入 `meta.json`。
- 若 `< 63.021 ZEC`：**允许且只允许一次**
  `python3 /mnt/d/Research/z3/wsl/fund_pool.py --fund-zec 55`
  跨池注资；注资后把金额与两侧**实测**余额写入 `meta.json`，**不按名义 55 推算到账额**
  （doc 29 §15.1：一次 `--fund-zec 55` 实际到账 `+56.249 ZEC`）。
- 注资是测量前的链准备，不计入任何轮次；其挖块与链高推进如实记录。
- **禁止第二次注资**、禁止改用更小的 `send_zec` 规避、禁止减少轮数。
  若一次注资后实测仍 `< 63.021 ZEC` → **整批不启动**，如实报告缺口，不临时改设计。
- warm-up 之后独立执行放行门禁：实测 ironwood ≥ `60.02 ZEC`；两个门槛不得互替。
- 批次结束做闭合核对（**以注资后实测余额为起点**）：
  `Δironwood ≈ −21 × 3.00020 = −63.0042 ZEC`、`Δsapling ≈ +21 × 3.0 = +63.0 ZEC`、
  链高增量 `= 42` 块（21 轮 × 2 块）；偏差须报告，不静默修正。

---

## 6. 测量链、G0 与排除规则

### 6.1 测量脚本（从已冻结的工具派生，改动范围冻结）

基线：`results/real_e2e_baseline/tools/baseline_measure_v2.py`
（其测量路径已与 `baseline_measure.py` 逐函数 AST 比对一致，doc 29 §15.3(1)）。
**只允许五类改动**：

1. `cpu_allowed()` 改为逐线程采集（§2.3）；
2. `meta.json` 增加环境字段：`RAYON_NUM_THREADS`、逐线程掩码摘要、`Threads` 总数、`comm` 分布、
   配置标签 `aligned_1worker_cpu0`、本预注册文件的 SHA256；
3. 输出目录改为 `results/real_e2e_1worker/run_<ts>/`；
4. G0 判据项替换为 §6.2 的八项；
5. 放行门禁脚本的 gate 注释 / 路径 / 输出目录。

**不得改动**：RPC 方法名与参数顺序、交易参数、阶段顺序、`T_build` 定义、
RSS 采样间隔（50 ms）与 phase + monotonic boundary、`N_rss` 三档阈值、
统计量算法（`statistics.median` / `quantiles(n=4, method='inclusive')`）、
`prove_share` 两种口径、`pgrep -x` + `/proc/PID/exe` + SHA256 三重 PID 识别、
`pczt_inspect` 形态字段。脚本副本与所用 commit 写入 `meta.json`。

### 6.2 G0 八项（全部 PASS 才进入 warm-up / 20 轮）

| ID | 判据 |
| --- | --- |
| G0-A | `uname -r` 记录（记录项，不作跨会话比较） |
| G0-B | 三个二进制 SHA256 == 冻结指纹（`baf0b2cd…` / `523bf86a…` / `e602e4a3…`） |
| G0-C | 两个 PID 由 `/proc/PID/exe` + SHA256 精确识别（禁用 `pgrep -f` fallback） |
| **G0-D** | **逐线程** `Cpus_allowed_list == "0"`：wallet 与 zebrad 的每一个 tid；记录线程数与 `comm` 分布 |
| **G0-E** | `/proc/<wallet_pid>/environ` 含 `RAYON_NUM_THREADS=1`，且无 `RAYON_RS_NUM_CPUS`；记录 zebrad 同项 |
| G0-F | 18232 / 40232 可连、`getwalletstatus` 同步判据成立、链高 ≥ 32257 |
| G0-G | 实测 ironwood ≥ `63.021 ZEC`（21 轮） |
| G0-H | `bench` 账户存在、`pczt_inspect` warm-up 形态 `ironwood.actions=2 / orchard.actions=0` |

**观察项（不作硬判据，E2/E3）**：启动期一次性创建的同名 worker 组规模。
全线程单核时 `available_parallelism()=1`，预期不再出现 doc 30 §1.4 那种「28 个同批 worker」；
实际线程总数与分组如实记录。身份判定标 **E3**（未用调试器读
`rayon_core::current_num_threads()`），**不得**把「没有 28 个」写成「已证明恰有 1 个 Rayon worker」。

### 6.3 warm-up 与放行

- 同一进程、同一配置下 1 轮 warm-up（完整含门禁），数据落盘但**不计入 20 轮、不进任何统计**。
- warm-up 后独立执行 **8/8 放行门禁**（含 W-7 实测余额 ≥ `60.02 ZEC`）。
- warm-up 之后若进程重启 → 整批作废。

### 6.4 有效轮次与排除

- 20 个**有效**轮次、单臂、不交错。三门禁 + 形态 + PID 恒定；失败轮标 `invalid` 并补足，
  但**不得拼接批次**、不得因「看起来偏慢」删除（doc 29 §16 的 `r01` 先例）。
- 每轮记录 `pid_changed`；PID/exe/SHA 恒定。
- 无异常不得剔除；不报标准差、p 值、置信区间，不做假设检验。

---

## 7. 允许与禁止得出的结论（测量前冻结）

### 7.1 允许（正列表，只有这六条）

1. 在**本批次、本配置、本场景**下 `T_build` 与四段的 median / IQR / min / max。
2. `prove` 在 `T_build` 中的占比：主口径 `median(逐轮比值)`、辅口径
   `median(prove)/median(build)`，**二者分开标注、不可互算**。
3. `pczt_prove` 窗口内 wallet 的 `VmRSS` 分布与轮间噪声范围。
4. 按 doc 29 §5.5 原阈值判 R1/R2/R3，得出「当前进程级采样能否分辨 6 MiB 量级」的**方法论结论**。
5. 正确性门禁通过情况（20/20 或失败明细）与环境版本记录。
6. **配置事实（E1-M，不是性能结论）**：逐线程 affinity、env 注入、线程数与 `comm` 分布、
   三个二进制 SHA、链高与资金闭合。

### 7.2 禁止（负列表，违反即整批结论作废）

| # | 禁止措辞 / 推论 | 原因 |
| --- | --- | --- |
| 1 | 「F4-A 在真实 Zcash / 真实交易中加速 X%」（任何形式） | 没有 B 臂，doc 29 §7.2 #1 |
| 2 | 「F4-A 在真实环境的内存代价是 Y MiB」/「证明 F4-A 不增加内存」 | 没有 B 臂 |
| 3 | 「已在真实交易流程中验证 F4-A」/「F4-A 具备外部有效性」 | 未部署，二进制仍是 0.3.2 |
| 4 | **「线程并行度变化导致 prove 变慢/变快 X%」**（含点估计、区间、E3 推算） | 单臂，且无同会话参考臂（§8） |
| 5 | **把本批次绝对毫秒与 doc 29 冻结 20 轮并列、相减、算比值或百分比** | §0 纪律 2 / 用户四条边界「跨会话绝对毫秒不比较」 |
| 6 | 把 `1480.0 ms` / `94.94%` 当作本批次的对照基准或预期值 | 不同配置、不同批次 |
| 7 | 把差异单独归因给「Rayon 线程数」这个单因子 | env 与全线程 pin 耦合，tokio 运行时同时收缩（§2.1） |
| 8 | 把 doc 26 的 −22.00% 乘到本批次 `T_build` 或任何 RPC 总时间 | 版本 / 调用链 / 测量对象都不同 |
| 9 | 把本批次称作「真实 1-thread baseline」/「单线程进程」 | 只是并行池为 1 worker；服务仍是多组件多线程（tokio、zebrad） |
| 10 | 把本批次称作「与本地 1P 完全对齐 / 已统一」 | Windows 全进程 pin vs Linux、Halo2 0.4.0 vs 0.3.2、`create_proof` vs `pczt_prove`、RPC/序列化/钱包逻辑仍在 |
| 11 | 把 `pczt_prove` 墙钟等同于 `create_proof` / quotient 耗时；把 broadcast/mine/confirm 计入 `T_build` | doc 29 §3.3 / §7.2 #4, #6 |
| 12 | 用 `VmHWM` / 全局峰值充当机制代价；把 R3 写成「代价为 0」 | doc 29 §5.4 / §5.5 |
| 13 | 用辅口径冒充主口径；把单批 20 轮 IQR 写成置信区间或显著性结论 | doc 29 §4.5 / §4.3 |
| 14 | 把 `fund_source="orchard"` 读成 "Orchard Spend"；按 action 数分组比较声称趋势 | doc 29 §8.3 |
| 15 | 把本批次结果并入冻结 20 轮、或改写 `results/real_e2e_baseline/**` 任何文件 | 用户「保留历史结果」决定 |
| 16 | 第二次注资、减轮次、改 `send_zec`、事后补 env / affinity | §5 / §2.2 |

**可以并列展示但不能计算**：允许在报告里用一张表把「冻结 20 轮（默认 28-worker）」与
「本批次（显式 1-worker / 全线程 CPU0）」作为**两个不同配置下的独立基线**并列列出各自的
median / IQR / n / 配置标签，且必须同页注明「不同批次、不同配置，绝对毫秒不可互比、不可计算百分比」。

---

## 8. 「线程并行度影响有多大」这一问：本批次**不能**回答（必须如实前置）

用户提出这一步的实际问题是「线程并行度变化对真实 PCZT proving 耗时究竟有多大影响」。
按 §7.2 #5 与 doc 29 §0 纪律 2，**单臂新批次 + 冻结 20 轮（另一批次）无法给出合法的效应量**：
两批不是同会话配对，内核漂移、链状态、进程冷启动、资金/注资路径都不同。

因此本预注册先把可回答的部分做完（一个干净的 1-worker 对齐基线），
并把该问题留给用户已授权范围内的**后续 Stage 2**，方案与代价如下，执行前须单独批准并
追加本文件的修订节，**不得在本批次实施期临时改设计**：

| 方案 | 设计 | 能否回答该问 | 代价 / 阻塞 |
| --- | --- | --- | --- |
| **S1（默认，本文件）** | 单臂 1-worker，20+1 轮 | **不能**，只给基线 | 一次 `--fund-zec 55` 可行，已被 doc 29 §15.1 实测 |
| **S2 同会话参考臂（推荐）** | 同一 WSL 实例 / 同一内核 / 同一二进制 / 同一快照 / 同一测量脚本内，**顺序两臂**：先默认 28-worker 参考臂，重启为 1-worker 对齐臂，各 1 warm-up + 各 N 轮；两臂各自独立统计，报告**描述性中位数比值** | **能给出描述性联合配置效应量**（仍非单因子因果、非配对 A/B、不得报 CI/p 值） | 需要一次服务重启 + 更大资金。两臂各 20 轮 = 42 轮 → G0 需 `126.042 ZEC`，而快照 sapling 仅 `119.998 ZEC`，**一次注资无法达到**；若坚持 S2，须把每臂降到 10 有效轮（共 22 轮 → `66.022 ZEC`，一次 55 可行）或另立资金准备方案，并在修订节重新冻结 n |
| **S3 只报方向** | 两批并列展示、不算任何百分比 | 只能定性 | 无需额外实验；但用户要的「有多大」仍无答案 |

**S2 的额外口径**（若批准，须写入修订节）：两臂之间只变「env + 全线程绑核」这个**联合配置**；
默认臂不得复用冻结 20 轮；比值须写作「本会话内两配置的中位数描述性比值」，
不得写成「Rayon 线程数导致 X%」、不得写成 F4-A 加速、不得乘 −22%。

---

## 9. 产物与冻结记录

- 结果目录：`results/real_e2e_1worker/run_<ts>/`，结构与 doc 29 §12.1 相同
  （`meta.json`、`rounds.csv`、`stage_timings.csv`、`summary.csv`、`rss_*`、`gate.csv`、
  `invalid_runs.csv`、`BATCH_RAW.txt`、`BATCH_NOTES.md`）。
- `meta.json` 必须回引本预注册文件的 SHA256 与 commit id、脚本 commit、逐线程 affinity 摘要、
  `RAYON_NUM_THREADS`、三个二进制 SHA、注资与余额记录、warm-up 轮次、配置标签。
- **不写** `results/real_e2e_baseline/**` 的任何文件；不改 doc 26/27/28/29/30、
  INDEX、README、tag `f4a-closed-2026-11-05`。
- 本文件**单独提交**；commit id 与文件 SHA256 在提交后由会话记录，并在实测 `meta.json` 回引。
- `b7a90b0`（doc 30）与本次提交按用户决定**先保留本地**，等勘误一起检查后再推送。

---

## 10. 本轮未执行（边界清单）

- 未恢复快照、未停止/启动/重启服务、未设置或注入任何环境变量、未改任何进程的 affinity；
- 未发交易、未挖块、未注资、未跑 G0 / warm-up / 任何轮次；
- 未改任何启动脚本、测量脚本、配置文件；未装 Rust、未拉源码、未移植 F4-A、未重建 prover；
- 未启动路线 B / H8 / F4-B；未改 F4-A 冻结材料、INDEX、README、tag；
- 本轮唯一的写操作是新增本文件与 doc 29 的 §21 追加节（均为文档）。
