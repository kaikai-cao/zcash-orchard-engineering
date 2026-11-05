# 23 · Stage F4-A · 实施预注册：让 39 条恢复链只算一次

> **本文件第 1–6 节在写任何一行求值路径代码之前定稿并单独提交。** 第 5 节的预测与
> 第 6 节的门槛在看到任何 F4-A 实施数字之前固定；实测结果落回第 8 节（届时另开一节，
> 不改预测）。
>
> 前置：`22_f4a_feasibility_prereg.md`（可行性 Go，判决见其 §8.6）、
> `21_constraint_system_recon.md`（869/242/627 的来源）。
>
> 本轮**只写预注册**。不改 `recurse`、不改 `Ast`、不改 `prover.rs`。

---

## 0. 一句话

把 `compress_selectors` 复制的 39 条 selector 恢复链，在**构造 `Ast` 时**合成同一个
`Arc`，在**求值时**每个 chunk 只算一次，消费者只读。撤掉 621 个整块乘，
换掉 168 次簿记（1.9 ns/次）与 5.1 MiB 常驻。

---

## 1. Baseline 与依赖指纹

| 项 | 值 |
|---|---|
| 基线 | **H6 冻结态**（`f0_verify_frozen.py` PASS，58 文件）；不含 H7 |
| 电路 | Orchard `PostNu6_3`，2 actions（1 spend + 1 output，UNPADDED） |
| 证明 | 7264 B / `0xa6bcd171d69e969a`（H6/H7/F4/F4-A 四处记录一致） |
| 工具链 | rustc 1.88.0 / cargo 1.88.0，`--release --offline` |
| vendor | `halo2_proofs-0.4.0` + 六个补丁（`f4a-probe`、`f4b-probe` 只在各自轮次应用） |
| 域 | `k=11`，`n=2048`，扩展域 16384；`chunk = 4096`（1P）/ `512`（8P） |

**为什么基线是 H6 而不是 H6+H7**：H7 改的是"每个节点怎么物化"，
F4-A 改的是"每个节点算几次"。叠在一起，任何收益都无法归因到哪一项。
H7 的 `Borrowed`/`Owned` 纪律会被借用（见 §3.4），但 H7 的池化/缓冲区复用不进来。

---

## 2. 优化范围

**只处理 F4 普查确认过的那 39 条链，以及它们的 168 个根位置消费者。**

| 量 | 值 | 来源 |
|---|---:|---|
| 恢复链（`compress_selectors` 的替身） | 50 | 22 号 §8.2 |
| 其中消费者 ≥ 2（可共享） | **39** | 同上 |
| 消费者总数 | 181 | 同上 |
| 位于约束根 | 179 | 同上 |
| **可共享且在根位置** | **168** | 本节 |
| 整块乘（现状） | 869 | 21 号 §5 |
| 共享后仍要算 | **248** | 242 + 6，见下 |
| **省下** | **621** | 869 − 248 |

**为什么是 621 而不是 627。** 固定列 19 的链有 12 个消费者，其中 10 个在约束根、
2 个嵌在 `witness point` 的乘积里（22 号 §8.2）。interning 只作用于"整条约束就是
一条链"的项，所以这条链仍要算 3 份（1 份预计算 + 2 份内联）：
`(12−3)×3 = 27`，而不是 `(12−1)×3 = 33`，少省 6 个乘。
把 2 个非根出现也替换掉需要一次结构重写——那已经接近通用 CSE，**本轮不做**（§8 约束一）。

---

## 3. 机制：`Ast::Memo(slot)` + 每 chunk 预计算 + 只读 memo

### 3.1 起点：`Ast` 本来就是 `Arc` 树

`poly/evaluator.rs:293-311`：

```rust
pub(crate) enum Ast<E, F: Field, B: Basis> {
    Poly(AstLeaf<E, B>),
    Add(Arc<Ast<E, F, B>>, Arc<Ast<E, F, B>>),
    Mul(AstMul<E, F, B>),                       // AstMul(Arc<Ast>, Arc<Ast>)
    Scale(Arc<Ast<E, F, B>>, F),
    DistributePowers(Arc<Vec<Ast<E, F, B>>>, F),
    LinearTerm(F),
    ConstantTerm(F),
}
```

子树已经是 `Arc`，`Clone` 是引用计数。**共享不需要新的节点类型，也不需要改
`Expression`**；缺的只是一个"这里是一个共享点"的标记，以及一个放结果的地方。

### 3.2 三个候选，实测后选最笨的那个

