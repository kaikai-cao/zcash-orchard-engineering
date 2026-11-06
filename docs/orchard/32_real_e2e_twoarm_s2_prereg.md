# 32. 真实 Zcash PCZT 环境 · 同会话两臂并行配置对照（S2 预注册）

**状态**：**预注册，测量前冻结（2026-11-06）。本文件提交时一轮都没有跑。**
没有恢复快照、没有停止/启动/重启任何服务、没有设置或注入任何环境变量、没有改任何进程的
affinity、没有发交易、没有挖块、没有注资、没有构建或部署任何代码、没有新建任何测量/启动脚本。
**执行须经用户单独批准；本轮只交付文档。**

**本实验的身份（必须整句引用）**：

> **同一实验会话内、两种完整运行配置（显式 1-worker / 全线程 CPU 0 与默认 28-worker / 仅主线程 CPU 0）顺序两臂的真实 PCZT 描述性对照**

**不是**（任何报告里都不得含糊）：

> ~~F4-A 的真实端到端 A/B 实验~~
> ~~Rayon 线程数从 1 到 28 的因果效应测量~~
> ~~doc 29 冻结 20 轮的跨会话性能比较或「复现」~~
> ~~与本地 F4-A 1P 配置「已完全对齐」的验证~~

**上游**：[`28_real_e2e_readonly_audit.md`](28_real_e2e_readonly_audit.md)（路线 B 前提被证伪）、
[`29_real_e2e_baseline.md`](29_real_e2e_baseline.md)（冻结 20 轮；§14 Errata-1 资金勘误、
**§21 Errata-2 线程/绑核配置事实勘误**）、
[`30_real_e2e_thread_config_readonly.md`](30_real_e2e_thread_config_readonly.md)（只读确认，commit `b7a90b0`）、
[`31_real_e2e_1worker_prereg.md`](31_real_e2e_1worker_prereg.md)（**S1 单臂预注册，保留为未执行文档，本文件不覆盖、不修改**）。

---

## 0. 用户决定与本文档定位

用户 2026-11-06 决定：**选 S2，不执行 S1；先把 S2 预注册设计完整，再做文档审阅。**

| 事项 | 决定 | 本轮落实 |
| --- | --- | --- |
| doc 29 §21 Errata-2 | **原样保留，进入文档审阅** | 一个字节未改（§12.4 校验） |
| doc 31（S1 单臂） | **保留为未执行的独立预注册，不直接启动** | 一个字节未改，不标 superseded |
| 下一份预注册 | **按 S2 另建文档，不覆盖 doc 31** | 本文件 `docs/orchard/32_…` |
| 实验 | **暂不执行** | 未起服务、未跑轮次 |
| Git 推送 | **暂不推送** | 本地提交，领先 `origin/main` 不推 |
| H8 / F4-B / 路线 B | **继续保持关闭** | 未启动，§13 重申 |

**选 S2 的理由（用户口径）**：S1 只能得到「单核约束配置下的新基线」，仍无法回答最初关心的
「并行配置究竟带来了多大影响」；S2 在同一会话内给出两臂，能给出**描述性联合配置差异**。
**但 S2 的边界必须前置**：测的是两种**完整运行配置**的差异，不是 Rayon 线程数的单因素效应（§4.3、§11）。

---

## 1. 术语与引用口径（本文件及后续材料的统一约定）

### 1.1 历史冻结 20 轮的统一称谓

引用 `results/real_e2e_baseline/run_20261106_113925/` 的 20 轮时，**整句写作**：

> **28-worker 默认并行池、主线程 CPU-affinity=0 的真实 PCZT 基线。**

- **禁止简称「28 线程基线」「28 线程配置」**：28 是 **Rayon worker pool 的规模**（doc 30 §1.4
  启动期一次性创建的 28 个同批 worker，身份标 E3），**不代表任一时刻都有 28 个 worker 在执行任务**，
  也不等于进程总线程数（正式 20 轮期间按 `starttime` 重建至少 53 个线程：1 主线程 + 24 tokio + 28 pool）。
- doc 29 §21.3 的冻结整句「主线程 CPU-affinity=0（非全线程）、`RAYON_NUM_THREADS` 未设置、
  启动期默认 28-worker 并行池」是同一事实的另一种表述，**doc 29 §21 原样保留，不修改**；
  本节只约定后续材料的引用措辞，两者不冲突、不得互相「更正」。
- 单独写「CPU-affinity=0」必须带「仅主线程」限定；禁止「CPU0 单核基线」「1-thread baseline」「单线程基线」。

### 1.2 两臂的称谓

| 臂 | 冻结标签 | 报告用整句 |
| --- | --- | --- |
| A | `armA_1worker_allthread_cpu0` | 显式 `RAYON_NUM_THREADS=1` / 全线程 CPU 0 配置臂 |
| B | `armB_default_pool_mainthread_cpu0` | 默认并行池 / 仅主线程 CPU 0 配置臂（本会话内重跑） |

- **B 臂不是冻结 20 轮的复跑，也不是它的替代**：B 臂是本会话内**新跑的 11 笔交易**。
  冻结 20 轮只作为「B 臂要复现哪一组配置特征」的**证据来源**，**不作性能对照**（§4.4、§10.2）。
- 禁止把 A 臂称作「真实 1-thread baseline」「单线程进程」；A 臂只是并行池为 1 worker，
  tokio / zebrad / 多组件仍是多线程。

---

## 2. 实验目的与可回答 / 不可回答

### 2.1 可回答

在**同一实验会话**、**同一官方预编译环境**、**同一 PCZT 场景**、**同一测量链与统计口径**下，
顺序测量两种完整运行配置，各自给出：

1. `T_build` 与四个 stage 的 median / IQR / min / max（每臂 n=10）；
2. 每臂 `prove / T_build` 的主、辅两种口径（分开标注）；
3. 每臂 `pczt_prove` 窗口内 `VmRSS` 分布、轮间噪声与 R1/R2/R3 档；
4. 每臂正确性门禁通过情况；
5. **两臂 `T_build` 边际中位数的描述性比值**（唯一允许的跨臂量，§9）；
6. 两臂各自的**配置 E1-M 证据**（逐线程 affinity、env、线程数与 `comm` 分布、二进制 SHA、
   链高与资金闭合、配置切换记录）。

### 2.2 不可回答（必须前置写明）

| # | 问题 | 为什么本批次回答不了 |
| --- | --- | --- |
| 1 | 「Rayon 从 1 增到 28 使 prove 快/慢 X%」 | 联合配置差异，非单因子（§4.3、§11） |
| 2 | 「并行度本身带来 X%」 | 同上；env、affinity、tokio、进程重启耦合 |
| 3 | 「与 doc 29 冻结 20 轮相比变化 X%」 | 跨会话，§0 纪律 2：绝对毫秒不比较 |
| 4 | 「F4-A 在真实交易中加速 X% / 内存代价 Y MiB」 | 二进制仍是 `halo2_proofs 0.3.2`，无 F4-A B 臂 |
| 5 | 「与本地 1P 已对齐，−22.00% 可外推」 | 版本 / 调用链 / OS / CPU 拓扑不同（§13） |
| 6 | 「差异具有统计显著性 / 置信区间」 | n=10、无配对、顺序固定，首次描述性比较（§9.4） |

---

## 3. 「同一实验会话」的严格定义

S2 的全部合法性来自「同会话」三个字，边界必须在测量前钉死：

| 项 | 冻结要求 |
| --- | --- |
| 实例与内核 | 同一个 WSL2 **Ubuntu** 实例（`wsl.exe -d Ubuntu`；默认发行版是 `docker-desktop`）、同一 `uname -r`（只读记录，不作跨会话比较） |
| 二进制 | `zebrad` / `zallet` / `zallet-zaino` 三个 SHA256 与 doc 29 §2.1 冻结指纹逐位一致，两臂各自回读 |
| 快照 | **只在阶段 0 恢复一次** `z3_regtest_h32297_20261029_145710.tar.gz`；阶段 1 **不得**再恢复 |
| 链数据 | 阶段 1 **不备份、不解包、不替换** `data/zebra-state` 与 `data/zallet`；链高与两侧余额在切换前后必须连续 |
| 脚本 | 同一份测量脚本与同一 commit（§8.1），两臂共用；`meta.json` 记录脚本 SHA256 |
| 时间 | 连续时间窗口，**不跨天**、不在两臂之间插入其它负载；keeper（`sleep 86400`）保持实例存活 |
| 进程重启 | **阶段 1 的配置切换是唯一允许的重启**；每臂内部不得再重启 |

- **每臂内部** PID / `/proc/PID/exe` / SHA256 恒定，任何变化判该臂 `pid_changed` 并整臂作废；
  **跨臂 PID 变化是预期**，写入 `switch/config_switch.json`，不适用 doc 29 §9.3 的「整批作废」。
