# Stage F3 · H6 冻结清单

**冻结日期**：2026-11-04 · **文档**：`docs/orchard/15_h6_neg_prereg.md`、`16_ast_neg_optimization.md`

从冻结之日起，F3 的默认对照是 **H6 树**，不是 F0 的未优化基线。F0 基线只在需要与
E1/E2/E3 对齐时才用（那批数字的口径见 `docs/orchard/11_stage_f_scope.md` §1.4）。

## 参考数值（1P 掩码 `0x1`，8P 掩码 `0xFF`，PostNu6_3，2 Action，K=11）

| 量 | 1P | 8P | 来源 |
|---|---:|---:|---|
| 完整 `create_proof` | **2781.4 ms** | **784.8 ms** | `summary.txt` D1，各 10 对 |
| 表达式求值 `evaluate` | **1094.6 ms** | 263.9 ms | D3（`--mode f2`，同进程隔离计时） |
| quotient 阶段 | 1276.2 ms | — | D2 |
| 融合树整块乘 `ast.vector_muls` | **3932**（原 6570） | 同 | D3 |
| 融合树节点数 | **16345**（不变） | 同 | D3 |
| 端到端相对降幅 | **−14.57%**<br>95% CI [−15.07, −13.91] | **−13.83%**<br>[−14.79, −13.20] | D1 配对 bootstrap |

## 冻结的东西 / 不冻结的东西

| 冻结 | 不冻结 |
|---|---|
| 上述六个数值与两个置信区间 | 后续 F3 单因子只要与 H6 树对照，可以另立基线 |
| H6-A..F 的判定结果（含 P1 的 112 偏差、P5 的字面 FAIL） | 允许在 `16` 追加"勘误"小节，不允许重写结论 |
| `halo2_proofs-0.4.0-h6-neg.patch` 的内容（+255/−13） | vendor 目录本身保持**未打补丁**的冻结基线 |

## 复现

```bash
# A 侧 = 冻结基线；B 侧 = 基线 + 补丁
cd experiments/transaction_e2e/harness
CARGO_TARGET_DIR=target/f3-a cargo build --release --offline --bin orchard-e2e
git apply ../vendor/halo2_proofs-0.4.0-h6-neg.patch
CARGO_TARGET_DIR=target/f3-b cargo build --release --offline --bin orchard-e2e
bash ../../../scripts/benchmark/run_f3_ab.sh <A.exe> <B.exe> ../../../results/transaction_e2e/f3_h6 5 5
python ../../../scripts/analysis/f3_report.py
git checkout -- ../vendor/halo2_proofs-0.4.0   # 还原
python ../../../scripts/analysis/f0_verify_frozen.py   # 必须 PASS
```

## 与下一个单因子的关系

H6 之后第一个被试是**叶子缓存（LC）**：预注册 `17`、结项 `18`。
它在自己的门槛上失败并被关闭（可省 1P 证明的 0.52%），因此**没有**产生第二个"冻结版本"——
F3 的参考树仍然是 H6 树。
