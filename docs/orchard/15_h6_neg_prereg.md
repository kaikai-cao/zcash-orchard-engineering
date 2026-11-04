# 15 · Stage F3 · H6 预注册：把取负从"乘 −1"里拆出来

> **本文件在任何构建、任何测量之前写定。** 第 3 节的改动范围、第 5 节的
> 精确预测、第 6 节的门槛，都在看到 B 侧任何数字之前固定。若结果落在预测之外，
> 第 8 节要如实记为"预测失败"，而不是回头改预测。

## 1. 要检验的假设

F2 把 quotient 求值器数清楚了：一次证明里有 **6570 次整块域乘**，其中 **2526 次
（38.4%）是乘 −1**，来源是 `Expression::Negated` 在编译到 `Ast` 时没有对应的
节点，只能写成 `Ast::Scale(child, −1)`：

```
Expression::Negated(e)
      ↓  Expression::evaluate(..., negated = &|a| -a, ...)
impl Neg for Ast { fn neg(self) -> Ast::Scale(Arc::new(self), -F::ONE) }
      ↓  recurse
Ast::Scale(a, −1)  →  分配 Vec  →  每个元素 Fp::mul(x, −1)
```

而 38.4% 这个占比换算成时间是可测的：F1 的 `scale-cost` 微基准在同一块 16384
元素缓冲上测到 `*x *= -1` 为 11.22 ns/元素、`*x = -*x` 为 1.13 ns/元素。
`Fp::neg` 是 4 条 `sbb`（`pasta_curves-0.6.0/src/fields/fp.rs:405` 的
`const fn neg`），不涉及 Montgomery 归约。

**H6**：取负在表达式层面是明确的语义，不该落到"乘一个常数"上。给 `Ast` 加一个
专用 `Neg` 节点，让 `recurse` 用域取负而不是域乘法处理它。

理想化收益上界（**不是预期值**）：

```
2526 × 16384 × (11.22 − 1.13) ns = 417.6 ms  ≈  3.35 s 的 12.5%
```

## 2. 单因子设计与不变量

| 项 | A 侧（baseline） | B 侧（treatment） |
|---|---|---|
| 源码 | 当前 vendor（HEAD `c5e5aeb`） | 仅 `poly/evaluator.rs` 加一个变体与一个分支 |
| `pasta_curves` | 0.6.0 | 0.6.0 |
| 电路 / Selector / K=11 / SRS / 协议 / transcript | 不动 | 不动 |
| 编译参数 | `release`：opt-level 3、codegen-units 16、无 `lto`、无 `target-cpu` | 完全相同 |
| 线程 | 1P (`0x1`) / 8P (`0xFF`) | 相同 |
| 计时开关 | D1 无任何 env；D2 `ORCHARD_E2_TIMING`；D3 `ORCHARD_F2` | 同侧同开关 |

**明确不做**（第一轮隔离变量，这几项各自的收益必须以后单独测）：

- 不引入原地修改（`recurse` 返回新 `Vec<F>` 的行为保留，`Neg` 也照样分配）
- 不复用 `Vec`、不做缓冲区池化
- 不做子表达式缓存 / CSE（F2 已算出上界 34.7%，但需 606 MB 常驻）
- 不做表达式融合、不改 `DistributePowers` 的折叠方式
- 不改 `Scale` 本身的实现（真的乘一个标量时仍走 `Fp::mul`）

**改动范围锁定在四处**：

| # | 位置 | 内容 |
|---|---|---|
| 1 | `poly/evaluator.rs` · `enum Ast` | 新增 `Neg(Arc<Ast<E,F,B>>)` |
| 2 | `poly/evaluator.rs` · `impl Neg for Ast` | 唯一的取负构造点，改为 `Ast::Neg(Arc::new(self))` |
| 3 | `poly/evaluator.rs` · `recurse` | `Ast::Neg(a)` 分支：`*x = -*x` |
| 4 | 其余 `Ast` 消费者 | `Debug`、F2 的 `f2_ast_stats`（新增 `neg` 计数，`vector_muls()` **不含** `neg`） |

关于第 2 项需要说清：`Expression::Negated` 不是直接构造 `Ast`，它通过
`Expression::evaluate(..., negated = &|a| -a, ...)` 调到 `impl Neg for Ast`。
所以**唯一的构造点就是 `impl Neg for Ast`**。改这里同时会让 `Sub`
（`self + (-other)`）产生 `Neg` 节点——这不是副作用而是同一件事：`a - b` 与
`a + (−1)·b` 在 `Ast` 层面本来就该是同一种形状。除此外没有第三处会生成取负。

## 3. 正确性：等价性优先于"证明能过"

`Neg` 与 `Scale(−1)` 必须逐元素完全相同，而不只是"最终证明能被验证"——
后者太弱，一个只在某些 chunk 出错的 bug 也可能被随机盲化掩盖。