- 切换前后必须记录并核对：时间戳、链高、两侧池余额、两进程 PID/exe/SHA、逐线程 affinity 摘要、
  env 快照。切换窗口内**不得**发交易、挖块、注资或改参数。
- **整批作废条件**：WSL 实例被回收、内核变化、再次恢复快照、跨天、二次注资、中途改配置/参数/轮数/
  顺序、把两臂拆到两个会话、拼接批次。作废后按新预注册重跑，不拼接。

---

## 4. 两臂配置冻结（测量前逐项钉死）

### 4.1 唯一允许改变的量：完整运行配置

| 项 | **A 臂** | **B 臂** |
| --- | --- | --- |
| `RAYON_NUM_THREADS` | **启动前注入 `1`** | **未设置**；`/proc/<pid>/environ` 中不得出现 `RAYON_NUM_THREADS`、`RAYON_RS_NUM_CPUS`、`TOKIO`、`OMP`、`NUMEXPR`、`MKL`、`BLAS` 等线程类变量 |
| 启动层 affinity | `taskset -c 0` 包住启动 shell → zebrad 与 wallet **所有线程**继承 `Cpus_allowed_list=0` | 启动命令**不带任何 `taskset`** → 所有线程继承 `0-27` |
| 启动后 pin | **无**；禁止事后补 `taskset -pc` | G0-B 对 **zebrad 与 zallet-zaino 两个 PID 各执行一次 `taskset -pc 0 <pid>`（不带 `-a`）**，与 doc 29 §11.2 / G0-D 字面一致 |
| `available_parallelism()` | **1** | **28**（`nproc=28`，`os.sched_getaffinity(0)=[0..27]`） |
| Rayon 全局池 | 目标 1 worker（**E3**，不作硬判据） | 启动期一次性 28-worker 组（**E3 观察项**，不作硬判据） |
| tokio 运行时 | 随 `=1` 收缩 | 随 `=28` |
| **逐线程 G0 期望** | 两进程**每一个 tid** `Cpus_allowed_list == "0"` | 两进程**各恰好一个** tid（主线程）`== "0"`，其余**全部** `== "0-27"` |
| Halo2 / Orchard / zallet-zaino / zebrad | `0.3.2` / `0.15.3` / beta.3 / 6.2.3 | **同左，不变** |
| PCZT 场景、调用链、`T_build` 定义、统计量、RSS 采样 | 见 §6 / §8 / §9 | **同左，不变** |

### 4.2 证据与版本依据（E1-S / E1-M）

- `rayon-core 1.13.0` 的 `get_num_threads()`：未设 env → `std::thread::available_parallelism()`；
  全线程 pin 到 CPU 0 **本身就让它返回 1**，即使不设 env 也只创建 1 个 worker（doc 30 §1.3 源码，
  `lib.rs:458–486`）。`get_thread_name()` 默认 `None`，worker 继承创建者 `comm`（`lib.rs:489–492`）。
- `taskset -pc 0 <pid>` **不带 `-a` 只作用于主线程**（doc 30 §1.5 用一次性 4 线程进程复核：
  main→`0`，其余 3 个→`0-27`）。旧批次 G0-D 输出确认两个 PID 都执行了这一调用：
  `pid 1940's current affinity list: 0-27 / new: 0`、`pid 2255's current affinity list: 0-27 / new: 0`。
- 二进制静态链接 `rayon 1.12.0` / `rayon-core 1.13.0` / `num_cpus 1.17.0`；旧批次 wallet
  启动后 0.24 s（11:39:49.78）一次性创建 28 个同名 worker，全部存活且 affinity `0-27`。

### 4.3 联合配置限制（本批次最核心的边界）

两臂差异**同时**包含：① `RAYON_NUM_THREADS` 注入与否；② 全线程 CPU 0 vs 仅主线程 CPU 0；
③ `available_parallelism()` 1 vs 28；④ Rayon 池规模；⑤ tokio 运行时规模；
⑥ 服务进程重启与冷启动状态；⑦ 链高、note 集合、wallet 缓存；⑧ 时间顺序与系统负载。

> 因此 **A/B 比值是「两种完整运行配置」的描述性差异，不是 Rayon 线程数的单因子效应**。
> env 与全线程 pin 还彼此耦合（pin 本身就使 `available_parallelism()=1`），
> **无法分离**「env 生效」与「affinity 生效」，也无法分离 Rayon 池与 tokio 运行时的贡献。

### 4.4 B 臂复现规格（用户明确要求，不得简化）

**B 臂复现的是「配置特征」，不是整个旧批次。** 以下五条逐条满足，并以 G0-B **逐线程**回读证明：

| ID | 复现规格 | 验证方式 |
| --- | --- | --- |
| R1 | 启动命令**不含** `taskset`、不含 `RAYON_*` 注入；进程继承干净环境 | `/proc/<pid>/environ` 快照 + 启动命令字面写入 `switch/` |
| R2 | 启动时可见 CPU 为 `0-27`（`nproc=28`、`sched_getaffinity=[0..27]`），`available_parallelism()=28` | G0-B 只读记录 |
| R3 | pin **之前**两进程每一个 tid 都是 `0-27` | 逐线程 `/proc/<pid>/task/<tid>/status` |
| R4 | 对 **两个 PID** 各执行 `taskset -pc 0 <pid>`（**不带 `-a`**），与旧 G0-D 字面一致 | 命令与 stdout 落盘 |
| R5 | pin **之后**两进程各**恰好一个** `0`（主线程），其余全部 `0-27`；wallet 启动期出现 28 个同批 worker 组（E3 观察项） | 逐线程回读 + `/proc/<tid>/stat` 的 `starttime` 分组 |

- **禁止**用 `taskset -c 0-27 <启动命令>` 替代后宣称「与原基线完全一致」：那改变了启动时的
  affinity 继承路径，且**不会**产出「仅主线程 `0`、其余 `0-27`」的逐线程格局，等于换了第三种配置。
- **禁止**用 `taskset -a -pc 0`（会把 B 臂变成全线程单核）、禁止只 pin wallet 不 pin zebrad。
- **B 臂与旧批次的已知不同点必须如实列出**（因此才不能宣称「完全一致」）：
  链状态起点（旧 h32297 起；B 臂起点是 A 臂结束后的链高）、内核版本可能不同、
  启动路径（旧为恢复快照后启动；B 臂为停服后**原数据**重启）、wallet 同步/缓存状态、
  注资时点（旧注资后随即 G0；本批次注资在阶段 0，B 臂在 11 笔交易之后）、历史 20 轮数据本身。
- **因此**：B 臂结果与 doc 29 §16 的 `1480.0 ms` / `94.94%` / `95.35%` **不得并列、相减、
  算比值或写成「复现成功/未复现」**；冻结 20 轮只作配置特征的来源证据。

---

## 5. 启动与配置切换（命令字面冻结）

### 5.1 阶段 0：恢复快照并以 A 配置启动（沿用 doc 31 §2.2）

```bash
SNAP=/mnt/d/Research/z3/snapshots/z3_regtest_h32297_20261029_145710.tar.gz
bash /mnt/d/Research/z3/wsl/snapshot_restore.sh --check "$SNAP"      # 只读深校验
RAYON_NUM_THREADS=1 taskset -c 0 \
  bash /mnt/d/Research/z3/wsl/snapshot_restore.sh --yes "$SNAP"
```

- `snapshot_restore.sh --yes` 自行「停服 → 备份 → 解包 → `setsid nohup` 重启两个服务」
  （`wsl/snapshot_restore.sh:205–214`）；`setsid`/`nohup` 不改 env 与 affinity，
  派生的 zebrad 与 wallet **继承** `RAYON_NUM_THREADS=1` 与全线程 CPU 0。
- 现有 `data/zebra-state` / `data/zallet` 被移到 `<dir>.bak.<时间戳>` **保留后再解包**，不删除；
  `results/real_e2e_baseline/**` 的冻结结果是磁盘 CSV/JSON，不受影响。如实写入 `meta.json`。
- **禁止**事后 `export`、`/proc` 写入、事后 `taskset -pc`（只改主线程，doc 30 已证伪）。
  若确需修正，只能 `taskset -a -pc 0 <pid>` 并在 `meta.json` 记为偏差；偏差导致 A 臂 G0 不达标则整批不启动。
- 保持 WSL2 常驻 keeper（最后一会话退出会回收实例），沿用 doc 29 §15.3(3)。

### 5.2 阶段 1：配置切换（**不恢复快照、不挖块、不注资**）

**明确禁止两条现成路径**：

| 脚本 | 禁止原因 |
| --- | --- |
| `snapshot_restore.sh --yes` | 必然**备份 + 解包**，重置链状态与余额 → 四级资金门禁与链高闭合全部失效 |
| `regtest_run.sh` | `pkill -9 -f`、按端口强杀、**`generate 2` 挖 2 块**、可能初始化钱包 → 污染链高与场景 |

**冻结的执行规格**（阶段 1 必须从一个**新的、未注入 `RAYON_*`、未 `taskset -c 0`** 的 WSL shell 执行）：

