# 28. 真实环境端到端 A/B · 只读审计（未跑实验）

**状态**：只读审计（2026-11-06）。**没有启动服务、没有发交易、没有跑任何 A/B、没有改任何补丁或配置**。
唯一执行的写操作是 `snapshot_restore.sh --check`，它把 13 MB 归档解到 `/tmp` 做完整性校验后立即清理，
不动服务与现有数据（脚本自带说明，见 `wsl/snapshot_restore.sh` 第 11 行）。

**上游**：用户提出的《Orchard F4-A：真实环境端到端验证方案》第一步——先把真实边界画死，
确认真实调用链，再写预注册。本文只回答这一步，**不含预注册方案**。

**与 F4-A 定稿的关系**：`26_f4a_action_scaling_prereg.md`（v1.0 FROZEN + errata-2）与
标签 `f4a-closed-2026-11-05` 保持不变。本文是新阶段的第一步，暂不挂进 INDEX / README，
等方向确定后再一并补，避免改动已收尾的入口材料。

---

## 0. 结论先行：原方案目前**不能直接跑**

三个硬阻塞，都不是"多跑几对"能解决的：

| # | 阻塞 | 证据 |
|---|---|---|
| **B1** | **A/B 版本不可切换**。真实进程是官方预编译二进制，内部链接 `halo2_proofs 0.3.2` / `orchard 0.15.3`；F4-A 补丁是 `halo2_proofs 0.4.0` 的 834 行改动，只存在于本机 harness 的 vendored crate | 见 §3.2、§3.3。二进制里没有 `ORCHARD_F4A` / `f4a_impl` / `Ast::Memo` 门控符号 |
| **B2** | **E1/E2/E3 场景矩阵不可构造**。真实 PCZT 路径恒为 `ironwood.actions=2`、`orchard.actions=0`（padding 固定），"1 Orchard Spend → 1/2 Output""2 Spend → 2 Output" 在这个 zallet 上没有可控旋钮 | 见 §4.2：10 条历史日志全部是 `ironwood.actions=2`；`pczt_pool_v2/REPORT_v2.md` §0.3 定证"成本由 action 数决定，padding dummy 与真实 spend 同价" |
| **B3** | **Peak RSS 的信噪比不够**。真实 wallet 进程 RSS 在 4 轮内从 231 448 KB 摆到 386 116 KB（**151 MiB**），F4-A 的 ~6 MiB 只是这个摆动的 ~4% | 见 §5.2，采样口径是 `/proc/<pid>/status` 的 `VmRSS`，0.25 s 间隔 |

一个额外但重要的口径问题：用户定义的 `T_E2E` 终点是"得到完整可广播 transaction"，
而现有脚本的 `total_ms = sum(STAGES)` **包含 broadcast / mine / confirm**，
其中 `generate 2` 固定约 510 ms，是 regtest 人造成本，与 F4-A 无关。见 §5.1。

**因此建议先做方向决策（§7），而不是先写命令清单。** 本文不给出实验命令。

---

## 1. 环境清单与仓库状态（只读核对）

| 项 | 值 |
|---|---|
| WSL2 发行版 | `Ubuntu`（另有 `docker-desktop`）；`zebrad` / `zallet` 当前**均未运行**，18232 / 40232 / 18233 无监听 |
| 数据根 | `~/z3-regtest/`：`bin/` 284 MB、`data/` **9.5 GB**、`log/` 76 MB、`snapshots/` 24 MB |
| 链快照 | `/mnt/d/Research/z3/snapshots/z3_regtest_h32297_20261029_145710.tar.gz`（13 167 422 B） |
| 快照校验 | `--check` **通过**：1058 条目（zebra-state 1051 / zallet 3）、rocksdb `.sst` 30 个、`wallet.db` `PRAGMA integrity_check = ok`、`blocks(MAX,COUNT) = (32297, 32297)`、`accounts = [(1,'bench',0,308)]` |
| 二进制 | `zebrad 6.2.3`（`baf0b2cd…`）、`zallet 0.1.0-beta.3` launcher（`523bf86a…`）、`zallet-zaino`（`e602e4a3…`，**实际执行 prove 的进程**） |
| Rust 工具链（WSL Ubuntu） | **没有** `rustc` / `cargo`；也没有任何 zallet 源码 checkout |
| CPU 控制 | `taskset` 可用（`/usr/bin/taskset`），但**现有脚本一条都没用**；`zallet.toml` / `zebra.toml` 无线程数配置 |