单元测试（放在 `poly/evaluator.rs` 的 `mod tests`，与已有
`short_chunk_regression_test` 并列）：

| 测试 | 目的 |
|---|---|
| `neg_constant_zero` | `Neg(Constant(0))` = 0，不是 p |
| `neg_constant_one` | `Neg(Constant(1))` = −1 |
| `neg_double` | `Neg(Neg(x))` = x |
| `neg_product` | `Neg(Mul(a,b))` = −(a·b) |
| `neg_sum` | `Neg(Add(a,b))` = −(a+b) |
| `neg_scaled` | `Neg(Scale(c,x))` 与 `Scale(−c, x)` 一致 |
| `neg_matches_scale_neg_one` | 一般情形：`Neg(A)` 与 `Scale(−1,A)` 逐元素相同 |
| `neg_random_leaf` | 用随机域元素填充叶子多项式，覆盖非平凡取值 |
| `neg_covers_every_chunk` | 结果向量长度正确、逐 chunk 与旧路径一致（`num_chunks > 1`） |
| `neg_mul_does_not_regress` | `Neg` 路径不得调用 `Fp::mul` 的等价物：用 AST 统计断言 `neg=1, scale=0, mul=0` |

前 8 条比较 `Vec<F>` 全等（`assert_eq!` 逐元素），第 9 条同时验证
`neg` 分支在 `chunk_index > 0` 时也正确（F2 的老 bug 就出在短 chunk 上）。

真实电路：用 `--mode v3 --spends 1 --outputs 2`（Orchard V3，与冻结基线同形状）
在 B 侧跑 3 次完整 prove + verify，记录 `verify_ms`。**不比较 proof 字节**——
随机盲化使两次有效证明本来就可能不同，字节相等既不是必要条件也不是充分条件。

## 4. 三套数据，互不混淆

| 数据集 | 命令 | 用途 | 是否用于主结论 |
|---|---|---|---|
| **D1 主计时** | `--mode v3 --reps 5`，无任何 env | prove 总时间 | **是** |
| **D2 阶段分解** | 同上 + `ORCHARD_E2_TIMING=1` | quotient / evaluate / 各阶段是否回退 | 否，只用于解释 |
| **D3 机制验证** | `--mode f2` + `ORCHARD_F2=1 ORCHARD_F2_BENCH=0` | `ast.scale`/`ast.neg`/`vector_muls` 精确计数与求值器单独耗时 | 否，只用于机制 |

D1 与 D2 分开跑，是因为 E2 的 `e2_add` 会在每个阶段边界取 `Instant` 并加锁一次；
虽然对称、量级可忽略，但把"主指标"和"带插桩的指标"混在一张表里，正是 F2 反复
提醒过的口径污染。D2 的每一侧都带同一套开关，仍然可以作配对比较。

## 5. 预测（可失败的）

**P1（精确，不是范围）**——由 F2 的表达式普查直接推出，改对了就必须逐位命中：

| 量 | A 侧 | B 侧 |
|---|---:|---:|
| `ast.scale` | 3054 | **528** |
| `ast.neg` | 0 | **2526** |
| `ast.vector_muls` | 6570 | **4044** |
| `ast.mul` | 3086 | 3086（不变） |
| `ast.poly` / `ast.add` / `ast.distribute_terms` | 4632 / 3344 / 430 | 不变 |
| Ast 节点总数 | 16345 | **16345**（`Scale`→`Neg` 是 1:1，多一个或少一个都是 bug） |
| gate 段 `scale` | 2924 | 398（−2526，全部落在门约束里） |

**P2** `evaluate` 单独耗时下降 **8%–20%**（D3 + D2）。上界是 26%
（0.42 s / 1.59 s），但取负仍然要读+写同一块 16384 元素，内存流量一点没少，
所以达不到上界。

**P3** 1P prove 下降 **3%–12%**（0.10–0.40 s）。低于 3% 说明内存/其它阶段把
收益吃了；高于 12% 说明上界算错了，要回头查 `scale-cost` 的口径。

**P4** 8P 的**相对收益不大于** 1P。F2 测到 8P 求值器只快 4.30×（8 线程），
说明 8P 已在争用内存带宽；而 `Neg` 省的是 ALU 不是带宽。

**P5（负面验收）** 除 `Neg` 以外的任何表达式类型都不得变慢：`ast.add`/`ast.mul`/
`ast.poly` 计数不变；D2 里除 `quotient` 外的所有阶段（`synthesis`、`advice_commit`、
`permutation`、`lookup_*`、`opening`）在 95% 区间内不显著变慢。

## 6. 统计与门槛

**交错顺序**：用户指定的 ABBA 块

```
round r:  A1 → B1 → B2 → A2
```