```text
① 停服：用 G0 的 pgrep -x + /proc/PID/exe + SHA256 三重识别取 zebrad / zallet-zaino 两个 PID，
        kill -TERM <pid>，轮询退出，必要时 kill -9 并记录 PID。
        禁用 pgrep -f / pkill -f（doc 28 §5.2：会命中执行 pgrep 的 shell 自身）。
② 不备份 data/、不解包、不挖块、不注资、不初始化钱包、不改任何配置文件。
③ 启动两条命令与 snapshot_restore.sh:205–214 字面一致（不带 taskset、不带 env 注入）：
        setsid nohup "$PREFIX/bin/zebrad" --config "$CONF/zebra.toml" start \
          >> "$PREFIX/log/zebra.log" 2>&1 < /dev/null &
        setsid nohup "$PREFIX/bin/zallet-zaino" --datadir "$ZW" --config "$CONF/zallet.toml" start \
          >> "$PREFIX/log/zallet.log" 2>&1 < /dev/null &
④ 等待 RPC 就绪与 wallet 追平，判据复刻 snapshot_restore.sh:207–238
        （wallet_tip >= max(node_tip, zebra.blocks)）。
⑤ 记录切换前后：时间戳、链高、两侧余额、两个 PID/exe/SHA、pin 前后逐线程 affinity 摘要、env 快照。
⑥ 执行完整 G0-B；其中对两个 PID 各做一次 taskset -pc 0 <pid>（不带 -a），再逐线程回读。
```

- 阶段 1 的启动命令**不得**继承阶段 0 的 `taskset -c 0` 或 `RAYON_NUM_THREADS=1`；
  G0-B 用 `/proc/<pid>/environ` 与逐线程回读**验证继承干净**，不满足即 FAIL、整批不启动。
- 若执行阶段确需把 ①–④ 封装为辅助脚本：**须单独提交、内容不得改变本文件冻结的命令字面**，
  并在 `meta.json` 记录脚本 SHA256 与 commit。**本轮不创建任何脚本**（§14）。
- 两臂内部**不得**改 mask / env / 参数、不得重启服务；配置切换是唯一例外，且只发生一次。

---

## 6. 固定交易场景、轮次与顺序冻结

### 6.1 场景（与 doc 29 §8.1 完全同参）

| 参数 | 冻结值 |
| --- | --- |
| `send_zec` | **3.0** |
| `minconf` | **1** |
| `policy` | `AllowRevealedAmounts` |
| `fund_source` | `orchard`（参数名，实际命中 ironwood 池，**不得读成 Orchard Spend**） |
| 收款方 | 同一个 `SAP_UA`，循环外解析一次复用 |
| `mine_blocks` | **2**（只在正确性门禁里） |
| `pczt_prove` timeout | 1800 s |
| 主形态门禁 | `ironwood.actions == 2` 且 `orchard.actions == 0`；异常标 `shape_changed` 不计入主统计 |
| 调用链 / `T_build` | `create + sign + prove + extract`；`pczt_inspect`、`wait_sync`、broadcast / mine / confirm 不计时 |

- 禁止按 action 数 / spend 数分组比较并声称规模趋势；`signed` 只作记录字段。
- 测量期间不得调整任何参数；确需调整 → 整批作废并按新预注册重跑。

### 6.2 轮次：10 + 10（用户决定，结论强度相应降低）

```text
A 臂：1 warm-up + 10 个有效轮
B 臂：1 warm-up + 10 个有效轮
合计：22 笔交易（warm-up 同样净支出 3.00020 ZEC）
```

- 每臂内 10 轮**连续**执行，不交错、不做 ABBA、不做配对统计、轮间不插入其它负载。
- warm-up 完整含门禁，数据落盘但**不计入有效轮、不进任何统计**；warm-up 后若该臂进程重启 → 整臂作废。
- 失败轮按 doc 29 §9.3 的预定义失败码写 `invalid_runs.csv` 并**在本臂内顺延补足**，
  每臂总尝试上限 **16 轮**（10 有效 + 最多 6 次失败），超过整批作废；
  **不得拼接批次**、不得因「看起来偏慢」删除（§16 的 `r01` 先例）。
- **n=10 小于冻结 20 轮**：本批次定位为**首次描述性比较**，不得包装成同等强度的正式统计结论，
  不得与冻结 20 轮等强度并列。若以后要更稳定估计，须**单独**解决资金并设计 20+20 新批次，
  **不在本批次执行中临时改变轮数或资金规则**。

### 6.3 顺序冻结：A → B（先验理由，不看结果）

**冻结顺序为 A 臂先、B 臂后，执行前即写死，不得因 A 臂结果调整。**

先验理由（与任何测量结果无关）：A 臂配置改动最大（启动前注入 env + 全线程单核 pin），
G0-A 的逐线程判据最严；先跑 A 臂可在批次早期暴露环境不满足，避免先消耗 B 臂 11 笔交易与
对应资金后才发现 A 臂不可达。B 臂要求「启动时不带 env / 不带 pin」，其可行性不依赖 A 臂结果。

- **禁止**：看完 A 臂结果后调换顺序、改 B 臂配置、改 n、增删轮次、追加第三臂、剔除轮次；
  禁止在中途恢复快照或二次注资来「重新开始某一臂」。
- 顺序效应的代价见 §11，作为**已知未控制混杂**如实报告，不用事后调整弥补。

---

## 7. 资金、注资与四级余额门禁

```text
单轮冻结需求   = 3.001 ZEC = 300 100 000 zat   （send_zec 3.0 + 0.001 冻结手续费/安全余量）
实测每轮 ironwood 净流出 = 3.00020 ZEC         （doc 29 §15.2，20 轮闭合已验证）
总覆盖轮数     = 22（A 11 + B 11）
总需求         = 22 × 3.001 = 66.022 ZEC = 6 602 200 000 zat   （用户口径）
```

### 7.1 准备路径（沿用 doc 29 §14 Errata-1 已实测路径）

```text
恢复快照（h=32297）→ ① 记录初始余额（历史实测 ironwood 11.2482 / sapling 119.998 ZEC）
→ ② 执行且仅执行一次：python3 /mnt/d/Research/z3/wsl/fund_pool.py --fund-zec 55
→ ③ 记录实际到账余额（不得按名义 55 推算；历史实测 ironwood 67.4972 / sapling 63.7498 ZEC）
→ ④ 进入 G0-A
```

- 注资是测量前的链准备，不计入任何轮次；其挖块（历史 3 块，h32297→32300）与链高推进如实记录，
  **不计入**「22 笔 × 2 块」的闭合核对。
- **禁止第二次注资**、禁止减小 `send_zec`、禁止减少轮数。任一门禁实测不足 →
  **整批不启动（或中止）**，如实报告缺口，不补差、不临时改设计。

### 7.2 四级余额门禁（全部以**实测**为准，不得互替、不得用名义值推算）

| 门禁 | 时点 | 尚需覆盖的轮数 | 冻结门槛 | 算式 |
| --- | --- | --- | --- | --- |
| **G0-G(A)** | A 臂 warm-up 前 | A 11 + B 11 = **22** | **≥ 66.022 ZEC** | 22 × 3.001 |
| **W-7(A)** | A 臂 warm-up 后 | A 10 + B 11 = **21** | **≥ 63.021 ZEC** | 21 × 3.001（Errata-1 口径） |
| **G0-G(B)** | B 臂切换后、warm-up 前 | B 11 = **11** | **≥ 33.011 ZEC** | 11 × 3.001 |
| **W-7(B)** | B 臂 warm-up 后 | B 10 = **10** | **≥ 30.01 ZEC** | 10 × 3.001 |

- 四个门槛是**四级独立门禁**，不是同一个数字的四种写法；任一 FAIL 即停，
  不得用「反正总量够」跳过中间两级。
- **预算推演（仅作预期管理，不作判据）**：以历史实测 `67.4972 ZEC` 为起点，
  `67.4972 ≥ 66.022`（余 1.4752）→ A warm-up 后 `64.4970 ≥ 63.021`（余 1.4760）→
  A 臂 11 笔后 `34.4950 ≥ 33.011`（余 1.4840）→ B warm-up 后 `31.4948 ≥ 30.01`（余 1.4848）→
  批次末 `1.4928 ZEC`。
- **薄余量风险须如实写明**：四级门禁余量均约 **1.5 ZEC**。若实际到账低于历史值、
  或额外挖块带来意外支出，G0-G(A) 即可能 FAIL。此时按 §7.1 **整批不启动**，
  不补差、不改轮数、不改 `send_zec`、不另立资金方案（那需要新预注册与用户批准）。

### 7.3 闭合核对（独立核对，非性能指标）

以**注资后实测余额**为起点：

```text
Δironwood ≈ −22 × 3.00020 = −66.0044 ZEC
Δsapling  ≈ +22 × 3.0     = +66.0000 ZEC
链高增量  = +44 块 = 22 × 2（注资挖块另计，如实记录）
```