三个仓库（全部 `git status` 干净）：

| 仓库 | 路径 | 分支 | HEAD | 本次角色 |
|---|---|---|---|---|
| `z3`（环境） | `D:\Research\z3` | `infra/regtest-harness` | `bb84d7b` | 启动脚本、配置、40 个 WSL 脚本、快照 |
| `zcash-sapling-engineering`（测量） | `D:\Research\zcash-sapling-engineering` | `experiment/msm-phase-d-xyzz-20261102` | `6f29a9d` | **真实交易测量脚本不在当前分支**，在 `experiments/e2e-testnet-tx-harness` @ `5e46712` |
| `zcash-orchard-engineering`（F4-A） | `D:\Research\zcash-orchard-engineering` | `main` | `717a192` | F4-A 补丁、harness、doc 01–27（已收尾） |

> 注意：当前工作分支上 `experiments/e2e_testnet_tx/` 只有 `e2e_tx_bench.py`、`mem_sample.py`、
> `README_runbook.md` 三个文件 + 若干原始日志；`sapling_e2e_bench.py`、`pczt_bench_pool.py`、
> `confirmatory_bench.py`、`h_ab_bootstrap.py` 等都在**另一个分支** `5e46712` 上。
> 可只读恢复查看：`git show 5e46712:experiments/e2e_testnet_tx/pczt_bench_pool.py`。

---

## 2. 启动与恢复方式（历史命令，本次未执行）

来自 `D:\Research\z3\wsl\README.md` §4 与 `wsl/regtest_run.sh`：

```bash
# 在 WSL2 Ubuntu 内，全程离线
SNAP=/mnt/d/Research/z3/snapshots/z3_regtest_h32297_20261029_145710.tar.gz
bash /mnt/d/Research/z3/wsl/snapshot_restore.sh --check "$SNAP"   # 只读深校验
bash /mnt/d/Research/z3/wsl/snapshot_restore.sh --yes   "$SNAP"   # 才真正恢复（停服→备份→解包→重启）
bash /mnt/d/Research/z3/wsl/regtest_run.sh                       # 起 zebrad + zallet-zaino
```

`regtest_run.sh` 做 7 步：部署二进制/配置 → `pkill -9` 旧进程并强制释放 18232/18234/28080/40232
→ 起 `zebrad --config conf/zebra.toml start` → `generate 2` → 钱包加密身份 → 起
`zallet-zaino --datadir data/zallet --config conf/zallet.toml start` → 打印链/钱包状态。

端口约定：Zebra RPC `127.0.0.1:18232`（回环免鉴权）、Zallet RPC `127.0.0.1:40232`
（cookie 在 `data/zallet/.cookie`）、Zebra p2p `18233`。

> 已有踩坑记录（`wsl/README.md` §5）：ZIP 212 grace period 到 **32257**（Canopy=1 + 32256），
> 快照在 32297 已越过；`generate` 返回值是假失败，只能看链高增量；`node_tip` 重启后可能陈旧
> 1001 块，同步判据必须取 `wallet_tip >= max(node_tip, zebra.blocks)`；停服只能 TERM，绝不 kill -9。

---

## 3. 真实调用链到底是什么（这次要画死的边界）

### 3.1 两条真实链路，只有一条与 F4-A 有关

| 链路 | RPC | 证明系统 | 与 F4-A 的关系 |
|---|---|---|---|
| **PCZT 四阶段** | `pczt_create` → `pczt_sign` → `pczt_prove` → `pczt_extract` → zebra `sendrawtransaction` | **ironwood / orchard 家族（Halo 2）** | **相关**（`pczt_prove` 中位 1415 ms，占四阶段 95.1%） |
| `z_sendmany` 异步 | RPC 返回 opid → 轮询 `z_getoperationstatus` → `success` | **Sapling（Groth16）** | 无关。这是 beta.3 的 Sapling PCZT 缺陷绕行方案 |

