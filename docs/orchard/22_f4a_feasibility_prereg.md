# 22 · Stage F4 · F4-A 可行性预注册：共享 selector 恢复链能不能付得起

> **本文件第 1–7 节在写任何一行 F4-A 代码之前定稿并单独提交。** 第 5 节的预测、第 6 节的
> Go/No-Go 门槛在看到任何 F4-A 数字之前固定；探针结果落回第 8 节，**不回头改预测**，
> 落空的部分另记"预测失败"。
>
> 本轮只做**零改动探针**：不建 DAG、不写缓存、不改 `recurse`、不改任何表达式的构造路径。
> 探针只读已经压缩完的约束系统，并做两个单线程微基准。**没有"顺便优化一下"的余地。**

---

## 0. 一句话

F4 找到的 869 个整块乘，是 `compress_selectors` 把 `q` 换成一个 5 次乘法的恢复链、
而同一道门里 14 条约束各自 `clone()` 了一份造成的。本轮要回答的不是"能不能省 627 个乘"，
而是**"省下它们要付什么"**：共享结果要么可只读复用，要么每个消费者各materialize一份。

后者不是免费的——H7 刚刚证明过，这类代价能把 4% 的毛上界吃到 1.45%。

---

## 1. 位置：第四类成本

F3/F4 到目前为止测到的是四类互不相同的成本，优化对象完全不同：

| 类别 | 问题 | 实验 | 结果 |
|---|---|---|---|
| 不必要的**计算** | 取负被翻译成整块乘 −1 | **H6** | 40.2% 整块乘消失，prove **−14.57%**，已冻结 |
| 不必要的数据**物化** | 每个节点都要一块新 `Vec<F>` | **H7** | prove **−1.45%**（1P，CI 排除零），原型保留 |
| 重复的**计算**（叶子） | 同一个叶子被求值 31.9 次 | **LC** | 可避免部分 12.8%，上界 0.52%，**关闭** |
| 重复的**计算**（子树） | 同一条恢复链被 14 个消费者各算一遍 | **F4-A（本轮）** | 毛上界 **4.28%**，待验证 |

必须把 F4-A 与 LC 分开，否则会误以为"LC 已经否掉了这件事"：

- LC 想缓存的是**叶子**（`Poly` 的字节），结论是 85.5% 的叶子位于必须可写的位置，缓存拿不到。
- F4-A 想共享的是**内部节点**（一个 5 乘法的表达式子树），它不对应任何多项式，
  只存在于求值过程中，而且它**只需要被读**。

LC 的死因（"消费者要就地写"）正是 F4-A 要量化的代价（F4A-2/F4A-3），不是同一件事。

也把它与"合并 Gate"分开：合并 Gate 省不了表达式（21 号文档 §5.5 已否），
F4-A 一条约束都不合并，193 条约束原样保留。

---

## 2. 机制：两种形状，只验证第一种

### 2.1 现状

`compress_selectors` 把虚拟 selector `q` 替换成
`q_col · ∏_{i=1..k-1} (i − q_col)`（k = 该列装的 selector 数），并且对**每一处** `q` 出现
`clone()` 一份。同一道门的 14 条约束，于是就有 14 棵一模一样、各自求值的子树：

```
Constraint 1 ── Product( Chain(k=6, 5 muls), body_1 )
Constraint 2 ── Product( Chain(k=6, 5 muls), body_2 )
   ...
Constraint 14 ─ Product( Chain(k=6, 5 muls), body_14 )
```

### 2.2 形状 S1（用户指定，本轮唯一验证对象）

```
              Shared Chain  (算一次，只读)
                    │
        ┌───────────┼───────────┐
        │           │           │
   Consumer 1   Consumer 2 ... Consumer 14
   各自出输出    各自出输出      各自出输出
```

**这不是把 14 条约束合成 1 条**，193 条约束全部保留，只是中间表达式不再重复求值。

### 2.3 形状 S2（本轮只记录，不验证、不实施）

因为 `y` 权重（`DistributePowers`）加在每条约束**之外**，若 14 个消费者的链位置相同，
则存在严格等价的代数提取：

```
Σ_i y^i · Chain · body_i  =  Chain · Σ_i y^i · body_i
```

S2 把"13 个乘法"变成"13 个加法"，**且不引入 DAG、不延长生命周期、不需要缓存**。
它比 S1 便宜，但它是一次**因式分解**（改写表达式构造），不是共享机制，而且 `y` 幂次
必须逐位对齐才能保证证明字节不变。

本轮的任务只是**量出 S2 的结构前提是否成立**（多少消费者的链位于约束根、
且根的另一侧与链互不相交）。**S2 若成立，必须另立预注册**，因为它的实现位置
（`prover.rs` 的表达式组装）与 S1（`evaluator.rs`）完全不同，混在一起无法归因。

---

## 3. F4A-1：什么叫"相同的链"

**不能凭 AST 哈希相同就认定可以共享。** 四条必须逐条检查，任一条不成立即该组不能共享：