偏差须报告，不静默修正；两臂各自的 txid 互不相同，只要求语义一致 + 节点接受。

---

## 8. 测量链、G0 与排除规则

### 8.1 脚本（从已冻结工具派生，改动范围冻结）

基线：`results/real_e2e_baseline/tools/baseline_measure_v2.py`
（测量路径已与 `baseline_measure.py` 逐函数 AST 比对一致，doc 29 §15.3(1)）。
**只允许七类改动**：

1. `cpu_allowed()` 改为**逐线程**采集 `/proc/<pid>/task/<tid>/status`；
2. 增加 `arm` 字段（A/B 标签）与两臂独立目录；
3. G0 判据项替换为 §8.2 的两套清单；
4. 四级余额门禁（§7.2）与两臂各自的 8/8 放行门禁；
5. `meta.json` 增加：两臂配置标签、两次启动命令字面、`RAYON_NUM_THREADS`、逐线程掩码摘要、
   `Threads` 总数与 `comm` 分布、本预注册文件 SHA256、脚本 commit；
6. 新增 `switch/config_switch.json` 与 comparison 输出（§12）；
7. 输出目录改为 `results/real_e2e_s2_twoarm/run_<ts>/`。

**不得改动**：RPC 方法名与参数顺序、交易参数、阶段顺序、`T_build` 定义、
RSS 采样间隔（50 ms）与 phase + monotonic boundary、`N_rss` 三档阈值、
统计量算法（`statistics.median` / `quantiles(n=4, method='inclusive')`）、
`prove_share` 两种口径、`pgrep -x` + `/proc/PID/exe` + SHA256 三重 PID 识别、
`pczt_inspect` 形态字段。脚本副本与所用 commit 写入 `meta.json`。

### 8.2 G0 两套清单（各自全部 PASS 才进入该臂 warm-up）

| ID | **G0-A**（A 臂） | **G0-B**（B 臂，含复现验证） |
| --- | --- | --- |
| 1 | `uname -r` 记录（不作跨会话比较） | 同左，并核对与 G0-A 一致，不一致 → 整批作废 |
| 2 | 三个二进制 SHA256 == 冻结指纹 | 同左 |
| 3 | 两个 PID 由 `/proc/PID/exe` + SHA256 精确识别 | 同左；记录 PID 变化属预期 |
| 4 | **逐线程**：两进程每个 tid `Cpus_allowed_list == "0"`；记录线程数与 `comm` 分布 | **逐线程**：pin 前全 `0-27`；pin 后两进程各恰好一个 `0`（主线程）、其余全 `0-27`（R3/R4/R5）；记录线程数与 `comm` 分布 |
| 5 | `/proc/<wallet_pid>/environ` 含 `RAYON_NUM_THREADS=1`，无 `RAYON_RS_NUM_CPUS`；记录 zebrad 同项 | `/proc/<pid>/environ` **无**任何线程类变量（R1）；记录 `nproc` / `sched_getaffinity`（R2） |
| 6 | 18232 / 40232 可连、`getwalletstatus` 同步判据成立、链高 ≥ 32257 | 同左，并核对切换前后**链高连续、无挖块** |
| 7 | 实测 ironwood ≥ **66.022 ZEC**（22 轮） | 实测 ironwood ≥ **33.011 ZEC**（B 臂 11 轮） |
| 8 | `bench` 账户存在、`pczt_inspect` warm-up 形态 `2/0` | 同左 |
| 9 | — | `switch/config_switch.json` 完整：切换前后时间戳、链高、余额、PID/exe/SHA、逐线程摘要、env 快照、两次启动命令字面 |

**观察项（不作硬判据，E3）**：启动期一次性同批创建的 worker 组规模。
B 臂预期出现 28 个同批 worker（doc 30 §1.4 的 E3 身份判定）；A 臂全线程单核时 `available_parallelism()=1`，
预期不再出现该规模的成批 worker。**不得**把「没有 28 个」写成「已证明恰有 1 个 Rayon worker」
（未用调试器读 `rayon_core::current_num_threads()`）。

### 8.3 warm-up、放行与有效轮

- 每臂各 1 轮 warm-up（完整含门禁），落盘但不计入 10 轮、不进任何统计。
- 每臂 warm-up 后独立执行 **8/8 放行门禁**；W-7 按 §7.2 对应门槛（A `63.021`、B `30.01`），
  W-6 的 PID 比对以**该臂 G0 记录的 PID** 为准，不得跨臂比对。
- 有效轮 `valid=true` 需同时满足 doc 29 §9.2 的六条 + `arm` 标签正确 + 该臂内 PID/exe/SHA 恒定。
- 排除与补足按 §6.2；无异常不得剔除。

### 8.4 RSS

每臂各自按 doc 29 §5.3–§5.5 的冻结规则采样并判 R1/R2/R3；
**不跨臂比较 `VmRSS`、不计算 `ΔRSS_A/B`、不说「1-worker 省内存」**（§10.2）。

---

## 9. 统计口径（测量前冻结，防止实施期二选一）

### 9.1 每臂独立统计

`T_create` / `T_sign` / `T_prove` / `T_extract` / `T_build` / `T_other` / `T_wall` 七个量的
median / IQR / min / max（n=10）；`prove / T_build` 主口径 `median(逐轮比值)`、
辅口径 `median(prove)/median(build)` **分开标注、不可互算**。
`median` 用 `statistics.median`（n=10 偶数取中间两值平均）；
`IQR = Q3 − Q1` 用 `statistics.quantiles(x, n=4, method='inclusive')`；
时间保留 1 位小数，比值保留 4 位小数或百分比 2 位小数。

### 9.2 唯一允许的跨臂量：描述性中位数比值

```text
ratio_build = median(T_build^A) / median(T_build^B)      ← 分母冻结为 B 臂
Δ%          = (ratio_build − 1) × 100%
```

- **分母必须是 B 臂（默认 28-worker / 仅主线程 CPU 0 配置）**，报告须写明分母是谁。
- 这是**两个边际中位数之比**，**不是**逐轮配对比值：两臂是不同交易、不同轮次，
  **不存在配对关系**。不得与 doc 26 的 `median(逐轮比值)`、F4-A 的 `ΔT` 配对差中位、
  或 `median(A/B)` 混用或互算（沿用 doc 26 §11.0 纪律）。
- 四个 stage 的 `ratio_stage` 只作**辅助展示**，不单独下结论（n=10、多重展示、无校正）。

### 9.3 唯一允许的措辞模板（用户口径，整句使用）

> **在本次真实 PCZT 工作负载和指定运行环境下，两种并行配置的端到端性能存在约 X% 的描述性差异。**

必须同时写明：两臂配置标签、各自 n=10、顺序 A→B、分母为 B 臂、
「联合配置差异、非单因子、非因果」、「首次描述性比较」。

### 9.4 明确不做的统计

**不报**置信区间、p 值、显著性、假设检验、标准差代替 IQR、bootstrap 区间、
配对统计、回归/协变量调整；**不把 IQR 写成置信区间**；**不把 n=10 的描述性比值写成「已证实」**。
若 `ratio` 接近 1，只如实报告「本会话内未观察到明显描述性差异」，**不得**反推「并行配置无影响」。

---

## 10. 允许与禁止得出的结论（测量前冻结）

### 10.1 允许（正列表，只有这六条）

1. 两臂**各自**在本配置、本场景下 `T_build` 与四段的 median / IQR / min / max（n=10）。
2. 两臂**各自** `prove / T_build` 的主、辅两种口径（分开标注）。
3. **按 §9.2–§9.3 计算的 `ratio_build` / `Δ%` 描述性中位数比值**，含模板措辞与全部限定语。
4. 两臂**各自** `pczt_prove` 窗口 `VmRSS` 分布、噪声范围与 R1/R2/R3 方法论结论。
5. 两臂各自正确性门禁、环境版本与**配置事实（E1-M）**：逐线程 affinity、env、线程数/`comm`、
   二进制 SHA、配置切换记录、链高与资金闭合。
6. **n=10 与固定顺序的局限声明**（§6.2、§11）。

### 10.2 禁止（负列表，违反即整批结论作废）

