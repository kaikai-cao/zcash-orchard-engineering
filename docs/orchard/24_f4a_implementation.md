# 24 · Stage F4-A · 实施结果：让 49 条恢复链每个 chunk 只算一次

预注册：[`23_f4a_implementation_prereg.md`](23_f4a_implementation_prereg.md)（`1e17ffd` 冻结，
`49c8e90` 补统计口径）。本文只报告实测，不改预注册的假设与门槛。

## 0. 一句话

把 `compress_selectors` 造出来的重复链换成一个只读 memo 之后，**1P 完整 Prove −22.00%**
（20 对交错，95% CI [−23.24%, −21.38%]，19/20 对更快，符号检验 p = 4.0e−5），
**8P −15.27%**（20/20 对，p = 1.9e−6），**proof 字节与 `h` 系数逐位不变**。

## 1. 三条结论

| # | 结论 | 证据 |
|---|---|---|
| 1 | **收益是真的，而且比预注册大 4 倍** | 1P −22.00%，成本模型三层对账全部闭合（§3） |
| 2 | 预注册低估的原因是**两个独立的乘性误差**，不是机制错 | 范围 ×2.394（§4）× 单位价格 ×2.17（§6）→ 4.24% × 5.20 ≈ 22.0% |
| 3 | 代价确实可以忽略 | 49 个槽 × 4 chunk 的预计算 = 239 次整块乘，**换掉 1487 次**，比 1 : 6.2 |

## 2. 改了什么

补丁 `vendor/halo2_proofs-0.4.0-f4a.patch`（`+593 / −44`，6 个文件）：

| 文件 | 改动 |
|---|---|
| `src/poly/evaluator.rs` | `Ast::Memo(u16)`；`recurse` 改为返回 `Val::{Owned, Borrowed}`；`intern_chains`（链识别 + 槽分配 + 重写）；`evaluate_inner` 每 chunk 先填 49 个槽再走树；`Evaluator::set_shared` |
| `src/f4a_impl.rs` | 新增。`ORCHARD_F4A` 开关、计数器、`h` 系数 FNV |
| `src/plonk/prover.rs` | 表达式列表 `collect` 后交给 `intern_chains`，再连同 `shared` 传给 `vanishing::construct` |
| `src/plonk/vanishing/prover.rs` | `construct` 形参 `impl Iterator` → `Vec<Ast>` + `Vec<Ast>`；`evaluator.set_shared(shared)` |
| `src/f2.rs` | `AstStats` 加 `memo` 计数，计入 `nodes` |
| `src/lib.rs` | 模块声明 |

**开关默认关。** `ORCHARD_F4A=1` 才生效，且**每次证明重读**（不缓存）——所以同一个进程里可以
`A B A B` 交替，连进程启动与分配器状态都消掉了，比 H6/H7 的"同会话"更紧一档（doc 23 §10.3）。

**没有做的事**：不合并门、不动 `ConstraintSystem` / `Gate` / `Selector`、不做通用 CSE、
不用 H7 的对象池、不改变任何剩余节点的求值顺序。`Add`/`Mul` 在"一侧借出"时把结果写进
`Owned` 那一侧，域加法与乘法可交换 ⇒ 逐位不变（§3.3 的 `h` 指纹证明它）。

## 3. 结构账目：三层独立对账

### 3.1 计数器（`stats_1p.log`）

| 量 | 值 |
|---|---:|
| `chains`（根位置恢复链，去重） | 51 |
| `slots`（消费者 ≥ 2） | 49 |
| `root_consumers`（叶子位置出现次数） | 360 |
| `chain_muls`（基线要做多少次整块乘） | 1740 |
| **`structural_mul_eliminations`** | **1487** |
| `memo_reads` / 每证明 | 1432 = 358 × 4 chunk |
| `slot_fills` / 每 chunk | 49；`chunks_filled` = 4 |

恒等式：`1740 = 1487 + 239 + 14`。239 = `Σ_slots(k−1)`，是 49 次预计算自己的成本；
14 = 2 条 `occ = 1` 的链（不分配槽，仍走旧路径，V6）。

### 3.2 树（`tree_crosscheck.log`，A/B 同一进程，`ORCHARD_F2_BENCH=1`）

| 分量 | A（基线） | B（F4-A） | Δ |
|---|---:|---:|---:|
| `nodes` | 16345 | 7715 | **−8630** |
| `mul` | 3086 | 1360 | −1726 |
| `scale` | 3054 | 1328 | −1726 |
| `add` | 3344 | 1618 | −1726 |
| `constant` | 2198 | 472 | −1726 |
| `memo` | 0 | **358** | +358 |
| `distribute_terms` | 430 | 430 | 0 |
| `vector_muls` | 6570 | 3118 | −3452 |

`−8630 = 5 × 1726`：每个被替换的因子是 `Constant → Scale(−1) → Add → Mul` 四层加一个叶子，
正好 5 个节点。`1726 = Σ_slots occ·(k−1) = 1740 − 14`，即被移出树的全部链乘法；
`358 = 360 − 2` 是留下的 `Memo` 叶子。**六个分量没有一个需要拟合。**