`f4b_probe` 实测（每次查找，`F4B_LOOKUP`）：

| 机制 | ns / 次 | 168 次 / 求值 |
|---|---:|---:|
| **连续数组下标**（`Ast::Memo(slot)` + `memo[slot]`） | **1.91** | **0.32 µs** |
| `HashMap` 按地址 | 15.71 | 2.64 µs |
| 39 项 `Arc::ptr_eq` 线性扫描 | 12.11 | 2.03 µs |

三者**都远小于收益**（350 ms 量级）。所以选数组的理由不是"哈希太慢"，而是：

1. 记账在结构上消失——`recurse` 里多一个 `match` 分支，没有哈希、没有额外分配，
   非共享节点一次都不查；
2. `memo: &[Vec<F>]` 是只读切片，天然按 chunk 隔离，多线程下无需任何同步原语；
3. 下标由构造期分配，**跨求值上下文不可能误用**（见 §3.3 Q4）。

**明确否掉的**：`Arc::strong_count > 1` 自动识别共享。链的每一个后代也都被复制了
14 次，`strong_count` 会在整棵子树（5(k−1) 个节点）上全部为真，等于给每个节点
都开一个 memo 槽。

### 3.3 用户点名的五个问题，逐条给出代码级答案

**Q1 · 共享节点能否在 AST 构建时可靠识别？** 能，位置唯一：
`plonk/prover.rs:669-700`，prover 用 `expr.evaluate(&poly::Ast::ConstantTerm, …, &|a,b| a*b, …)`
把**每条约束的 `Expression`** 折成一棵 `Ast`，193 条。链的判定沿用 22 号 §8.1 的
**结构判据**（左斜 `Product` 脊；最左叶 `Fixed(c,0)`；其余操作数是 `Constant(r) − Fixed(c,0)`；
`k−1` 个根恰为 `1..=k` 去掉一个；节点数 `1+5(k−1)`），**不靠哈希**。
可行性轮的 `identity_fail = 0` 已经把 I1–I4 四条身份条件全部验过：

| 条件 | 结论 | 依据 |
|---|---|---|
| 同一物理固定列 | ✅ | 链的唯一 `Fixed` 列号即 key 的第一分量 |
| 零旋转 | ✅ | `as_chain` 结构上只接受 `Rotation::cur()` |
| 同求值域 / 同 coset | ✅ | 一次 `evaluate` 内只有一个 `domain`、一个 `chunk_size` |
| 不依赖 advice / instance / challenge | ✅ | 链内只允许 `Constant` 与 `Fixed`，结构上排除了其余 |

**Q2 · 每次 `evaluate` 是否重新构造 AST？** 是：**每证明一次，不是每 chunk 一次**。
F2 记录到每证明 13 次 `evaluate`（12 次是 permutation/lookup 的小树，10–66 节点），
**商那一次是 16345 节点**；它由 `vanishing/prover.rs:83-85` 构造一次，
随后 `evaluate` 内部才按 chunk 循环。`Ast` 的构造发生在 `prover.rs` 的 `flat_map` 里，
**每证明只跑一遍**。所以 interning 的开销是每证明一次：实测 181 次 map 操作 =
**3.1 µs**（`F4B_INTERN`）。顺带收益：每条链的子树只建一次，其余 13 次直接拿 `Arc`，
省掉 13 次子树构造。

**Q3 · memo 的生命周期能否覆盖全部 170 个消费者？** 不能跨 chunk——
`multicore::scope` 为每个 chunk 起一个任务（`evaluator.rs:256-269`），每个任务只处理
自己那段下标。但**所有消费者都在同一棵树里**，所以"同一任务内 39 个槽"就够。
生命周期 = 一个 chunk 任务，任务结束即释放。

**Q4 · 不同 `poly_len` / 求值域 / coset 是否需要不同缓存？** 结构上不可能串味：
槽号是在**这一次**构造里分配的局部编号，`Ast` 也是这一次构造的产物，
下一次 `evaluate` 用新的 `Ast` 和新的 `memo`。**不存在可被误复用的全局状态。**

**Q5 · 多线程下共享结果如何安全管理？** 每个 chunk 任务在**走树之前**把自己的 39 个
槽填满，走树期间 `memo` 是 `&[Vec<F>]`（不可变）。因此：无锁、无原子、无 `RefCell`。
`shared: &[Arc<Ast>]` 只读共享。8P 下 32 个任务各持 39 × 512 × 32 B = 639 KiB，
峰值 20 MiB；1P 下 39 × 4096 × 32 B = **5.1 MiB**。