| # | 禁止措辞 / 推论 | 依据 |
| --- | --- | --- |
| 1 | 「Rayon 从 1 线程增加到 28 线程，使证明耗时降低/升高 X%」 | 联合配置，非单因子（§4.3、§11） |
| 2 | 「线程并行度导致 X%」「CPU affinity 单独导致 X%」「tokio 规模单独导致 X%」 | 无法分离贡献 |
| 3 | 「F4-A 在真实 Zcash / 真实交易中加速 X%」、任何 F4-A 内存代价数字 | 无 F4-A 臂，`0.3.2` 二进制 |
| 4 | 「已在真实交易流程中验证 F4-A / 具备外部有效性」 | 未部署 |
| 5 | 把 doc 26 的 −22.00% 乘到本批次 `T_build` 或任何 RPC 总时间 | 版本 / 调用链均不同 |
| 6 | **把 B 臂结果与 doc 29 §16 冻结 20 轮并列、相减、算比值，或写「复现/未复现」** | 跨会话，§0 纪律 2 |
| 7 | 把 `1480.0 ms` / `94.94%` / `95.35%` 当作任一臂的对照基准或预期值 | 不同批次 |
| 8 | 把 B 臂称作「与原基线完全一致」；用 `taskset -c 0-27` 替代 §4.4 复现规格 | 配置特征不同 |
| 9 | 把 A 臂称作「真实 1-thread baseline」「单线程进程」「与本地 1P 完全对齐 / 已统一」 | 多组件多线程；OS/版本/调用链不同 |
| 10 | 把任一臂绝对毫秒与本地 harness、10-29 历史批次或冻结 20 轮并列比较 | §0 纪律 2 |
| 11 | CI / p 值 / 显著性 / 假设检验 / 配对统计 / 标准差代替 IQR / `median(A)/median(B)` 冒充逐轮配对比值 | §9.2、§9.4 |
| 12 | 跨臂 `VmRSS` 差值归因、`ΔRSS_A/B`、`VmHWM`、把 R3 写成「代价为 0」 | §8.4、doc 29 §5.4–§5.5 |
| 13 | 把 `pczt_prove` 墙钟等同 `create_proof` / quotient；把 broadcast/mine/confirm/`wait_sync` 计入 `T_build` | doc 29 §3.3 / §7.2 |
| 14 | 按 action 数 / spend 数分组声称趋势；把 `fund_source=orchard` 读成 Orchard Spend | doc 29 §8.3 |
| 15 | 看完 A 臂结果后改顺序 / 改 B 臂配置 / 改 n / 增删轮次 / 追加第三臂 / 剔除「偏慢」轮 | §6.3 |
| 16 | 第二次注资、减轮数、改 `send_zec`、事后补 env / affinity、拼接批次、跨天继续 | §7、§3 |
| 17 | 把两臂差异写成「已证实」/「因果」/「显著」，或把 `ratio≈1` 写成「并行配置无影响」 | §9.4 |
| 18 | 把本批次结果并入 `results/real_e2e_baseline/**`，或改写 doc 29 / doc 31 任何字节 | 用户「保留历史结果」决定 |
| 19 | 用「28 线程基线」简称冻结 20 轮；把 A/B 比值写成「28 线程 vs 单线程」 | §1.1、§1.2 |

**可以并列展示但不能跨会话计算**：允许一张表把 A、B 两臂（同会话）与冻结 20 轮（**仅作背景、
灰注**）并列列出各自的 median / IQR / n / 配置标签，但必须同页注明
「冻结 20 轮属另一会话另一批次，绝对毫秒不可互比、不可计算百分比」。

---

## 11. 顺序效应与未控制混杂（如实前置）

固定 A→B 使**时间漂移与配置效应不可分离**，这是本设计的已知代价，不是可忽略项：

| 混杂 | 说明 |
| --- | --- |
| 时间顺序 | 温度、系统负载、内核抖动随批次推进变化，单向混在 B 臂里 |
| 进程状态 | B 臂是停服后**冷启动**，wallet 缓存/同步状态与 A 臂不同；各臂 warm-up 只能部分缓解 |
| 链状态 | B 臂起点链高、note 集合、UTXO 与 A 臂不同（每轮 2 块 × 11 轮） |
| 配置耦合 | env、全线程 vs 仅主线程 affinity、`available_parallelism()`、Rayon 池、tokio 运行时同时变化 |
| 样本量 | n=10，IQR 与边际中位数对小样本更敏感 |

- **不设** ABBA / 交错 / 反序重复（资金与轮数不允许，且用户已冻结 10+10 顺序两臂）。
  若以后要控制顺序效应，须另立预注册（例如反序重复批次或 20+20 并解决资金），**本批次不做**。
- 因此 §9.3 的措辞里「描述性」三个字不可省略，且必须写明「顺序 A→B、未控制时间漂移」。

---

## 12. 产物与冻结记录

### 12.1 目录（不覆盖任何既有结果）

```text
results/real_e2e_s2_twoarm/run_<ts>/
  meta.json                     # 回引本预注册 SHA256 + commit、脚本 commit、两臂配置标签
  switch/config_switch.json     # 切换前后 PID/exe/SHA、逐线程摘要、env 快照、链高、余额、命令字面
  armA/{g0,warmup,final}/       # rounds.csv, stage_timings.csv, summary.csv, rss_*, gate.csv, invalid_runs.csv
  armB/{g0,warmup,final}/       # 同上
  comparison.csv                # 两臂边际中位数 + ratio_build / Δ% + 模板措辞字段
  BATCH_RAW.txt / BATCH_NOTES.md
```

- `meta.json` 必须记录：两臂配置标签、`RAYON_NUM_THREADS`、逐线程 affinity 摘要、
  `Threads` 总数与 `comm` 分布、三个二进制 SHA、四级余额门禁实测值、注资与到账记录、
  两次启动命令字面、warm-up 轮次、链高闭合、本文件 SHA256 与 commit id。
- **不写** `results/real_e2e_baseline/**`；**不写** `results/real_e2e_1worker/**`
  （该目录保留给未执行的 S1 / doc 31）。
- **不改** doc 26 / 27 / 28 / 29（含 §21）/ 30 / 31、INDEX、README、tag `f4a-closed-2026-11-05`、
  任何既有脚本 / 配置 / 结果文件。

### 12.2 提交与推送

- 本文件**单独提交**；commit id 与文件 SHA256 在提交后由会话记录，并在实测 `meta.json` 回引。
- `b7a90b0`（doc 30）、`1a8071d`（doc 29 §21 + doc 31）与本次提交按用户决定
  **先保留本地，等勘误与预注册一起检查后再推送**；本轮**不推送**。

### 12.3 执行前的批准闸门

本文件**不是执行授权**。执行前须用户单独批准，并确认：
① 四级资金门禁与薄余量风险；② 配置切换需新起服务（改启动路径，与 doc 29 §11.2
「不主动改现有环境」的冻结边界冲突，须显式批准）；③ 10+10 与固定 A→B 顺序。
若上述任一项调整，须**另起修订节或新预注册**，不得实施期临时改。

### 12.4 本轮文档边界校验（提交前复核）

| 项 | 提交前实测 | 期望 |
| --- | --- | --- |
| `git diff`（tracked） | **空**；`git status` 仅 `?? docs/orchard/32_real_e2e_twoarm_s2_prereg.md` | 只新增本文件，0 删除；提交后 `git show --stat` 为 1 file / 0 deletions |
| doc 29 `29_real_e2e_baseline.md` | `61381` bytes / `cc83087dbd8f6392f2505e98e2f9442afee65c95425d1706c14f70e4da8fb2d4` | 与 `git show HEAD:` 逐位一致（提交 `1a8071d`），**§21 原样保留** |
| doc 31 `31_real_e2e_1worker_prereg.md` | `19919` bytes / `7d223350392e853b836929b5ecec2546713b15689e174fcdbeeb6c01e4a35d3f` | 与 `git show HEAD:` 逐位一致，未改 |
| INDEX / README / tag / `results/**` / 任何脚本 | 未出现在 `git status` | 未改 |

---

## 13. 版本鸿沟（S2 完成后依然不消除）

即使 S2 顺利完成，它**只回答真实 PCZT 环境中的运行配置差异**，不消除以下差异，
也不构成对 F4-A 优化收益的验证：

| 项 | 真实环境 | 本地 F4-A |
| --- | --- | --- |
| Halo2 | `halo2_proofs 0.3.2` | `0.4.0`（补丁基线） |
| Orchard | `0.15.3` | harness 版本 |
| 测量对象 | `pczt_prove` JSON-RPC 墙钟（含序列化/钱包逻辑） | `Bundle::create_proof` 进程内 |
| 场景 | `ironwood.actions=2 / orchard.actions=0` | Orchard bundle 1/2/4/8 Action |
| OS / 绑核 | WSL2 Linux，主线程或全线程 pin | Windows `SetProcessAffinityMask` 全进程 pin |
| CPU 拓扑 | WSL 可见 28 核 | 本地 1P / 8P 配置 |

- 因此**不能**把 doc 26 的 −22.00% 乘到任何真实 RPC 总时间，也不能把本批次比值解读为
  「F4-A 在真实环境的预期收益」。
- **真实环境 F4-A A/B（路线 B）继续关闭**：线程统一不解决版本差异，仍需独立构建工程
  （Halo2 0.3.2 的 F4-A 移植）。H8 / F4-B 保持暂缓。移植与否待 S2 结果与文档审阅后另行决定。

---

## 14. 本轮未执行（边界清单）