每块产生两对：`(A1,B1)` 与 `(B2,A2)`。块内 A 出现在两端、B 在中间，一次线性
漂移（升温、频率下降）对两侧的偏差符号相反，求平均就抵掉了。这比 H1c 用的
"A 先 B 后"更硬——H1c 在 8P 上出现的 `+1.14%` 就疑似是顺序效应。

每侧 25 个样本以上（5 轮 × 2 个位置 × 5 reps = 50 个样本/侧，10 对）。
配对 bootstrap 重采样**对**（10 对，10000 次），报 95% 区间。

| 门 | 条件 | 判定 |
|---|---|---|
| **H6-A** | 第 3 节全部单测通过 + 真实 V3 证明 verify 通过 | 语义等价 |
| **H6-B** | P1 的计数**逐位**命中（`scale` 3054→528、`neg` 0→2526、节点总数不变） | 乘法确实被消除 |
| **H6-C** | D3 的 `evaluate` 单独耗时同进程下降，且方向与 P2 一致 | 求值器变快 |
| **H6-D** | D1 的 1P 配对区间**上界 < 0**（95%），且 H6-A 通过 | 真实 prove 变快 |
| **H6-E（负面）** | P5 成立：无其它类型/阶段回退 | 不是拆东墙补西墙 |
| **H6-F（失败门）** | 若 D1 区间**跨 0** 而 D2 的 `quotient` 显著下降 | 收益被别的阶段吸收，须定位后再谈收益 |

**H6-D 是唯一允许说"端到端更快"的门。** 若 D1 跨 0 而 D3 的求值器确实下降，
结论必须写成"求值器变快，但被 X 吸收"，X 要由 D2 指出来，不能含糊成"收益不明显"。

## 7. 要回答的那个问题

> F2 测得的 38.4% 整块乘法里，有多少**真的**被 H6 消除了？消除之后求值器
> 的耗时又下降了多少？

答案分三层，缺一层都不算回答：

1. **计数层**：`6570 → 4044`，即 2526/6570 = 38.4% 的整块乘法消失（P1，逐位）。
2. **求值器层**：`evaluate` 时间从 X ms 到 Y ms，降幅 Z%（D3）。
3. **端到端层**：prove 从 X' 到 Y'，降幅 Z'%（D1），并给出 Z' 与
   `Z × (evaluate/quotient) × (quotient/prove)` 的差额——差额就是"被吸收"的部分。

若 1、2 成立而 3 不成立，本实验的结论是"求值器的 ALU 时间不是端到端瓶颈"，
这比"prove 快了 3%"更有价值：它说明后续所有求值器层面的优化（CSE、叶子缓存）
都要先跨过这道吸收再说。

## 8. 结果（测量后填写）

完整结果见 [`16_ast_neg_optimization.md`](16_ast_neg_optimization.md)。四条一句话：

| 门 | 结果 |
|---|---|
| H6-A | 通过：9 条单测 + 41/41 库测试；80 次运行每次 `verify_proof` 全部成功 |
| H6-B | 不变量通过（`scale + neg` 守恒、节点总数不变）；**绝对值预测差 112**（漏了 `Sub`） |
| H6-C | 通过：1P `1598.2 → 1094.6 ms`（−31.5%），8P −31.1% |
| H6-D | 通过：1P `−14.57%`，95% CI [−15.07, −13.91] |
| H6-E | 按字面 FAIL（3/9 阶段 +0.7%～+1.3%），按量级通过（合计 +7.5 ms / +0.32%） |
| H6-F | 未触发 |

三层答案：**40.2% 的整块乘法消失 → 求值器 −31.5% → 证明 −14.57%**，
按 `evaluate/quotient` 与 `quotient/prove` 两个份额推算是 −15.40%，
即层 2 的收益有 95% 走到了端到端。

## 9. 复现

```bash
# A 侧（HEAD 源码）
cd experiments/transaction_e2e/harness
CARGO_TARGET_DIR=target/f3-a cargo build --release --offline --bin orchard-e2e

# B 侧（应用补丁之后）
git apply experiments/transaction_e2e/vendor/halo2_proofs-0.4.0-h6-neg.patch
CARGO_TARGET_DIR=target/f3-b cargo build --release --offline --bin orchard-e2e

# 单测（注意：不能加 --offline，dev-deps assert_matches/criterion/gumdrop 不在本地 registry）
git apply experiments/transaction_e2e/vendor/halo2_proofs-0.4.0-h6-neg.patch
cargo test --manifest-path ../vendor/halo2_proofs-0.4.0/Cargo.toml --lib

# D1/D2/D3
bash scripts/benchmark/run_f3_ab.sh <A.exe> <B.exe> results/transaction_e2e/f3_h6 5 5
python scripts/analysis/f3_report.py
```