`pczt_pool_v2/REPORT_v2.md` §0.5 明确写过："本报告的 prove 数据代表 ironwood/orchard 家族
（Halo 2），不可当作 Sapling（Groth16）的证明成本。"

### 3.2 harness 的"端到端"不是这个"端到端"（防止再次踩坑）

`zcash-orchard-engineering/experiments/transaction_e2e/harness/src/main.rs` 的"End-to-end"
指**进程内完整 Orchard bundle 流程**：用合成 Merkle 路径构造 Note → `Builder::build` →
`bundle.create_proof(&pk, &mut rng)` → sign → verify，`Cargo.toml` 依赖是
`orchard = { path = "../../../../orchard-upstream" }`（`0.16.0` @ `616a669d`）+
`[patch.crates-io] halo2_proofs = { path = "../vendor/halo2_proofs-0.4.0" }`。

源码里**没有**任何 RPC / 广播 / 节点交互（全文检索 `zallet|Zebra|broadcast|sendraw|pczt|reqwest`
零命中）。F4-A 的 A/B 其实是**同一个二进制 + 运行期 `ORCHARD_F4A=1` 开关**
（`Cargo.toml` 里 `f4aimpl` feature 的注释写得很清楚："the same binary serves as both arms"）。

所以：doc 24/25/26 里所有 −22.00%、+6.09 MiB、1487 次消除，测的都是**本地 bundle 证明**，
不是 Zcash 交易流程。这一条必须先说清，否则"真实环境端到端"会被误读成已做过。

### 3.3 A/B 版本差异：现在只有一个版本

| 对象 | 版本证据 | 是否含 F4-A |
|---|---|---|
| 真实 `zallet-zaino` 二进制 | 二进制串：`halo2_proofs-0.3.2` ×30、`orchard-0.15.3` ×14；同时含 `ironwood_v3` / `orchard_v3` / `NU6_3` 各 1 处、`compress_selectors` ×5 | **否**：`ORCHARD_F4A`、`f4a_impl`、`Ast::Memo`、`structural_mul_eliminations` 全部零命中 |
| F4-A 补丁 | `vendor/halo2_proofs-0.4.0-f4a.patch`，834 行，改 6 个文件（`f4a_impl.rs`、`evaluator.rs`、`prover.rs`、`vanishing/prover.rs`、`f2.rs`、`lib.rs`），`ORCHARD_F4A=1` 运行期门控 | 只在 harness 的 vendored crate 里 |

三个具体差距：

1. **二进制是官方发布包解压的**，SHA256 已钉在 `wsl/binfingerprint_20261029_154419.txt`，
   不是从源码构建；Rust 静态链接，无法用 `LD_PRELOAD` 或换环境变量注入补丁。
2. **crate 版本不同**：补丁对 0.4.0，真实二进制链接 0.3.2；`evaluator.rs` 是F4-A 的主战场，
   0.3.2 与 0.4.0 之间不能假定可直接套用。本地没有 0.3.2 源码可比对，
   `~/.cargo/registry` 只有 0.4.0 / 0.4.1。
3. **orchard 版本不同**：harness 用 0.16.0，二进制串显示 0.15.3（但含 NU6.3/ironwood_v3 符号，
   可能是 git 依赖未改版本号）。真实电路与 `PostNu6_3` 是否同形，**没有源码级确认**。

> 二进制串检索只是线索，不是源码级证明；但"没有 F4-A 门控符号"足以否定
> "同一份代码、env 一开一关就能 A/B"这个前提。

---

## 4. 之前跑真实交易的脚本与命令

### 4.1 可复用的脚本（都在分支 `5e46712`）

