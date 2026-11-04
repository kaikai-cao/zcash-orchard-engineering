# 17 · Stage F3 · LC 预注册：叶子缓存的收益必须在 H6 之后重新核算

> **本文件在实现探针、跑任何一次证明之前写定。** 第 4 节的预测、第 5 节的门槛都在
> 看到任何 LC 数字之前固定。若结果落在预测之外，第 4 节不改，另立"预测失败"记录。
>
> 本轮**不实现优化**，只回答四个问题。计算的数值路径一行不改：探针只多走一遍 AST。

---

## 0. 先把 H6 冻结成参考版本

H6（`Ast::Neg`）的结论已闭合，从本文件起作为 F3 的**性能参考**，不再用 F0 的
3374.6 / 931.6 ms 做唯一对照：

| 项 | 参考值 | 来源 |
|---|---:|---|
| 1P 完整 prove | **2781.4 ms** | `f3_h6/`，10 对配对，95% CI [−15.07, −13.91]% |
| 8P 完整 prove | **784.8 ms** | 同上，[−14.79, −13.20]% |
| 1P 表达式求值 | **1094.6 ms** | D3，同进程隔离计时 |
| 8P 表达式求值 | **263.9 ms** | 同上 |
| 融合树整块乘 | **3932 次**（原 6570） | D3 `ast.vector_muls` |
| 融合树节点 | **16345**（不变） | D3 `ast.nodes` |

后续任何 F3 单因子的 A 侧默认取"H6 树"，只有在需要与 F0 对齐时才回到冻结基线。
冻结的含义是：**H6 的数值、门槛判定、以及 P1 的 112 偏差记录都不再修改**；
`16_ast_neg_optimization.md` 只允许追加勘误，不重写结论。

---

## 1. 为什么 51 MB / 0.12 s 不能直接沿用

F2（文档 13 §6.3）给出的估算是：

```
4632 次叶子访问 × 16384 元素 × 32 B = 2.43 GB
100 个互异查询 × 16384 × 32 B = 51 MB 常驻  →  省掉 2.3 GB 拷贝 ≈ 0.12 s（3.6%）
```

这个估算在 H6 之后有三个口径问题，必须先拆开，否则下一轮会拿一个不可能实现的
数字去做验收。

**(a) 0.12 s 是"全部叶子拷贝"的口径，不是"可避免"的口径。**

`recurse` 对每个节点返回一个新的 `Vec<F>`。叶子 `Ast::Poly` 的物化是"从多项式
原址（带旋转折返）拷出一块 chunk"。但缓存在两种位置上的效果完全不同：

| 位置 | 判定 | 缓存命中后 |
|---|---|---|
| **累加器位置** | `Add` 的左操作数、`Mul` 的左操作数、`Scale`/`Neg` 的唯一子节点、树根 | 仍然需要一块**可写**缓冲区：命中只是把"旋转拷贝"换成"线性拷贝"，**搬运字节数不变** |
| **只读位置** | `Add`/`Mul` 的右操作数、`DistributePowers` 的每个 term | 只需 `&[F]`，**物化完全消失**：0 字节分配、0 字节写回 |

所以可避免的量是 `borrow` 位置的、非首次的访问，不是 4632 次全部。F2 的 2.3 GB
是上界中的上界。（把这个区分做实，正是本轮探针存在的理由。）

**(b) 分母已经变了。**

0.12 s 当初对的是 1.598 s 的求值器；H6 之后求值器是 1.095 s。同样的绝对节省，
在求值器里的占比从 7.5% 升到 11%，在 prove 里的占比从 3.6% 升到 4.3%。但
**不能把 H6 的 14.57% 和 LC 的百分比相加**：H6 移走的是 ALU（`Fp::mul` → 4 条
`sbb`），LC 要移走的是数据搬运（分配 + 读 + 写）。两者是不同类别的成本，
只能各自对 H6 树重新测量。

**(c) 51 MB 是个巧合数字，不是结构量。**

驻留量 = `D × poly_len × 32 B`，`D` 是**融合树**的互异叶子数（跨全部 instance，
不是"每实例 49 个再乘一个拍出来的系数"）。`D` 必须实测。同一份数据在两种缓存
粒度下总量相同，但工作集形态不同：

