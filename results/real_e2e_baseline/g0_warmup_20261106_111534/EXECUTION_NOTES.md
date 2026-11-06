# Phase 1 (G0) + Phase 2 (warm-up) 执行层记录

本文件**不是结果报告**，只记录为了解释 `PHASE12_RAW.txt` 必须知道的执行层事实。
不做统计、不做结论、不改写 `docs/orchard/29_real_e2e_baseline.md` 的任何预注册正文。
F4-A 冻结材料（doc 26 / 27 / INDEX / README / tag `f4a-closed-2026-11-05`）未动。

## 1. WSL 实例回收（导致首次 G0 全项 FAIL）

- 第一次 `snapshot_restore.sh --yes` 于 11:14:35 成功恢复并起服务，但 `wsl.exe` 会话结束时
  WSL2 回收整个实例，`zebrad` / `zallet-zaino` 收到 `SIGTERM`（日志时间 03:14:55 UTC）。
  11:17:00 的 `g0_gate_pre_fund` 因此 6 项 FAIL（PID 无、RPC refused、cookie 缺失），
  **不是门禁条件失败**，保留原报告作为记录。
- 处理：启动一条常驻 WSL 会话（`sleep 86400`，后台任务）撑住实例，再重跑
  `--check` → `--yes`。之后 `wsl.exe` 跨调用时 `zebrad`(736) / `zallet-zaino`(1027) 持续存活。
- 因实例回收，快照恢复**执行了两次**（11:14:35 一次、11:20:29 一次）。
  两次都从同一个归档、同一 `--yes`，数据根被完整替换；第二次备份为
  `*.bak.20261106_112029`。**未执行 `regtest_run.sh`**：恢复脚本已含停服→备份→解包→重启，
  再跑它会 `pkill -9` 刚恢复的进程并额外 `generate 2`，属于与预注册 §11.1 不同的执行路径，
  此处如实标注偏差。

## 2. `taskset -pc 0x1` 字面命令失败（语法问题，不是条件变更）

- doc 29 §11.2 写的 `taskset -pc 0x1 <pid>` 中 `0x1` 是 taskset **输出**的十六进制掩码格式，
  util-linux taskset 的**输入**只接受十进制 CPU 列表，字面执行报
  `failed to parse CPU list: 0x1`（见 `g0_gate_g0_run2.json`，当时 `Cpus_allowed_list=0-27`）。
- 修正为 `taskset -pc 0 <pid>`，目标仍是**同一个 CPU 0**；回读 `Cpus_allowed_list=0`，
  与冻结条件一致。失败的原始尝试保留在 `g0_gate_g0_run2.*`。
- 这是执行层 CLI 语法修正，未改变冻结的 CPU 配置、未在中途改动 mask。

## 3. 资金门禁：照原判据执行，判为 FAIL

| 时点 | ironwood | sapling | 链高 |
| --- | ---: | ---: | ---: |
| 快照恢复后（G0 首测） | 11.2482 ZEC | 119.9998 ZEC | 32297 |
| 唯一一次 `--fund-zec 40` 后（最终 G0） | **51.87245 ZEC** | 79.3748 ZEC | 32300 |
| warm-up 后 | 48.87225 ZEC | 82.3748 ZEC | 32302 |

- 预注册 §8.2 冻结需求 `60.02 ZEC`（20 × 3.001）。注资 40 ZEC 后实际增加 40.62425 ZEC
  （40 主输出 + 0.62425 找零进 ironwood），仍**差 8.14755 ZEC**。
- 因此最终 G0 为 **7/8 PASS，唯一 FAIL = G0-G 资金**。按 §11「失败则不进入 20 轮」，
  **正式 20 轮未启动**。按 §13「照原判据执行并如实报告」，**没有**降 `send_zec`、
  **没有**第二次注资、**没有**改任何交易参数。
- warm-up 一轮实测 ironwood **−3.00000 ZEC**、sapling **+3.00000 ZEC**，
  这是对「每轮从 ironwood 净流出 3 ZEC」以及 `need_total = 60.02` 的**实测确认**，不是推算。
- 这是预注册 §8.2 的算术缺陷（一次 40 ZEC 不足以覆盖快照的真实起始余额）。
  按 §13 应由勘误节记录；**本轮不自行修订**，等用户决定后再追加到 doc 29 的 §14+。

## 4. warm-up 的身份

- `--label warmup --rounds 1`，`valid=False`（§9.2 第 6 条：warm-up 不算有效轮次），
  `invalid_runs.csv` 里 `failure_codes` 为空，仅作标记。
- 数据用于**测量链路验收**（场景 / PID / RSS 覆盖 / 时间字段 / 资金），
  **不计入 20 轮、不进入任何统计、不授权 Phase 3**。
- 若后续需要重新起环境（含修订预注册、再次恢复快照、或 WSL 实例被回收），
  按 §9.3「warm-up 之后若进程重启，整批作废」与本记录，本轮 warm-up 数据一并废弃并重跑。

## 5. 服务与进程状态

- `zebrad` PID 736、`zallet-zaino` PID 1027，两者 `/proc/PID/exe` 与 SHA256 四次 G0 全程一致，
  `Cpus_allowed_list=0`，内核 `6.18.58.1-microsoft-standard-WSL2`。
- 常驻会话仍在（后台任务 `tWNZbx`）。若该会话在本轮对话结束后被回收，服务会随之退出；
  这不是实验失败，但后续任何正式轮次都必须从快照重新恢复、重跑 G0 与 warm-up。

## 6. 产物文件

`PHASE12_RAW.txt` 是 G0 四份报告 + 注资日志 + warm-up 全部原始表的顺序拼接（无加工）。
`tools/g0_gate.py`、`tools/baseline_measure.py` 是本轮使用的两个脚本，
后者按 §11.3 的五类白名单从 `zcash-sapling-engineering@5e46712` 派生。
