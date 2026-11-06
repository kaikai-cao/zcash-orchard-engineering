# 批次状态标记（doc 29 §14.4 / Errata-1）

**状态：`superseded for batch execution / measurement-chain validation only`**

- 提交：`4f7983d`（Phase 1 G0 + Phase 2 单轮 warm-up 的原始输出）
- 标记时间：2026-11-06
- 标记依据：`docs/orchard/29_real_e2e_baseline.md` §14 Errata-1（v1.0-errata-1）

## 判定

| 项 | 结论 |
| --- | --- |
| 正式批次资格 | **作废**。G0-G 资金门禁 FAIL（7/8 PASS），且 warm-up 已改变链状态与余额，按 §9.3 不得拼接、不得在当前链状态继续 |
| 测量链路验收 | **成功**。时间窗口、四段字段、`T_wall` 一致性、prove 窗口 RSS 覆盖、phase 标记、三门禁、PID 三重识别均通过 |
| 原始字节 | **全部保留、不删不改**（四份 G0 报告 + 注资日志 + warm-up 全部 CSV/JSON + `PHASE12_RAW.txt` + `EXECUTION_NOTES.md`）。本文件是新增标记，不覆盖任何既有产物 |
| 执行层事实 | 可引用：WSL2 回收最后一个会话需常驻 keeper、`taskset -pc 0` 才是正确输入语法、`pgrep -x` + `/proc/PID/exe` + SHA256 有效、50 ms prove 窗口采样可用 |

## 明确不得引用为正式 baseline 统计结果

`1535.582 ms`（warm-up prove）、`94.4865%`（warm-up prove_share）、
`RSS 417888 KB`（warm-up 窗口峰值），以及本批任何时间 / RSS 数值。
它们属于**另一批次**，按 §0 纪律 2 不得与正式 20 轮并列、算百分比或作趋势比较。

## 重新执行

按 §14.3：恢复原始快照 h=32297 → 单次 `--fund-zec 55` → G0 **8/8 PASS** →
1 轮 warm-up → warm-up 后余额 ≥ `60.02 ZEC` → 正式 20 个有效轮次。
新批次产物另起 `results/real_e2e_baseline/run_<ts>/`，与本目录严格隔离。