| 模型 | 驻留 | 线程可见性 |
|---|---:|---|
| 整向量（每次 `evaluate` 把 D 个叶子各物化一次，线程共享 `&[F]`） | `D × poly_len × 32 B` | 跨 chunk 共享，需要一次屏障 |
| 分块（每线程在自己的 chunk 内缓存） | `D × chunk_size × 32 B` **× 并发 chunk 数** | 无共享，但同一份数据被复制 `num_chunks` 次 |

两者总量相同（都是 `D × poly_len × 32 B`），分块模型省掉屏障、但每线程工作集
更大。本轮探针把两个量都报出来。

---

## 2. 四个问题，以及它们的可测定义

| # | 问题 | 可测定义 | 探针字段 |
|---|---|---|---|
| **Q1** | 哪些叶子被多个表达式重复求值？ | 融合树里 `Ast::Poly` 的访问次数 `V`、互异 `(index, rotation)` 数 `D`、复用倍数 `V/D`、按叶子的访问计数表 | `visits`, `distinct`, `leaves[]` |
| **Q2** | 重复求值的输出是否完全相同？依赖哪些上下文？ | ① 同上下文重复物化逐字节相同（单测）；② 换 `(k, extended_k)` 后同一 `(poly, rotation, chunk)` 输出**不同**（单测，证明键必须含域参数） | `domain_len`, `poly_len`, 两条单测 |
| **Q3** | 缓存需要驻留多少数据？ | `D × poly_len × 32 B`（整向量）/ `D × chunk_size × 32 B`（每线程） | `cache_bytes`, `per_thread_bytes` |
| **Q4** | 从缓存读是否比重算便宜？ | 只读位置命中 = 0 搬运；累加器位置命中 = 1 次线性 memcpy。用 replication bench 测单次物化的 ns/元素 | `ns_per_elem`, `saved_chunks` |

Q2 的上下文清单（来自代码，不是猜测）——物化结果
`B::get_chunk_of_rotated(domain, chunk_size, chunk_index, &polys[index], rotation)`
的全部输入：

| 输入 | 是否进入结果 | 说明 |
|---|---|---|
| `polys[index]` | 是 | 注册后不可变（`register_poly` 取所有权），但 `index` 只在一个 `Evaluator` 内有意义 |
| `rotation` | 是 | 旋转量本身 |
| `chunk_size` / `chunk_index` | 是 | 决定返回哪一段、以及短 chunk 的边界 |
| `domain.extended_k − domain.k` | **是** | `get_chunk_of_rotated_extended` 把 `rotation` 放大 `2^(extended_k−k)` 倍（Orchard 这里是 8 倍），所以同一个 `rotation = 1` 在两个不同 k 的域里指向不同偏移 |
| 基（`B`） | 是 | `LagrangeCoeff` 与 `ExtendedLagrangeCoeff` 走不同实现；前者拒绝非零旋转 |
| 线程数 | 间接 | 通过 `chunk_size` 进入（`num_threads × 4` 分块） |

因此**唯一安全的键是"调用内 + chunk 内"**：在一次 `evaluate` 的一个 chunk 里，
`domain`、`chunk_size`、`chunk_index`、`polys` 全部固定，键退化成
`(index, rotation)`。跨调用复用必须把 `(evaluator 身份, domain.k, domain.extended_k,
chunk_size, chunk_index)` 全部并入键——本轮**不建议**做跨调用缓存。

---

## 3. 探针设计（零计算路径改动）

- 新增 `src/f3lc.rs`，只做记录与 JSON 序列化；`ORCHARD_F3_LC` 未设时全体空转。
- `Evaluator::evaluate` 顶部加一条分支：`enabled()` 为真时**额外**走一遍 AST 做普查，
  然后照原样调用 `evaluate_inner`。`recurse` 一个字不改。
- 位置划分写在普查里：根 = 累加器；`Add/Mul` 左 = 累加器、右 = 只读；`Scale/Neg`
  子节点 = 累加器；`DistributePowers` 的 term = 只读。
- 可避免量取保守定义：
  `saved_chunks = borrow_visits − distinct_with_borrow`（每个叶子至少欠一次首次
  物化；首次访问落在累加器位置时更保守）。对应字节 `saved_chunks × chunk_size × 32`。
