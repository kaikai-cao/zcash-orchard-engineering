# 正式批次执行记录 · `run_20261106_113925`

本文件**不是结果报告**。正式结果在 `docs/orchard/29_real_e2e_baseline.md` §15–§20。
这里只记录为了复核原始产物必须知道的执行层事实与产物索引。

- 实验身份：真实 Zcash PCZT 环境性能基线与 F4-A 外部有效性边界验证（**不是** F4-A 端到端 A/B）
- 依据：`docs/orchard/29_real_e2e_baseline.md` §0–§13（v1.0 预注册，字节未改）
  + §14 Errata-1（v1.0-errata-1，commit `710fded`，资金预算修正）
- 预注册正文前缀 SHA256：`c3d8f999e910e471c218777dc62680994c596324829b1eb16703055a6e666de1`（36 366 bytes）
- 旧批次 `g0_warmup_20261106_111534/`（commit `4f7983d`）：
  **superseded for batch execution / measurement-chain validation only**，原始字节全部保留

## 1. 严格按 §14.3 的 ①–⑦ 执行

| 步 | 动作 | 结果 |
| --- | --- | --- |
| ③ | `snapshot_restore.sh --check` / `--yes` 回 h=32297 | PASS，1058 条目、wallet.db integrity ok |
| ① | 记录初始余额 | ironwood 11.2482 / sapling 119.998 ZEC |
| ② | **一次且仅一次** `fund_pool.py --fund-zec 55` | txid `8d601799…a84ab`，挖 3 块到 32300 |
| ③ | 实测到账（不假定名义值） | ironwood **67.4972** / sapling 63.7498 ZEC |
| ④ | G0（Errata-1 判据 63.021 ZEC） | **8/8 PASS** |
| ⑤ | 1 轮 warm-up（不计入） | 三门禁全 True、RSS 28 点、0 相位异常 |
| ⑥ | **新增放行门禁** W-1..W-8 | **8/8 PASS**，warm-up 后 64.497 ≥ 60.02 ZEC |
| ⑦ | 正式 20 个有效轮次 | **20/20 valid**，无失败轮 |

G0 之后没有第二次注资、20 轮期间没有中途注资、没有改参数 / CPU mask / 进程，
没有重启后拼接。资金闭合：ironwood `64.4970 → 4.4930`（−60.0040 = 20×3.00020），
sapling `66.7498 → 126.7498`（+60.0000），链高 `32302 → 32342`（+40 = 20×2）。

## 2. 执行层事实（不是结果，不改变任何判据）

1. **warm-up 输出层崩溃，交易未重跑**。首跑 `baseline_measure_v2.py` 在所有测量 CSV
   落盘**之后**、`meta.json` 写出之前，因 warm-up 行 `valid=false` 使 `valid_rows` 为空，
   在 `statistics.median` 处抛 `StatisticsError`。该轮交易已成功（txid 在 `rounds.csv`），
   **重跑会多一笔交易并改变链状态**（违反 §9.3）。修复只加在统计输出层：
   空数据保护 + warm-up 不发 R 档；`meta.json` 由 `tools/rebuild_warmup_meta.py`
   只读从已落盘 CSV + 两个只读 RPC 重建。测量路径经 AST 逐函数比对与
   `baseline_measure.py` **完全一致**。`warmup/DERIVED_NOTE.json` 标注 warm-up 数值不是结果。
2. **余额记录脚本变量未导出**：两段内联记录脚本首次未写文件（不影响链状态）。
   初始值由 `restore_yes.log` 与 `fund_once_55zec.log` 的 `[1]` 只读输出双证固定，
   注资后值由实时只读 RPC 复取并与日志 `[5]` 一致；已补写两份 JSON 并注明来源。
3. **WSL keeper**：沿用旧批次已验证的常驻会话 `sleep 86400`（pid 410）保持实例存活；
   恢复脚本自带停服→备份→解包→重启，**未执行** `regtest_run.sh`（会 `pkill -9` 并额外挖块），
   与 §11.1 的执行路径差异如实标注。
4. **`taskset -pc 0`**：§11.2 字面 `0x1` 是 taskset 输出格式、输入只收十进制列表
   （旧批次已定证）；本批次沿用 `0`，回读 `Cpus_allowed_list: 0`。
5. **派生工具**：`tools/g0_gate_errata1.py`（只改 §14.3 资金判据）、
   `tools/baseline_measure_v2.py`（只增统计产物）、`tools/warmup_release_gate.py`（新增门禁）、
   `tools/rebuild_warmup_meta.py`（只读重建）。旧工具 `g0_gate.py` / `baseline_measure.py`
   **字节未变**（SHA256 `9b4b64b6…` / `ed97d5b3…`），保证旧批次可复现。
6. **`prove_share` 精度**：脚本 CSV 按 1 位小数写出，§4.4 冻结口径是百分比 2 位小数。
   结果报告以冻结口径复算值 `94.94%` 为主报；**原始 CSV 保留原样不回填**，偏差见 §16.1。
7. **推送**：Errata-1 首次 `git push` 遇代理 502，本地提交与冻结不受影响，重试后补齐。

## 3. 产物索引

| 路径 | 内容 |
| --- | --- |
| `g0/g0_gate_g0_final.{txt,json}` | G0 8/8 PASS 报告（含 PID/exe/SHA、亲和回读、余额、形态探针） |
| `warmup_release_gate.{txt,json}` | §14.3 第 6 步新增放行门禁 8/8 PASS |
| `warmup/rounds.csv`、`stage_timings.csv`、`rss_windows.csv`、`rss_timeseries.csv` | warm-up 测量链路验收原始输出 |
| `warmup/meta.json`、`DERIVED_NOTE.json` | 只读重建的环境记录与"非结果"标注 |
| `final/rounds.csv`、`stage_timings.csv`、`gate.csv` | 20 轮逐轮原始字段 |
| `final/rss_timeseries.csv`、`rss_windows.csv` | 50 ms 全量采样与每轮窗口汇总 |
| `final/summary.csv`、`prove_share.csv`、`rss_summary.csv`、`rss_verdict.csv` | 冻结统计算法输出 |
| `final/meta.json` | 环境、PID、余额前后、R 档、预注册哈希回引 |
| `logs/restore_check.log`、`restore_yes.log` | 快照深校验与恢复 |
| `logs/balance_initial.json`、`balance_post_fund.json`、`fund_once_55zec.log` | 单次注资前/后余额与原始日志 |
| `logs/g0_console.log`、`warmup_console.log`、`release_gate_console.log`、`final_console.log` | 各阶段控制台原始输出 |
| `logs/environment.txt` | 内核、Python、taskset、二进制 SHA、进程与亲和回读 |