### 3.4 借出，还是拷出

`recurse` 现在返回 `Vec<F>`。memo 里的值被 14 个消费者共用，**不能交出所有权**。
两条路：

| 路线 | 机制 | 预期代价 | 风险 |
|---|---|---|---|
| **P1（主）** | `recurse` 返回 `Val<'a,F> { Owned(Vec<F>), Borrowed(&'a [F]) }`；**只有 `Ast::Memo` 产生 `Borrowed`** | 0 | 动到每个分支的返回类型 |
| **P0（退路）** | `Ast::Memo(slot) => memo[slot].clone()`，其余一行不改 | 170 次拷贝 ≈ 4–9 ms（0.2–0.3%） | 无 |

P1 的可行性依赖一个结构事实：**168 个消费者里没有一个需要 materialize**。
链位于约束根，约束根在全局树里的父节点是 `DistributePowers` 的一个 term，而
`DistributePowers` 只读 term（`evaluator.rs:229-239`，`acc = acc*y + term`）。
所以 `Borrowed` 从 memo 一路流到 `acc += term`，**零复制**。
真正要处理的只有 2 个非根消费者（它们不 interning，仍走旧路径）。

若 P1 的改动面被证明过大（例如 `Val` 渗透到 `BasisOps`），退到 P0：
收益从 ~5.4% 掉到 ~5.1%，仍远高于 1.0% 的门槛。**验收按实际走的那条路报，
两条都允许多报一次残差**（复制字节数）。

---

## 4. 求值策略与改动清单

```
构造（每证明一次）
  prover.rs: 对每条约束的 Expression
      ├─ 是链 && 消费者 ≥ 2 && 位于根 → 分配 slot，建一次子树，产出 Ast::Memo(slot)
      └─ 否则                        → 照旧 expr.evaluate(...)
  产出：Vec<Ast>（193+ 项） 与 shared: Arc<Vec<Ast>>（39 项）

求值（每 chunk 一次，在任务内部）
  for slot in 0..39:  memo[slot] = recurse(shared[slot], ctx)     // 248 个乘
  walk(tree)                                                       // Memo 只读
```

改动的六处，全部在 crate 内部，**不动约束系统**：

| 文件 | 位置 | 改动 |
|---|---|---|
| `poly/evaluator.rs` | `enum Ast` | 加 `Memo(u16)`（一个变体，槽号 < 65536，39 够用） |
| `poly/evaluator.rs` | `recurse` | 返回类型 `Vec<F>` → `Val<'_,F>`；`Memo` 分支读 `memo[slot]` |
| `poly/evaluator.rs` | `evaluate_inner` | 每任务预计算 39 个槽，再把 `&memo` 传给 `recurse` |
| `poly/evaluator.rs` | `f2_ast_stats` | 加 `Memo` 分支（否则 F2 会 panic；计数记为 0 个 `poly` 叶子） |
| `plonk/prover.rs` | 669-700 | `expressions` 先 `collect::<Vec<_>>`，再 interning；产出 `shared` |
| `plonk/vanishing/prover.rs` | `construct` | 形参 `impl Iterator` → `Vec<Ast>` + `Arc<Vec<Ast>>` |

**P1 特有的两处**：`Add`/`Mul` 在"一侧 `Borrowed`、另一侧 `Owned`"时，把结果写进
**`Owned` 那一侧**（域加法与乘法可交换，值不变）。这个交换**只在借出侧出现时启用**，
其余情况保持今天的 lhs-first 顺序——否则测量里会混进"操作数顺序"这个第二因子。
`Scale`/`Negative` 遇到 `Borrowed` 必须新分配（168 个消费者里一个都没走到）。

---

## 5. 预测（冻结，看到任何数字之前）