- replication bench（`ORCHARD_F3_LC_BENCH=R`）在主求值**之后**单线程重放
  `D × num_chunks × R` 次物化，报 ns/元素。它自己不计入任何计时口径。
- 这套普查是第 4 套数据（D4），与 D1（主计时）/D2（阶段）/D3（F2 计数）完全分离，
  不参与任何时间比较。

---

## 4. 预测（可失败的）

| # | 预测 | 若失败意味着 |
|---|---|---|
| **P1** | `V = 4632`，与 H6 树的 `ast.poly` 逐位相同；`D ∈ [90, 150]`，点估计 **108**；`V/D ∈ [30, 52]` | 普查漏了节点类型或漏了 instance |
| **P2** | 只读位置占比 **40%–55%**（点 47%） | < 30% ⇒ "缓存省拷贝"的前提垮掉，LC 只剩分配开销可省 |
| **P3** | 可避免字节 **0.9–1.3 GB**（占 2.43 GB 的 37%–53%） | > 1.6 GB ⇒ 位置划分错了 |
| **P4** | 驻留 **45–75 MB**（整向量模型）；每线程 **1.5–2.5 MB**（8P 几何） | > 100 MB ⇒ 需要按复用阈值筛选，不能全缓存 |
| **P5** | 单次物化 **1.5–3.0 ns/元素**；可避免时间 **60–95 ms/次求值**（1P 单线程口径）；折算端到端 **2.0%–3.2%** | > 5% ⇒ H6 之后搬运占比被低估，LC 优先级应高于预期 |

P5 的推算方式写在这里，免得事后凑数：

```
可避免时间 = saved_chunks × num_chunks × chunk_size × (ns/元素)
1P: saved_chunks ≈ 4632 × 0.47 − 108 ≈ 2070；× 4 chunk × 4096 元素 × 2.2 ns ≈ 75 ms
75 ms / 2781 ms = 2.7%
```

`ast.vector_muls` 这类 ALU 计数与 LC 无关，所以本轮**不预测**任何乘法计数的变化。

---

## 5. 门槛

| 门 | 条件 | 判定 |
|---|---|---|
| **LC-A** | 探针不改值：`V`/节点总数与 D3 逐位一致；同一二进制连跑 3 次计数完全一致；`verify_proof` 通过 | 普查可信 |
| **LC-B** | `V/D ≥ 8` | 复用足够高 |
| **LC-C** | 只读位置占比 **≥ 30%** | 缓存能真的省搬运 |
| **LC-D** | 可避免时间 **≥ 2% 的完整 prove**（1P，H6 树） | 量级值得做 |
| **LC-E（决定）** | LC-B ∧ LC-C ∧ LC-D → 下一轮实现"调用内提升的叶子缓存"单因子 A/B；任一不过 → **关闭 LC**，结论写成"求值器剩余成本不在数据搬运"，不再尝试 | 不做事后调门槛 |

负面验收（留给下一轮，先写定）：缓存的实现不得让 `add`/`mul`/`distribute` 路径
变慢；D2 里除 `quotient` 外的阶段 95% 区间内不得显著回退。

---

## 6. 复现

```bash
cd experiments/transaction_e2e/harness

# 探针 = H6 补丁 + LC 普查补丁（后者依赖前者的 Ast::Neg 分支）
git apply ../vendor/halo2_proofs-0.4.0-h6-neg.patch
git apply ../vendor/halo2_proofs-0.4.0-f3-lc-probe.patch

CARGO_TARGET_DIR=target/f3-lc cargo build --release --offline --features f3lc --bin orchard-e2e
RAYON_NUM_THREADS=1 target/f3-lc/release/orchard-e2e.exe --mode lc --version v3 \
    --spends 1 --outputs 2 --reps 5 --seed 7 --mask 0x1 --tag lc_1p
python ../../../scripts/analysis/f3_lc_report.py
```

跑完必须把 vendor 还原到冻结基线并复核：

```bash
git checkout -- ../vendor/halo2_proofs-0.4.0
python ../../../scripts/analysis/f0_verify_frozen.py
```