- 未恢复快照、未停止/启动/重启服务、未设置或注入任何环境变量、未改任何进程的 affinity；
- 未发交易、未挖块、未注资、未跑 G0 / warm-up / 任何轮次、未做 A/B 任一臂测量；
- 未新建任何启动脚本 / 测量脚本 / 门禁脚本、未改任何配置或既有工具；
- 只读核对内容为：仓库 git 状态与文件哈希、doc 29/30/31 正文、`wsl/snapshot_restore.sh` 与
  `regtest_run.sh`、旧批次 G0-D 输出与 `meta.json`、WSL 中的 `ps` / `nproc` / `uname -r`；
- 未改 doc 26/27/28/29（含 §21）/30/31、INDEX、README、tag、任何 `results/**` 文件；
- 未启动路线 B / H8 / F4-B；未推送；
- 本轮**唯一的写操作**是新增本文件，以及工作区记忆日志的追加记录（仓库外）。

---

## 15. 技术审阅补充（v0.2-revision-1，2026-11-06 16:13，**纯追加，未执行**）

**性质**：回应用户 2026-11-06 16:13 基于设计摘要的技术审阅（**非逐行签字**）。
§0–§14 **一字未改**：追加前全文 `38271` bytes /
SHA256 `af7190a30fd0d874b4206f1c0585475f1c0f938b2021e98db479807fdfdfc15e`（§15.9 复验方法）。
本节只把三处「需核实」的技术细节钉死，并按用户建议做称谓降级与两条纪律再确认；
**不改**资金预算、四级门禁、A→B 顺序、10+10、统计主口径与禁止清单的实验设计。

用户明确表示：**暂不给「预注册审阅通过、可以执行」的结论；暂不执行、暂不推送。**
本文件（含本节）**仍不是执行授权**。

### 15.0 修订导读（§0–§14 原文保留，按本节覆盖引用）

| 计划逐项检查的章节 | 本节对应补充 |
| --- | --- |
| 文首「实验身份」整句 | §15.3 称谓降级 |
| §1.2 两臂称谓 | §15.3 |
| **§4 B 臂配置**（含 §4.4 的 R3/R4/R5 与「启动期 28-worker 组」） | §15.2（初始化时机 + 两段式 G0-B）、§15.3、§15.4 |
| **§5 启停流程** | §15.5（G0-B10–B12 链状态连续性三条） |
| **§8 G0 判据 / §9 统计口径** | §15.2.4（G0-B 两段式与新增硬判据）、§15.4（双变量）、§15.5（B10–B12）、§15.6 |
| §11 混杂清单 | §15.6(2)「同会话不等于同状态」 |
| **§12 批准闸门** | §15.7 新增第 ④ 项 |
| §7 资金门禁 | §15.6(1) 再确认 |
| §1.1 / 历史冻结 20 轮称谓 | §15.10 证据等级说明（E1-M / E3 / E4） |
| **§8.3 warm-up 与放行门禁** | §15.11 warm-up 后逐线程 affinity 复查（G0-B13）+ `current_num_threads()` 警示 |

**为什么指针不插在 §4 / §5 / §8 就地**：本文件沿用 append-only 纪律，
任何插入正文中部的行都会改变 §0–§14 的字节前缀，使 `head -c <追加前字节>` 复验失效。
因此全部补充集中在文件末尾，并以本导读表指路；§0–§14 原文逐字可读、未被「悄悄改写」。

### 15.1 三项核实的结论（全部只读）

| 审阅项 | 核实结果 | 等级 |
| --- | --- | --- |
| B 臂 Rayon 全局池初始化时机 | 用户指出的机制风险**成立**；旧批次池在 pin 前约 60 s 已建（旧进程事实）；触发代码路径**无证据** → §15.2 给两段式 G0-B 与时序硬判据 | E1-S + E1-M + **E4** |
| `RAYON_RS_NUM_CPUS` 环境变量门禁 | `rayon-core 1.13` **仍读取**该旧变量；旧批次 `/proc/2255/environ` 显式查验两变量均 ABSENT → §15.4 拆成两个独立布尔判据 | E1-S + E1-M |
| 停服重启后链状态连续性 | `--check` 已有只读 `sqlite3 PRAGMA integrity_check` 与 rocksdb 结构校验可复用；停服为 `kill -TERM` + 轮询 → §15.5 给三条可验证检查 | E1-S + E1-M |

核实过程未起停/重启服务、未改任何进程 affinity、未注入任何环境变量、未发交易、未挖块、未注资。

### 15.2 Rayon 全局池初始化时机（用户第 2 点的核心）

#### 15.2.1 机制：用户担心成立（E1-S）

```
rayon-core-1.13.0/src/lib.rs:458–486   get_num_threads()
  self.num_threads > 0  → 显式值
  RAYON_NUM_THREADS (1..) → 该值 ; =0 或无效 → default()
  RAYON_RS_NUM_CPUS  (1..) → 该值        [lib.rs:477–483，注释 "Support for deprecated ..."]
  否则 default() = std::thread::available_parallelism()
```

```
rust std 1.88 / sys/pal/unix/thread.rs:346–379（linux 分支）
  libc::sched_getaffinity(0, size_of::<cpu_set_t>(), &mut set)
  → pid/tid = 0 指**调用线程**；返回 CPU_COUNT(set)，再与 cgroups quota 取 min
```

- **结论**：`available_parallelism()` 读的是「**执行全局池初始化的那个线程**」的 affinity。
  rayon 全局池是 lazy 的（首次使用 `THE_REGISTRY` 时构建 `Registry`；二进制含
  `GlobalPoolAlreadyInitialized` / `WorkerThread` 符号，见 doc 30 §1.3）。
  若 `taskset -pc 0` 先把主线程钉到 CPU 0、**随后由主线程**触发初始化，
  `available_parallelism()` 返回 **1** → 池规模 1 而非 28。
- 因此 **§4.1 / §8.2 原 G0-B 判据确实不足以排除该路径**：三项通过
  （env 未设、主线程 `0`、其余 `0-27`）并不自动证明池规模。用户这一条完全成立。

#### 15.2.2 旧批次的实测时序（E1-M + E2，但只对旧进程有效）

| 事件 | 时刻 | 证据 |
| --- | --- | --- |
| wallet 2255 主线程创建 | 11:39:49.540 | `/proc/2255/task/2255/stat` 的 `starttime` 换算 |
| **28 个同批 worker 组创建** | **11:39:49.780–.790** | 同上；`comm=tokio-worker-24` ×28，逐线程 affinity 全 `0-27` |
| 注资日志完成 | 11:40:22 | `logs/fund_once_55zec.log` mtime |
| **G0 报告**（内含对两个 PID 的 `taskset -pc 0`） | **11:40:50** | `g0/g0_gate_g0_final.json` 的 `timestamp` |

- 同批线程组在 G0 pin **之前约 60 秒**已出现，且出现时全部线程可见 `0-27`；但其 Rayon 身份仍为 E3、全局池实际规模从未直接采样（E4）。
  所以**旧批次**没有走到用户担心的「先 pin 主线程、再由主线程触发 lazy 初始化导致池规模为 1」路径（线程组先于 pin 出现的 E1-M 事实成立；线程的 Rayon 身份仍 E3、池规模仍 E4）。
- 但这是**旧进程 2255 的事实**。B 臂是停服重启后的**新进程**，不得据此假设相同；
  这正是 §15.2.4 要在 B 臂重新取证的原因。

#### 15.2.3 明确无数据的部分（E4，不得编造）

- **具体由哪条 Rust 运行路径触发全局池初始化**（哪个 tokio task、`par_iter`、
  `spawn_blocking` 或其它）**没有任何证据**：未做调试器 attach、未做符号级追踪、
  未读 `rayon_core::current_num_threads()`（doc 30 §1.6 明确把侵入式验证排除）。
- 故本节**不回答**「由哪个运行路径初始化」，只给**行为级时序证据**；
  报告里不得写「已证明这 28 个线程就是 rayon worker」——身份恒为 **E3**。
- 用户「**不能只依据进程线程总数或 `/proc/PID/task` 中的线程数量来认定 Rayon worker 数量**」
  一条完全接受：本节依据的是「创建时刻 + 逐线程 affinity + 与 pin 时刻的先后」，
  **不是**「线程数 = worker 数」的推断。

#### 15.2.4 B 臂 G0-B 改为两段式（覆盖 §4.4 R3/R4/R5 与 §8.2 ID 4/9 的执行顺序）