| # | 预测 | 依据 |
|---|---|---|
| **P1** | 1P 完整 Prove 下降 **4.2%–5.6%**，点估计 **≈4.8%** | 下界＝621/627 × 4.25%（只算 product）；上界＝22 号 §8.5 的 ×1.28 → 5.45% |
| **P2** | quotient 阶段（`e2_add("quotient")`）下降 **9%–12%** | 下界 621 × 0.1861 = 115.6 ms 对 1276 ms；上界 806 × 0.1861 = 150 ms |
| **P3** | **proof 字节完全不变**：7264 B / `0xa6bcd171d69e969a` | 只改"算几次"，不改任何域运算的结果 |
| **P4** | `h` 多项式的系数**逐位相同** | 表达式求值顺序变化不改变域元素的值（非结合性不适用：域加法/乘法可交换、可结合） |
| **P5** | verify 时间不变（±2%） | 验证方不涉及 `Ast` |
| **P6** | 每 chunk 少 **≥ 621 次分配**，1P 全证明少 **≥ 2484 次** | 每个消失的乘至少带一次 `Constant` 叶子分配；`Fixed` 叶子那份也一并消失 |
| **P7** | 峰值 RSS 增加 **≤ 16 MiB** | 39 × 4096 × 32 B = 5.1 MiB，加预留 |
| **P8** | 8P 点估计比 1P 低 0–25% | 同一份元素工作量被 32 个 chunk 摊薄，记账与内存压力上升；**8P 不作门槛** |

---

## 6. 验收协议与门槛

**A/B 协议**（沿用 H6/H7 的同会话交错配对，不跨会话比绝对值）：

- 同一二进制、同一 seed、同一 bundle；`A` = 基线，`B` = H6+F4-A；
- 逐对交错 `A B A B …`，1P **≥ 20 对**，8P **≥ 20 对**（样本量按 H7 的教训先定，
  不到 10 对时分不清 30 ms）；
- 统计量：每对的比值 `B/A`，报中位数与 bootstrap 95% CI；另做符号检验。

**门槛**：

| 门 | 判据 | 不过怎么办 |
|---|---|---|
| **F4A-1 正确性** | proof 字节相同 **且** `h` 系数逐位相同 **且** verify 通过 | 立即回滚，不讨论性能 |
| **F4A-2 上下文** | 39 个槽全部命中、无空槽；`Ast::Memo` 只出现在根位置 | 视为实现缺陷，修到为零 |
| **F4A-3 1P 收益** | 完整 Prove 下降 **≥ 1.0%**，且 95% CI **排除零** | No-Go，改 P0 再测一次；仍不过则关档 |
| **F4A-4 内存** | 峰值 RSS 增量 ≤ **64 MiB**，分配次数不增加 | 视为失败（与 P7 的预测做对照） |
| **F4A-5 8P** | **只报告**，不设门槛（用户明确：8P 不要求同幅收益） | — |

**1.0% 是工程决策门槛，不是对性能的预测。** 预测是 4.2–5.6%；若实测落在 1–4%，
说明"×1.28 的模型"高估了，按实测记录，不回头改预注册。

---

## 7. 失败条件（No-Go 的完整清单）

1. 任何一份 proof 的字节或 `h` 系数不同（哪怕是最后一位）；
2. 39 个槽中出现空槽，或 `Ast::Memo` 出现在非根位置；
3. 复用代价实测 > 20 ms（即 §3.2 的记账估计错了两个数量级）；
4. 1P 收益 < 1.0%，或 CI 跨零；
5. 峰值 RSS 增量 > 64 MiB；
6. 需要改动 `ConstraintSystem` / `Gate` / `Selector` / 压缩结果才能实现。

---

## 8. 三条硬约束（用户指定）

**一、不做通用 CSE。** 只复用 F4 普查确认的 39 条恢复链。键是
`(固定列号, k)`，来自结构判据，不是任意子树的 hash-consing。
`Arc::strong_count`、子树哈希、`Borrowed` 传播到叶子——**全部不做**。

**二、不改约束系统。** 193 条约束、56 → 15 个 selector 列、压缩结果、
证明协议、VK 全部不变。本改动只影响"同一个表达式算几次"。

**三、不同时优化缓冲区。** 消费者各自出自己的输出（P0 就是拷出），
**不用 H7 的对象池、缓冲区复用、或任何物化优化**。否则无法把收益归因到 DAG。
P1 的 `Val` 只是"允许借出"，不是"允许池化"——`Owned` 分支的分配行为与 H6 完全一致。

---

## 9. 复现与产物

```bash
# 预注册（本文件）单独提交，早于任何实现代码
# 实施轮次将产生：
#   experiments/transaction_e2e/vendor/halo2_proofs-0.4.0-f4a.patch
#   results/transaction_e2e/f4a_impl/{ab_1p.log,ab_8p.log,h_hash.txt,summary.md,build.txt}
#   scripts/analysis/f4a_ab.py
```

上一轮的探针与数字（`f4b_probe/`）可原样复现：