| 文件 | 作用 | 与本方案的关系 |
|---|---|---|
| `pczt_bench_pool.py` | **最贴近**：PCZT 四阶段逐段单调时钟 + 每轮 `total_ms` + `VmRSS` 采样 + 形态分组 | 已有 `T_create/T_sign/T_prove/T_extract/T_broadcast/T_mine/T_confirm` 和 `memory_timeseries.csv` |
| `sapling_e2e_bench.py` | `z_sendmany` 异步路径，拆 `submit / pipeline / chain` 三段 | 可用于对照，但证明系统是 Groth16，与 F4-A 无关 |
| `e2e_tx_bench.py` | 分阶段计时 + `docker stats` 内存；为 **Testnet / Docker** 写的 | `network` 写死 `"testnet"`、阶段顺序是 `create → prove → sign → extract`（与实跑的 `create → sign → prove → extract` **不一致**），不能直接搬到 WSL 原生 regtest |
| `mem_sample.py` | 一次性同步内存（容器口径） | 同上，容器口径不适用 WSL 原生进程 |
| `confirmatory_bench.py` / `h_ab_bootstrap.py` | 50 轮确认性实验与 bootstrap（B=4000, seed=20261030） | 统计脚手架可借鉴，**不是** A/B 版本对照 |

调用形态（`pczt_bench_pool.py` 第 206–234 行，逐字）：

```python
created = z("pczt_create", [ACCOUNT, [{"address": recipient, "amount": send_zec}],
                            minconf, policy, fund_source])     # policy=AllowRevealedAmounts
signed  = z("pczt_sign",   [pczt, row["policy"], False])
proved  = z("pczt_prove",  [signed["pczt"]], timeout=1800)
ext     = z("pczt_extract",[proved["pczt"]])
# 之后 zebra: sendrawtransaction / generate 2 / getrawtransaction confirmations
```

历史命令（来自 `z3/wsl/README.md` §4 第 3 步，注意脚本已不在当前分支）：

```bash
python3 .../experiments/e2e_testnet_tx/sapling_e2e_bench.py \
    --rounds 3 --amount-zec 4 --outputs 8 --outdir .../raw/sapling_e2e_out9
```

### 4.2 历史实测形态（**只作环境事实，不是本次结果**）

来自 2026-10-29 的日志与报告，全部在 `D:\Research\z3\wsl\README.md` 与
`zcash-sapling-engineering@...5e46712` 的 `raw/` 内可复核：

| 量 | 值 |
|---|---|
| `pczt_inspect` | `sapling.spends=0`、`orchard.actions=0`、**`ironwood.actions=2`**、`proof_complete` |
| `pczt_prove` | `ironwood_proven=True`，稳态中位 **1415.2 ms**（N=15），冷启动首轮 1537.1 ms |
| 四阶段合计 | 1488.2 ms，`prove` 占 **95.1%** |
| `total_ms` | 2155.5 ms（含 broadcast 170.6 + mine 512.7 + confirm 0.8） |
| 交易 | txid 有、`size=11134 B`、`stored=True`、确认 2 |
| 形态 | 10 条日志全部 `ironwood.actions=2`；`signed=1` 与 `signed=2` 的 prove 只差 0.5% |

`pczt_orchard2.py` 里 `fund_source="orchard"` 只是参数名，**实际命中的是 ironwood 池**
（`pczt_bench_pool.py` `target_pool()` 的注释、以及 `pczt_orchard2.log` 的余额
`{'ironwood': 3124940000}` 都对得上）。这是 zallet beta.3 的命名/实现细节，不是可以靠参数改成
"1 Orchard Spend" 的旋钮。

---

## 5. 用户列的五个待确认问题，逐条回答

### 5.1 能否在外层包住整个交易流程测时间 —— **能，但要先重新定义窗口**

`pczt_bench_pool.py` 已经在 `one_round()` 里用 `time.monotonic()` 包住每个 RPC 并求
`total_ms = sum(STAGES)`。但：

- 用户要的 `T_E2E` 终点是"得到完整可广播 transaction"，对应 `pczt_extract` 返回时；
  现有 `total_ms` 还含 `broadcast + mine + confirm`（≈684 ms，其中 regtest 挖块 510 ms 是
  人造成本）。**预注册时必须把窗口切到 `create → sign → prove → extract`**，
  节点接受只作正确性门禁，不进计时窗口。