```text
【G0-B1 · pin 之前，全只读】
  ① 采集 wallet/zebrad 每个 tid 的 comm / starttime / Cpus_allowed_list
  ② 两个环境变量双项检查（§15.4）
  ③ 记录 nproc / sched_getaffinity（预期 28 / 0-27）
  ④ 记录「同批创建的 worker 组」规模（E3 观察项）及其最晚 starttime 换算的墙钟 t_worker_group（严谨别名 t_first_observed_thread_group）
  ⑤ 记录本次采集时刻 t_B1；落盘 switch/pre_pin.json

【硬顺序约束】
  只有 ① 全部 tid == "0-27"、② 两项都 ABSENT、③ 为 28 / 0-27、
  且 ④ 的同批组已存在（t_worker_group < t_B1）时，才允许进入 G0-B2。
  若同批组尚未出现：每 2 s 重采一次，最多 60 s（与 snapshot_restore.sh 等待量级一致）；
  超时仍无 → **不得执行 taskset -pc 0，整批不启动**，如实报告「B 臂并行池规模未证实」。
  禁止为了推进批次「先 pin 再等池」。

【G0-B2 · pin 与回读】
  ⑦ 对 zebrad 与 wallet 两个 PID 各执行 taskset -pc 0 <pid>（不带 -a），记录 t_pin
  ⑧ 逐线程回读 R5 格局（两进程各恰好一个 "0"，其余全 "0-27"）
  ⑨ 判定 t_worker_group < t_pin，写入 switch/config_switch.json
```

- **⑨ 是新增的 G0-B 硬判据**；`t_worker_group >= t_pin` 即 G0-B FAIL。
- **`t_worker_group` 的语义边界（用户 2026-11-06 16:23 复核要点 #1）**：它**只表示**
  「历史观测到的同批线程组先于主线程 pin 创建」，是 E1-M + **E3** 观察；
  **不表示** Rayon 全局池初始化已被直接证明（E4）。不得把 `t_worker_group < t_pin` 写成
  「已证实 Rayon 池在 pin 前完成初始化」，也不得据此宣称「已复现 28-worker 全局池」。
- 即使 ①–⑨ 全通过，**仍不得**宣称「已复现 28-worker 全局池」（§15.3 称谓降级）。
- 该两段式对旧批次是**复现而非改变**：旧批次实测顺序同样是
  「同批线程组先出现（11:39:49.78）→ G0 pin（11:40:50）」。该事实只证明线程组在 pin 前已存在，
  不证明 Rayon 全局池初始化已被直接测量（E3/E4）。
- `switch/pre_pin.json` 与 `switch/config_switch.json` 均须回引本节版本号。

### 15.3 B 臂称谓降级（覆盖 §1.2 与文首身份整句）

用户建议接受。**B 臂整句改称**：

> **旧批次的环境变量与逐线程 affinity 配置复现臂（本会话内重跑）**

- **禁用**：「28-worker 配置复现」「默认 28-worker 并行池复现」「已复现 28-worker 全局池」
  「B 臂 = 28-worker 配置」。
- 文首实验身份整句中的「默认 28-worker / 仅主线程 CPU 0」按本节覆盖为
  「旧批次环境变量与逐线程 affinity 配置复现 / 仅主线程 CPU 0」；**原文保留作历史记录**。
- §4.1 表格里「启动期一次性 28-worker 组」保留，但恒为 **E3 观察项、不作硬判据**；
  报告中若提到 28，只能写「启动期同批 worker 组规模为 28（E3，身份未证实）」。
- §1.1 对**冻结 20 轮**仍沿用「28-worker 默认并行池、主线程 CPU-affinity=0 的真实 PCZT 基线」作为
  历史简称（§0–§14 字节前缀不变，doc 29 §21 原样保留）；但其证据等级见 **§15.10**：
  28 个同组线程的创建时刻为 E1-M，线程的 **Rayon 身份为 E3（未直接证实）**，
  **Rayon 全局池实际线程数从未直接采样（E4）**。该简称不得被读成「已测量到 28-worker 全局池」；
  前进引用应改用 §15.10 的严谨整句（不是 B 臂身份，也不改写 doc 29 §21 的冻结标签）。
- 因此 §9.2 的跨臂比值、§10 允许/禁止清单、§11 混杂清单都按「**两种完整运行配置**」解释，
  **不得**改写成「1 worker vs 28 worker 的对比」。

### 15.4 `RAYON_RS_NUM_CPUS` 显式双变量门禁（覆盖 §4.1 / §8.2 ID 5，即旧称 G0-E）

| 证据 | 内容 |
| --- | --- |
| E1-S | `rayon-core-1.13.0/src/lib.rs:477–483` 注释 `Support for deprecated RAYON_RS_NUM_CPUS`；读取顺序为 `RAYON_NUM_THREADS` → `RAYON_RS_NUM_CPUS` → `available_parallelism()`。用户指出它在 1.13 仍有兼容性读取逻辑，**正确** |
| E1-M | 只读 `tr '\0' '\n' < /proc/2255/environ \| grep -E '^RAYON_NUM_THREADS=\|^RAYON_RS_NUM_CPUS='` → **两者均 ABSENT**；所有 `RAYON/THREAD/TOKIO/OMP/NUMEXPR/MKL/BLAS` 变量名也为空 |

**G0-B5 冻结为两个独立布尔项**（两项都 PASS 才算该项通过；zebrad 同项记录）：

```text
env_rayon_num_threads_absent = (grep -c '^RAYON_NUM_THREADS='  environ) == 0
env_rayon_rs_num_cpus_absent = (grep -c '^RAYON_RS_NUM_CPUS='  environ) == 0
```

- 任一为 `false` → G0-B FAIL、整批不启动；**不得**用「只设了旧变量、效果等价」放行。
- A 臂也**只**注入 `RAYON_NUM_THREADS=1`；**禁止**用 `RAYON_RS_NUM_CPUS=1` 替代或同时注入。
- doc 30 §1.2 的宽 grep 已隐含覆盖这两个变量名，本节把它提升为**显式双项判据**，
  避免实施期只查其中一个。

### 15.5 停服重启后的链状态连续性（覆盖 §3 / §5.2 / §8.2 ID 6/9，新增 G0-B10–B12）

**目标（用户原话）**：不是恢复到 A 臂开始前的状态，而是确认
**B 臂确实在 A 臂结束后的连续链状态上继续执行**。
这正是选 S2、而非「两个独立快照单臂实验」的设计理由。

切换窗口内落盘两份**只读**快照 `switch/pre_stop.json` 与 `switch/post_start.json`
（字段冻结：时间戳、链高、`wallet_tip`/`node_tip`/`fully_synced_height`、两池 zat、
两 PID/exe/SHA、`wallet.db` 的 inode/size/mtime、`zebra-state` 的 `CURRENT`/`.sst` 计数、
`data/*.bak.*` 目录集合）。

| 新增判据 | 检查内容 | FAIL 处置 |
| --- | --- | --- |
| **G0-B10 · 停服与持久化** | 两个 PID 经 `kill -TERM` 后用 `kill -0` 轮询至**完全退出**（记录耗时）；触发 `kill -9` 记为偏差并评估；停服后以只读 `sqlite3 "file:...?mode=ro" "PRAGMA integrity_check"` 得 `ok`（复用 `snapshot_restore.sh:127–145` 的只读方式），并记录 `wallet.db` inode/size/mtime 与 rocksdb `CURRENT`/`.sst` | 任一不成立 → 整批不启动 |
| **G0-B11 · 重启后连续一致** | 重启并追平后，只读核对 `getblockchaininfo.blocks`、`getwalletstatus` 三项高度、`z_getbalanceforaccount` 两池 zat 与 `pre_stop.json` **逐位相等**（允许 0 差异）；同步判据成立 | 任一不等 → G0-B FAIL，**不得**「修正/对齐」 |
| **G0-B12 · 启动过程无意外重置** | ① `data/*.bak.<时间戳>` **集合无新增**（当前已有大量历史 bak，必须先记录集合再比对，不能只看「有没有 bak」）；② `wallet.db` inode 未变、`identity.txt`/`*.age` 未变（未重建 DB）；③ 切换窗口内链高**无增量**（未挖块；`fund_pool.py` 默认 `--mine-blocks 3`，若被调用必留痕）；④ 无第二次注资（无新 `z_sendmany`、无新 txid、`fund_pool.py` 未再执行）；⑤ 实际命令字面与 stdout 全部写入 `switch/` | 任一项异常 → 整批不启动 |

- 链高基线：本次只读实测 `zebra blocks = 32342`（正式 20 轮结束态，仅作取证记录）；
  执行阶段**以切换前实测值**为准，不得引用这个历史值。
- 这三条是 §5.2「不备份/不解包/不挖块/不注资」的**可验证化**，不是新增实验变量。

### 15.6 两条纪律再确认（用户第 1、3 点）

1. **资金门禁（算术通过）**：用户复算 `67.4972 − 66.022 = 1.4752 ZEC`，
   「余量较薄但并非预算不足」——接受。§7.2 四级门禁**每次都必须重新读取实测余额**，
   **不得**用上一门禁的理论余额减去预算值推算；`--fund-zec 55` 仍是**唯一**的一次性准备操作，
   **最终是否放行只看实测余额**。