| # | 条件 | 本轮怎么查 |
|---|---|---|
| I1 | 物理固定列相同 | 链内所有 `Fixed` 叶子的 `column_index` 必须相同且唯一 |
| I2 | Rotation 相同 | 链内所有 `Fixed` 叶子的 `rotation` 必须为 0（零次 rotation 之外的链一律不共享） |
| I3 | 求值域 / coset 相同 | 全电路只有一个 `EvaluationDomain`、一条 coset 链；断言 `domains == 1`、`extended_k` 唯一 |
| I4 | 不依赖别的上下文 | 链内**不得**出现 `Advice` / `Instance` / `Selector` 叶子；不得出现 `Scaled`（标量必须是 `Constant`，避免不同 challenge 折叠出同形异值） |

I4 是本轮最容易出错的一条：`y`/`beta`/`gamma` 在 0.4 里是在构造 `Ast` 时**折叠成常量**的，
所以同一个 `Expression` 在不同 challenge 下会求值不同——但本轮共享的是**同一次求值内部**的
中间结果，14 个消费者共用同一组 challenge 值。**链里没有任何 challenge**，
所以这一点是结构性成立的，而不是靠"恰好一样"。

### 3.1 身份的实现方式

对每个子树算一个**规范化结构键**（`Sum`/`Product` 的子节点按键排序——域上可交换，
逐位精确），键包含：节点种类、`Constant` 的字节、`Fixed` 的 `(column_index, rotation)`、
以及子键。相同键 ⇒ 结构完全相同 ⇒ 在 I1–I4 成立时可共享。

排序归一化会**合并掉**原本左右顺序不同的写法，这对"能不能共享"是正确的（可交换），
但会**高估**可共享范围（求值顺序不同其代价相同，收益不受影响）。

---

## 4. 探针的四个工作块（零改动）

| 块 | 内容 | 是否触碰求值路径 |
|---|---|---|
| **W1 定位** | 在压缩后的 `ConstraintSystem` 上枚举链、消费者、每链的 `(k, occ)`，出 `Constraint × Chain × Consumer` 表 | 否，keygen 内只读 |
| **W2 安全** | 对每个 occ ≥ 2 的链检查组身份（I1–I4），并统计"若共享，谁会就地写" = 全部消费者（H7 规则 2 的必然结果） | 否 |
| **W3 代价** | 微基准：`in-place mul`（已有 owned 缓冲区）vs `fused out-of-place mul`（fresh alloc）vs `fused into reused buffer`，几何 4096 / 16384 | 否，独立 bench，不进 prove |
| **W4 无扰动** | 开/关探针各跑一次 prove，比 7264 B 与指纹；还原 vendor 后 `f0_verify_frozen.py` | 否 |

W2 的产出是一张**风险表**，不是一次优化：`hazard = 必须在共享结果上就地写的消费者数`。
在 H7 的规则 2 下（"有一侧已 `Owned` → 就地累加到它上面"），这个数等于消费者数本身，
**所以共享的前提是给每个消费者一个新的输出缓冲区**——这正是 W3 要定价的东西。

### 4.1 记录项（预先固定，不允许事后加）

| 指标 | 单位 | 作用 | 来源 |
|---|---|---|---|
| `chains` / `chains_shared` | 条 | 结构规模 | W1 |
| `saved_muls` = Σ(occ−1)(k−1) | 整块乘 | 收益 | W1 |
| `shared_bytes` | KiB/链 | 常驻 | W1 |
| `consumers` = Σ occ | 个 | 复用宽度 | W1 |
| `root_factorable` | 个 | S2 的结构前提 | W1 |
| `identity_fail` | 组 | F4A-1 | W2 |
| `hazard_consumers` | 个 | F4A-2 | W2 |
| `ns_per_elem` in-place / fused / reused | ns | F4A-3 | W3 |
| `delta_per_consumer` = fused − in-place | ns/元素 | **复用代价** | W3 |
| `proof_bytes` / `fnv` | B / 16 进 | 无扰动 | W4 |

---

## 5. 预测（可失败的）

数值全部来自 21 号文档与 H6/H7 的冻结记录，**没有任何 F4-A 数据**。