### 3.3 正确性

| 量 | 观测 |
|---|---|
| proof 字节 | 7264（80 次计时证明 + 4 次统计运行，全部相同） |
| proof FNV | `0xa6bcd171d69e969a`（与 H6 / H7 / F4 普查 / F4-A 可行性记录逐位相同） |
| `h` 系数 FNV | `0xee5d0c5e768ae539`，A 与 B 相同（两次独立运行各验一遍） |
| `verify` | 全部通过 |

## 4. 范围更正：普查数的是一个实例，证明者树上是两个

doc 21 / 22 的 181 个消费者、869 个整块乘、627 个可省，**都是对一个
`ConstraintSystem`（193 条约束）数的**——普查挂在 `keygen` 的 `compress_selectors` 两侧，
那里只有一份门表。

但 `prover.rs` 的 `flat_map` 是 `advice_cosets.iter().zip(instance_cosets.iter())`，
而 `advice_cosets` 是**每个 action 一项**。本 bundle 有 2 个 action，于是
`meta.gates` 的 193 条约束被展开两次，链也在树上出现两次：

| 量 | 普查（1 个实例） | 证明者树（2 个 action） | 比 |
|---|---:|---:|---:|
| 消费者 | 181 | **360** | 1.99 |
| 链乘法 | 869 | **1740** | 2.00 |
| 可省 | 621 | **1487** | 2.39（多出的部分来自 `occ = 1` 的链跨 action 变成 `occ = 2`） |

这不是实现的额外收获，也不是机制变了——**是普查的统计范围小了一半**。
doc 21 §5 的"869 / 商树 1424 个 product"同样是单实例口径（F2 的 `cs` 节记录
`product: 1424`，而证明者树的 `mul` 是 3086 = 2 × 1424 + 238）。
两份文档的正文数字保持原样，只在其后加了指向本节的范围说明。

## 5. 性能

### 5.1 1P（`ab_1p.log`）

| 臂 | 中位数 ms | 最小 | 最大 |
|---|---:|---:|---:|
| A（基线） | 6939.6 | 4078.2 | 7968.3 |
| B（F4-A） | 5399.9 | 2726.4 | 6116.5 |

- 每对 `B/A` 中位数 **0.7800**，bootstrap 95% CI **[0.7676, 0.7862]**
- **−22.00%**，CI 折算 [−23.24%, −21.38%]
- 20 对里 19 对更快；**唯一一对反超（1.49）与最极端一对（0.39）是相邻两对**，
  即机器的一次抖动（A 出现 4078 ms 的异常低值、B 出现 6116 ms 的异常高值），
  中位数与 CI 都不受影响。

### 5.2 8P（`ab_8p.log`）

| 臂 | 中位数 ms |
|---|---:|
| A | 1222.3 |
| B | 1041.6 |

`B/A` 中位数 **0.8473**，CI **[0.8376, 0.8645]**，**−15.27%**，**20/20 对**更快
（p = 1.9e−6）。8P 收益低于 1P 是预期的：同一份元素工作量被 32 个 chunk 摊薄后，
memo 的填充与驻留压力相对上升（P8 预测 0–25% 的衰减，实测 7 个百分点）。

### 5.3 verify

| 几何 | A | B | 比值 |
|---|---:|---:|---:|
| 1P | 59.67 ms | 61.35 ms | 0.9495 |
| 8P | 11.06 ms | 11.62 ms | 1.0401 |

绝对差 1.7 ms / 0.6 ms，方向还相反，verify 只占证明的 0.9% / 1.1%。
**按"无系统差异"记录**，不当作信号（P5 的判据是 ±2%，这里超了，但超的量级是毫秒）。

## 6. 预测与实测的偏差：两个乘性因子，都能算出来

预注册 P1：**4.2%–5.6%**（下界 = 621 × 0.1861 ms ÷ 2725.8 ms = 4.24%）。实测 **22.00%**。

**因子一：范围 ×2.394**（§4）。621 是单实例，真实是 1487。

**因子二：单位价格 ×2.17。** 下界把"消掉一个整块乘"定价为 H6 的一个 `Scale`（0.1861 ms）。
但被消掉的不是一次 `Scale`，而是每个因子的整条算式：

| 被消掉的节点 | 每元素代价 | 折算成 `Scale` |
|---|---|---:|
| `ConstantTerm(r)` → `vec![r; n]` | memset，≈0.3 ns | 0.03 |
| `Scale(q, −1)` | 1 次域乘，≈12 ns | 1.00 |
| `Add(c, −q)` | 1 次域加，≈3 ns | 0.25 |
| `Mul(acc, c−q)` | 1 次域乘，≈12 ns | 1.00 |
| 合计 | ≈27 ns | **≈2.3** |

从实测反推：本轮 1487 次消减换回 1526.7 ms，即 **1.0265 ms/次**；把 H6 锚点按本轮
prove 缩放（0.1861 × 6939.6 / 2725.8 = 0.4738 ms）后，比值是 **2.17**——与上表的 2.3 一致。