2. **同会话不等于同状态，更不等于逐轮配对**（用户原话，补入 §11 的强调）：
   A 臂承担较早的系统/缓存状态；B 臂继承 A 臂之后的链状态、数据库状态与部分系统缓存，
   且经历停服重启，进程内部状态也变化。§9.2 的
   `R = median(T_build^A) / median(T_build^B)` **只解释为两个边际中位数之比**；
   **不得**转换成「增加线程数使耗时降低 X%」的因果结论，
   **不得**用逐轮配对、置信区间或显著性检验强化结论（§9.4 / §10.2 已有禁止项，此处再强调）。

### 15.7 批准闸门新增一项（覆盖 §12.3）

§12.3 三道闸门之外，执行前还须用户确认
**④ 本节 §15.2–§15.5 的三项核实与 §15.3 称谓降级已被接受**。
用户已明确：**暂不执行、暂不推送、暂不给「审阅通过」结论**；
本地提交（`b7a90b0` / `1a8071d` / `17b66a4` 及本修订提交）是否一起推送，
待按实际章节逐项审阅后另行决定。

### 15.8 本修订节未做（边界清单）

- 未执行任何实验、未起停/重启服务、未发交易、未挖块、未注资、未跑 G0 / warm-up / 任何轮次；
- 未改任何进程的 affinity、未注入任何环境变量；WSL 操作均为只读
  （`/proc` 读取、`taskset -pc <pid>` 无掩码回读、只读 RPC `getblockchaininfo`、`ls` / `stat`）；
- 未新建任何脚本；未改 doc 26/27/28/29（含 §21）/30/31、INDEX、README、tag、
  `results/real_e2e_baseline/**`、任何既有脚本或配置；
- 未推送；未启动路线 B / H8 / F4-B。

### 15.9 版本与 append-only 校验

| 项 | 值 |
| --- | --- |
| 文档版本 | **v0.2（§0–§14 预注册正文）+ revision-1（本节）**，2026-11-06 |
| 追加前全文 | `38271` bytes / `af7190a30fd0d874b4206f1c0585475f1c0f938b2021e98db479807fdfdfc15e` |
| append-only 复验 | `head -c 38271 <本文件> \| sha256sum` 须与上式逐位相等 |
| §0–§14 | **一字未改**（追加前 `38271` bytes / `af7190a3…` 仍是本文件前 38271 字节）；revision-2 仅在 §15 内部就地改 `t_pool` 术语、新增 §15.10–§15.11，对 doc 29/30/31 与 §0–§14 仍为 0 删除 |
| 提交方式 | **单独提交**，只含本修订与术语/门禁修订，不夹带实验代码或结果；本地保留，**不推送** |

---

### 15.10 历史冻结 20 轮称谓的证据等级说明（用户 2026-11-06 16:23 复核要点 #2）

用户撤回此前「把冻结批次称为『28-worker 默认并行池、主线程 CPU-affinity=0』」的建议，
理由成立：若 28 个线程未被可靠归属到 Rayon，该称谓就把「默认逻辑推断」写成了「实际线程池测量」。

**前进引用的严谨整句（今后引用冻结 20 轮时使用）**：

> **RAYON 线程数环境变量未设置、主线程 CPU-affinity=0、其余观测线程 affinity=0-27 的真实 PCZT 基线；当时观测到 28 个同组线程，但 Rayon 全局池实际线程数未直接采样。**

三段证据等级（呼应 doc 30 / §15.2 / §15.3）：

| 事实 | 等级 | 说明 |
| --- | --- | --- |
| 28 个同批线程的 `starttime` 集中在 11:39:49.780–.790、affinity 全 `0-27` | **E1-M**（实测） | 线程在该时刻已创建、当时可见 `0-27` |
| 这些线程属于 Rayon 全局池 | **E3**（推断，未直接证实） | `comm=tokio-worker-24` 是线程名信息，非 Rayon worker 身份证明；未 attach 调试器 / 未读 `current_num_threads()` |
| Rayon 全局池实际线程数 = 28 | **E4**（无数据） | 从未直接采样；不得由「观测到 28 个线程」反推 |

- **doc 29 §21 不因此回头改写**：它的标签是当时冻结记录，本文件 §15.10 只补充其证据等级，
  两者不冲突；前进引用按本严谨整句，不再把预期值写成直接测量值。
- **B 臂称谓**维持 §15.3 的降级（环境变量与逐线程 affinity 配置复现臂）；冻结 20 轮与 B 臂的
  称谓区分写清，避免读者把「观测到 28 个线程」与「28-worker 全局池已证实」混为一谈。
- §1.1 的「28-worker 默认并行池」简称作为历史 shorthand 保留（§0–§14 前缀不变），
  但本报告 / 后续材料引用冻结 20 轮时，须附本证据等级或改用严谨整句。

### 15.11 warm-up 后逐线程 affinity 复查门禁（用户 2026-11-06 16:23 复核要点 #3）

**问题**：B 臂执行顺序为 ① 启动（未 pin）→ ② 等线程组出现 → ③ `taskset -pc 0` pin 主线程
→ ④ 逐线程检查 → ⑤ warm-up → ⑥ 10 轮正式。warm-up 可能触发此前尚未初始化的延迟工作线程：
Linux 新线程**继承创建它的线程的 CPU affinity**（Linux manual page）；若某新线程是在主线程已 pin 到
CPU 0 之后创建的，它会继承 `0`，而非预期的 `0-27`。因此 pin 阶段的逐线程检查通过后，
warm-up 仍可能改变线程集合或 affinity 格局。

**新增门禁 G0-B13（warm-up 后逐线程复查；§8.3 放行门禁的强制前置）**：

1. warm-up 完成后、进入正式 10 轮之前，对 zebrad 与 wallet 两个 PID 的**全部 TID**重新读取
   `Cpus_allowed_list`；
2. 与 G0-B2（`taskset -pc 0` 之后）回读的线程集合做**全量 diff**，记录**新增 TID** 与其
   `comm` / `starttime`；
3. 验证：两进程仍各恰好一个 `0`（主线程），其余全部 `0-27`；**新增 TID 的 affinity 也必须满足该格局**，
   不得出现继承 `0` 的异常线程；
4. 任一新增 TID 的 affinity 不符合预期 → **整批不启动**，不得继续正式轮次。

- **不得**用「只复查启动阶段已经出现的线程」替代全量重读；G0-B13 落盘
  `switch/post_warmup_recheck.json`，须回引本节版本号。
- 该门禁**不改变** §6.2 / §8.3 的 warm-up 定义（warm-up 仍不计入有效轮、不进统计），
  只是给 warm-up 之后补一道线程集合一致性硬判据。

**关于 `rayon_core::current_num_threads()` 的警示（E1-S，不得作为「只读确认」）**：
该调用**不是无副作用的被动探针**。若当前线程不在 Rayon worker 中，它会访问 `global_registry()`；
而该路径在全局池尚未初始化时会**触发默认初始化**（Rust 文档：`THE_REGISTRY` 懒构建）。
换言之，在第一次 Rayon 调用之前直接调用它，可能正是它自己触发了初始化，
从而改变原本的初始化时序，以及（若在 pin 之后调用）affinity 继承结果。
→ **禁止**把 `current_num_threads()` 加进 B 臂启动 / pin / warm-up 阶段作为「只读确认」。
若将来确需采样实际池大小，必须在预注册中显式说明：调用发生在哪个运行阶段、调用线程当时
是否已在 Rayon worker 内、它是否会改变原本的初始化时序，并作为侵入式 E4 测量**单独批准**，
不得混入 G0 判据或 B 臂配置取证。

### 15.12 revision-2 变更记录（2026-11-06 16:23 复核回应）

回应「doc 32 修订摘要」技术复审（非逐行签字），**纯文档层、未执行**：

1. **`t_pool` 术语降级**（§15.2.2、§15.2.4）：`t_pool` → `t_worker_group`（严谨别名
   `t_first_observed_thread_group`）；明确 `t_worker_group < t_pin` 只表示「观测到的同批线程组
   先于主线程 pin 创建」（E1-M + E3），**不表示** Rayon 全局池初始化已被直接证明（E4）；
   旧批次「池先建」措辞改为「同批线程组先出现」。
2. **历史冻结 20 轮称谓证据等级**（§15.10，呼应 §15.3 的 §1.1 引用）：新增严谨整句与三档证据等级
   （E1-M / E3 / E4）；doc 29 §21 不回头改写，只在本文件补充证据等级。
3. **warm-up 后逐线程 affinity 复查门禁**（§15.11，新增 G0-B13）：warm-up 完成后对两 PID 全部 TID
   全量重读、记录新增 TID、验证 affinity 格局；新增线程继承 `0` 即整批不启动；并警示
   `rayon_core::current_num_threads()` 非无副作用被动探针，禁止作为 B 臂启动 / pin / warm-up 阶段的「只读确认」。

本修订**未执行**任何实验、未起停 / 重启服务、未改 affinity / env、未发交易、未注资、未新建脚本；
doc 29 §21、doc 30、doc 31、§0–§14、INDEX / README / tag / `results/**` 全部不变；**不推送**。