| # | 预测 | 若失败意味着 |
|---|---|---|
| **P1** | 链 **52** 条（50 个装列 simple + 2 个 deg-9 simple）；4 个 complex selector 不产生链 | 压缩的替换模型记错了，先回头核 21 号 §2.2 |
| **P2** | `Σ(k−1) = 242`，`Σ occ·(k−1) = 869`，`saved_muls = 627` | 与 F4 的候选 A 不自洽，候选 A 的上界作废 |
| **P3** | occ ≥ 2 的链覆盖 869 的全部（即没有哪条链只出现在一条约束里却被算进 869） | 存在"只出现一次"的链，节省被高估 |
| **P4** | `root_factorable / consumers` ≥ 90%：链在压缩后的位置是约束根 `Product` 的直接操作数 | S2 不可用，只剩 S1 这条更贵的路 |
| **P5** | 身份检查 I1–I4 **零失败** | 出现跨列/跨 context 的"同形异源"，共享范围要缩 |
| **P6** | `delta_per_consumer` **≤ 0**（chunk 4096）：`collect(zip)` 的融合写在 128 KiB 上比"拷贝后就地乘"更快（D4：12.287 vs 14.289 ns/元素），因此 out-of-place 不劣于 in-place | delta > 0 ⇒ 复用代价为正，按 4.28% 的毛上界扣 |
| **P7** | 净收益 **3.0%–4.3%**：627 × 0.1861 ms = 116.7 ms，扣掉 DAG 记账（每消费者一次指针比较，估 ≤ 2 ms）与 4 个 chunk 的额外分配 | 净收益 < 1% ⇒ No-Go，见 §6 |
| **P8** | 探针二进制与 F4 探针**互不影响**，proof 7264 B / `0xa6bcd171d69e969a` | 只读性被破坏，本轮全部作废 |

P2 是可逐位闭合的：`Σ occ·(k−1) − Σ(k−1) = Σ(occ−1)(k−1)`，探针里直接 assert。

---

## 6. Go / No-Go（预先固定，不随结果改）

### Go：进入 F4-A 实施预注册

四条**全部**成立：

| 门 | 条件 |
|---|---|
| **F4A-A** 无扰动 | W4 的 proof 7264 B + 指纹 `0xa6bcd171d69e969a`；`f0_verify_frozen.py` PASS |
| **F4A-B** 身份 | 所有 occ ≥ 2 的链 I1–I4 零失败；`identity_fail == 0` |
| **F4A-C** 只读 | 消费者可以只读共享（`hazard_consumers` 全部可由"各自出输出"化解，不存在必须原地写共享缓冲区的消费者） |
| **F4A-D** 净收益 | 已计入 collect/分配/搬运后，端到端估计 **≥ 1.0%** |

### No-Go：关闭 F4-A

任一条成立：

- 出现求值上下文不一致（I1–I4 任一失败，且无法通过拆分链解决）；
- 共享必须复制大量数据（`delta_per_consumer` > 0 且按字节折算吃掉 > 50% 的 116.7 ms）；
- 额外 collect / allocation 吃掉大部分收益；
- 净收益估计 < **1.0%**。

**1.0% 的来历**（与 H7 同一把尺）：H6 之后 1P prove 是 2725.8 ms，1% = 27.3 ms。
F4-A 的毛上界是 116.7 ms（4.28%），但它是**外推**（0.1861 ms/整块乘来自 H6 的 `Scale`
价格，`Product` 未必同价），且 DAG 记账完全未测量。按 S1 的最坏假设
（每个消费者一次额外分配 + 一次指针查表）预留一半余量，仍有 2% 以上，
所以 1% 是"值得投入"的下限，不是预测值。

---

## 7. 本轮不回答的问题

| 问题 | 为什么不在这一轮 |
|---|---|
| DAG 在 `recurse` 里怎么落地（`Arc` 复用 + 按指针去重） | 这是实施，不是可行性；先看 F4A-C 是否成立 |
| 缓存用什么结构（`FxHashMap<*const Ast, Vec<F>>` / 每 chunk 一个槽位） | 记账成本必须实测，不能在探针里假设 |
| S2 因式分解怎么改 `prover.rs` | 形状不同、位置不同，必须独立预注册 |
| 推广到"通用 CSE" | 用户明确禁止；本轮只针对 selector 压缩引入的链 |
| 与 H6/H7 同栈后的复合收益 | 三者改的是不同层次，叠加要单独一轮交错实验 |
| 8P 下的表现 | 本轮不测时间，P7 是外推；并行行为留给实施验收 |

---

## 8. 探针结果

_（本节在 W1–W4 跑完后填入，填入时间见提交记录；第 1–7 节不改。）_

---

## 9. 复现

```bash
cd experiments/transaction_e2e/harness
git apply ../vendor/halo2_proofs-0.4.0-f4a-probe.patch
cargo build --release --offline --features f4a --bin orchard-e2e
./target/release/orchard-e2e --mode f4a --reps 200 > ../../../results/transaction_e2e/f4a_probe/probe_on.log
ORCHARD_F4A=0 ./target/release/orchard-e2e --mode f4a --reps 0 > ../../../results/transaction_e2e/f4a_probe/probe_off.log
python scripts/analysis/f4a_probe_report.py results/transaction_e2e/f4a_probe/probe_on.log \
  --out-dir results/transaction_e2e/f4a_probe

# 还原
git checkout -- experiments/transaction_e2e/vendor/halo2_proofs-0.4.0/src/lib.rs \
                experiments/transaction_e2e/vendor/halo2_proofs-0.4.0/src/plonk/keygen.rs
rm -f experiments/transaction_e2e/vendor/halo2_proofs-0.4.0/src/f4a.rs
python scripts/analysis/f0_verify_frozen.py    # 期望 RESULT: PASS（58 个文件）
```