- `T_proof` 是 **`pczt_prove` 这条 JSON-RPC 的墙钟**，不是 `Bundle::create_proof` 的插桩时间。
  该 RPC 内部还可能包含同一 PCZT 里的其它证明（历史上 `sapling_proven` 也会返回 true，
  即便 `sapling.spends=0`）。所以它是"真实钱包侧证明阶段"，不能与 harness 的
  `create_proof` 计时逐位对齐。
- `T_other = T_E2E − T_proof` 在这个定义下 = `create + sign + extract`（历史约 **73–78 ms**，
  N=15 报告 73.0 ms、冷启动 N=4 报表 77.6 ms），而不是"800 ms 的其它"；
  用户举例里的 800 ms 更像包含了广播/挖块。
- 阶段顺序要以实际跑通的 `create → sign → prove → extract` 为准；
  `e2e_tx_bench.py` 里的 `prove → sign` 是 Testnet 草案，未在本环境验证。

### 5.2 RSS 从哪个进程采集 —— **`zallet-zaino`，且必须窗口内采样**

现有 `pczt_bench_pool.py` 的 `find_pids()` 用 `pgrep -x zallet || pgrep -f '/zallet '`，
`rss_kb()` 读 `/proc/<pid>/status` 的 `VmRSS`，0.25 s 一次，写
`memory_timeseries.csv`（列：`t_wall,t_mono,round,zebrad_rss_kb,zallet_rss_kb`）。
`meta.json` 记录当轮 PID（历史值 `{"zebrad":239435,"zallet":239967}`）。

**这里有一处本次审计新发现的可靠性问题**：`zallet` 只是 dispatcher，实际干活的是
`zallet-zaino`；而上面那个 fallback 模式 `/zallet ` 会命中执行 `pgrep` 的 shell 自身
（本次在无服务状态下实测复现：`pgrep -x zallet || pgrep -f "/zallet "` 返回了 shell 的 PID，
`pgrep -a -f zallet` 显示该 PID 的命令行就是这条 `sh -c ...`）。因此历史
`memory_timeseries.csv` 的进程归属**不能无条件采信**，需要在预注册里改成
`/proc/PID/exe` 精确解析（`z3/wsl/bin_fingerprint.sh` 第 24–30 行已经是这个做法，
`wsl/README.md` §1 也明说"比 `command -v` 可靠"），并每轮把 `pid → exe → sha256` 落盘复核。

两个必须写进预注册的口径限制：

1. **`VmHWM`（进程生命周期峰值）在这里没有意义**：wallet 是长驻进程，keygen / 同步 / 缓存
   已经把高水位抬到几百 MB，F4-A 的 6 MiB 会完全淹没——这和 doc 24 §8.3 在 Windows 上
   遇到的 `PeakWorkingSetSize` 问题是同一个。只能报"**交易窗口内采样峰值**"。
2. **噪声量级**：`pczt_cold_v1` 的 161 个采样点里，`zallet_rss_kb` 最小 231 448、最大 386 116，
   **跨度 151 MiB**。6 MiB 是它的 ~4%。若不做严格的同会话 A/B 交错 + 窗口对齐 + 配对统计，
   ΔRSS 不可判定。采样间隔 0.25 s 对 1.4 s 的 prove 窗口也偏粗。

### 5.3 快照还能不能用 —— **能**（§1 的 `--check` 结果）

### 5.4 两个版本如何切换 —— **当前不能**（§3.3）

唯一在概念上成立的做法：拿到 zallet 源码 → 用 `[patch.crates-io]` 把 `halo2_proofs`
指向打了 F4-A 补丁的副本 → 构建出**一个**含 `ORCHARD_F4A` 门控的 `zallet-zaino`
→ 每次开/关 env **重启进程**跑一臂。这能满足"唯一变量是补丁是否启用"，
但需要：网络拉源码、在 WSL 装 Rust 工具链、把补丁从 0.4.0 移植到 0.3.2、
确认 orchard 电路同形、重新做二进制指纹与正确性校验。**这是一个新的构建工程**，
与"不要重新搭环境 / 不要开大而全的新实验工程"直接冲突。