```bash
cd experiments/transaction_e2e/harness
git apply ../vendor/halo2_proofs-0.4.0-f4b-probe.patch
cargo build --release --offline --features f4b --bin orchard-e2e
RAYON_NUM_THREADS=1 ./target/release/orchard-e2e --mode f4b --reps 200 \
  > ../../../results/transaction_e2e/f4b_probe/probe_1p.log
python scripts/analysis/f4b_probe_report.py results/transaction_e2e/f4b_probe/probe_1p.log \
  --out-dir results/transaction_e2e/f4b_probe
```

---

## 10. 补充说明（2026-11-05，实施前追加，第 1–9 节不改）

预注册已于 `1e17ffd` 冻结。本节只做三件用户指定的事：**统一计量单位**、
**给两个数字换名**、**把实施不变量列成清单**。不新增假设，不放宽门槛。

### 10.1 三层计量单位，不得混用

上一轮 22 号 §8.4 的初稿把 `Δ(4096)` 当成"每元素"的价格算总量，却在"每消费者"
处引用了 **chunk 级**的数值，于是 `1.1 µs/消费者` 与 `0.75 ms / 170 消费者`
（= 4.40 µs）自相矛盾。今后固定三层：

| 层 | 单位 | 谁用 |
|---|---|---|
| 微基准 | **ns / 元素**、**ns / chunk** | 内核价（`fused − in_place` 这类差值） |
| 单消费者 | **µs / 消费者** = 该次 `evaluate` 涉及的全部 chunk 之和 | 复用代价 |
| 完整证明 | **ms / proof** | 收益与门槛 |

1P 几何：`chunk_size = 4096`、`num_chunks = 4`，故
`1.1 µs/chunk × 4 = 4.4 µs/consumer`，`4.4 µs × 170 ≈ 0.748 ms`。
**`0.75 ms` 是对的，错的是把它标成"每消费者 1.1 µs"。**

### 10.2 621 与 806 是两种量，必须分开命名

| 名字 | 值 | 含义 |
|---|---:|---|
| `structural_mul_eliminations` | **621** | 代码里真正不再执行的整块乘法：根位置口径，`869 − 242` 再减去 2 个嵌套消费者所在链的残余 |
| `product_equivalent_saved_work` | **806** | 把每 chunk 模型测出的总节省时间，按 **product 单价**归一化得到的等价量；它包含 `Sum`、分配等乘法以外的工作 |

两者的关系是**换算**，不是"多删了 185 个乘法节点"。最终结果表、摘要、对外表述
一律用这两个名字；**`869` 只用作出发点的替换模型总量，`627` 只出现在 22 号的历史
叙述里，不再作为收益口径**。

### 10.3 收益只认同会话配对

本机已实测到跨会话漂移：内核价 12.06 → 24.32 ns/元素（×2.0）、
Prove 2725.8 → 7835 ms（×2.9）。因此：

- **预测百分比与最终收益，一律来自同一会话、同一二进制、逐对交错的 A/B 数据。**
- 不得用本轮的 353 ms 去除以其它会话的 H6 证明时间再宣称收益。
- 跨会话绝对值只作历史参考；跨会话只允许比较**无量纲比率**（如 `×1.28`、`621/869`）。

实现方式：`ORCHARD_F4A` 环境变量在**同一个二进制内**开关 interning，
`A` = `ORCHARD_F4A=0`、`B` = `ORCHARD_F4A=1`，两者共用 seed、bundle、二进制，
把构建漂移也一并消掉（H6/H7 只消了运行漂移）。

### 10.4 实施不变量（逐条核验，写进 `summary.md`）

| # | 不变量 | 核验方式 |
|---|---|---|
| **V1** | 全部槽位在当前 chunk 内完成初始化 | 计数器：`slots_initialised == slots_allocated`，逐 chunk 累加 |
| **V2** | 读 memo 时不允许出现未初始化槽 | 预计算循环覆盖 `0..n_slots`，读侧无 `Option`；另加 debug 断言 |
| **V3** | 同一槽位不对应不同求值域 / coset / 上下文 | 槽号是本次构造的局部编号；`Ast` 每次 `evaluate` 新建 |
| **V4** | memo 生命周期不超过当前 chunk evaluator | 类型上：`memo` 是任务栈上的 `Vec<Vec<F>>` |
| **V5** | 消费者不得修改 memo 中的向量 | `Borrowed(&[F])` 只读；`Val` 不提供 `&mut` |
| **V6** | 不符合共享条件的 11 条链保持原有执行方式 | 它们**不分配槽**（构建期按 `occ ≥ 2` 过滤），走的仍是原 `recurse` 路径 |