两者相乘：`4.24% × 2.394 × 2.17 ≈ 22.0%`，与实测 22.00% 相差不到 0.1 个百分点。

**结论：机制预测是对的，定价与范围各错了一次。** 预注册不改；这条偏差按 doc 23 §6 的约定
"按实测记录，不回头改预注册"，并且本节给出可复算的归因。

## 7. 六条不变量（doc 23 §10.4）

| # | 不变量 | 核验 |
|---|---|---|
| **V1** | 全部槽在当前 chunk 内完成初始化 | `slot_fills / chunks_filled = 196 / 4 = 49 = slots`，每个 chunk 恰好填满 |
| **V2** | 读 memo 时无未初始化槽 | 读侧是 `&ctx.memo[slot]`，越界即 panic；`memo_reads / chunks_filled = 1432 / 4 = 358 = root_consumers − 2` |
| **V3** | 同一槽不对应不同域 / coset / 上下文 | 槽号是本次 `intern_chains` 的局部编号，`Ast` 与 `memo` 都是本次构造的产物 |
| **V4** | memo 生命周期不超过当前 chunk | `memo: Vec<Vec<F>>` 是任务闭包里的局部变量，任务结束即释放 |
| **V5** | 消费者不得修改 memo | `Val::Borrowed(&[F])`，没有 `&mut` 通道；三个消费点都是只读 |
| **V6** | 单消费者链保持原路径 | `occ ≥ 2` 才分配槽；51 条里 2 条不分配，`chain_muls − saved = 253` 里的 14 就是它们 |

## 8. 边界与没做的

1. **收益里有一半来自 doc 21 / 22 没数到的那半个树**（§4）。对外表述必须写成
   "1P −22.0%"，不能写成"消掉 621 个乘法换来 22%"——621 与 22% 对不上，1487 才对得上。
2. **没有独立数分配次数**。P6 要求"每 chunk 少 ≥ 621 次分配"，结构上每消掉一个链因子
   就少 4 次分配（`Constant` / `Scale` / `Add` / `Mul` 各一次）加 1 次叶子拷贝，
   即每 chunk 少 `5 × 1726 = 8630` 次、1P 四 chunk 合计 34 520 次。这是**推算**，
   没有装计数器实测，不能标成测量值。
3. **内存只给了上界**。`PeakWorkingSetSize` 在 keygen 之后已经是 GB 量级，几 MiB 的
   增量落在分辨率以下（A、B 都读到 0.0 MiB）。按槽数算：1P 每 chunk
   `49 × 4096 × 32 B = 6.1 MiB`（4 个 chunk 串行）→ 峰值 ≈ 6.1 MiB；8P 32 个并行 →
   上界 24.5 MiB。都远低于 F4A-4 的 64 MiB，但**这是算出来的，不是测出来的**。
4. **只覆盖 `(固定列, 根位置)` 的链**。非根位置的链不共享（F4A-2 要求 `Memo` 只在根位置）；
   §4 说明的那 2 个嵌入消费者仍在旧路径上。
5. **不推广**。没有做通用 CSE、没有跨 `evaluate` 复用、没有对象池。H8 与 F4-B 未开工。

## 9. 复现

```bash
cd experiments/transaction_e2e
git apply vendor/halo2_proofs-0.4.0-f4a.patch
cd harness
cargo build --release --offline --features f4aimpl --bin orchard-e2e

RAYON_NUM_THREADS=1 ./target/release/orchard-e2e --mode f4aimpl --reps 20 \
  > ../../../results/transaction_e2e/f4a_impl/ab_1p.log
RAYON_NUM_THREADS=8 ./target/release/orchard-e2e --mode f4aimpl --reps 20 \
  > ../../../results/transaction_e2e/f4a_impl/ab_8p.log
ORCHARD_F4A_STATS=1 RAYON_NUM_THREADS=1 ./target/release/orchard-e2e --mode f4aimpl --reps 1 \
  > ../../../results/transaction_e2e/f4a_impl/stats_1p.log
ORCHARD_F2=1 ORCHARD_F2_BENCH=1 ORCHARD_F4A_STATS=1 RAYON_NUM_THREADS=1 \
  ./target/release/orchard-e2e --mode f4aimpl --reps 1 \
  > ../../../results/transaction_e2e/f4a_impl/tree_crosscheck.log

cd ../../..
python scripts/analysis/f4a_ab.py results/transaction_e2e/f4a_impl/ab_1p.log \
  --struct results/transaction_e2e/f4a_impl/stats_1p.log \
  --out-dir results/transaction_e2e/f4a_impl          # → summary_1p.md

# 还原
cd experiments/transaction_e2e/vendor/halo2_proofs-0.4.0
git checkout -- src/f2.rs src/lib.rs src/plonk/prover.rs src/plonk/vanishing/prover.rs \
                src/poly/evaluator.rs
rm -f src/f4a_impl.rs
python ../../../scripts/analysis/f0_verify_frozen.py    # 期望 RESULT: PASS
```