### 5.5 能否外层包住 + 正确性门禁 —— **能，现成**

`pczt_extract` 给 `txid` / `size` / `stored`，zebra `sendrawtransaction` 后轮询
`getrawtransaction` 的 `confirmations`（历史 log 里 `confirmations=8` / `2`）。
用户说的"不要求 `txid(A)==txid(B)`、只要求语义一致 + 节点接受"是对的：
本环境有签名与证明随机性，txid 天然不同。

---

## 6. 若真要做 A/B，还缺的东西（精确清单）

| 缺口 | 现状 | 代价 |
|---|---|---|
| 含 F4-A 的真实 `zallet-zaino` | 不存在 | 拉源码 + Rust 工具链 + 补丁移植 0.4.0→0.3.2 + 重构建 |
| 0.3.2 源码可比对 | 本地没有 | 首次联网；`evaluator.rs` 差异未知 |
| 真实电路与 `PostNu6_3` 同形 | 未做源码级确认 | 需要 orchard 源码/构建元数据 |
| 场景旋钮（spend/output 数） | actions 恒 2 | 需先验证多收款地址能否改变 action 数；本轮只读审计未测 |
| CPU 亲和 | `taskset` 可用但脚本未用，双进程 | 需固定 `zebrad` + `zallet-zaino` 两 PID 的 mask，并固定 `RAYON_NUM_THREADS` |
| `T_E2E` 窗口定义 | 现有 `total_ms` 含 mine | 预注册里改定义，不改历史脚本数字 |
| RSS 判据 | 151 MiB 背景摆动 | 需窗口内采样 + 配对，且预先写"若判定不了就报不可判定" |

---

## 7. 三条路线（需要你定方向，本轮不擅自选）

| 路线 | 做什么 | 能得到什么 | 代价 / 风险 |
|---|---|---|---|
| **A（最小，推荐）** | 只用现有预编译二进制做**单臂真实基线**：3 层时间（窗口重定义）+ RSS 窗口峰值 + 正确性门禁；把"版本不可切换"作为审计结论归档 | 真实系统里 `T_E2E / T_proof / T_other` 的绝对值与占比、F4-A 收益能传递多少的**上界**；明确回答"22% 到端到端还剩多少" | 得不到 A/B 实测；任何"F4-A 使端到端快 X%"只能是**推算（E3）**，不能写成实测 |
| **B（新工程）** | 拉 zallet 源码、装工具链、移植补丁、构建带 `ORCHARD_F4A` 门控的单一二进制，真正做 10 对交错 A/B | 真正的因果 A/B，满足原方案全部控制变量 | 违背"不重新搭环境 / 不开大工程"；版本对齐与电路同形都需先做可行性验证；工作量与 F4-A 主研究同量级 |
| **C（纯推断）** | 不跑真实流程，用 doc 26 的 `B/A` 比值 × 真实 `prove` 占比（95.1%）算端到端预估 | 一个数字 | 只能标 E3；正是用户要求避免的"把局部证据写得更强"，不建议单独做 |

**我的判断**：先走 A，同时把 B 列为"需要单独立项、不在本阶段"；C 只在 A 的报表里作为一行
明确标注的推算，绝不单独成结论。

---

## 8. 边界与禁止措辞（沿用 doc 26 §21 的纪律）

- 本文是**只读审计**：历史数字来自 2026-10-29 的既有日志，**不是本次实验的结果**。
- 不能写"已在真实 Zcash 交易流程中验证 F4-A"——目前只验证了环境可恢复、调用链可定位。
- 不能把 `pczt_prove` 墙钟等同于 harness 的 `create_proof` 计时。
- 不能把 `fund_source=orchard` 读成"Orchard Spend"；实测是 `ironwood.actions=2`。
- 不能把 `total_ms`（含挖块）当作用户定义的 `T_E2E`。
- 不能把进程级 `VmHWM` 或全局 peak RSS 当作 F4-A 的内存代价。
- H8、F4-B 仍暂缓；doc 26 v1.0 FROZEN 与 `f4a-closed-2026-11-05` 标签不受影响。
