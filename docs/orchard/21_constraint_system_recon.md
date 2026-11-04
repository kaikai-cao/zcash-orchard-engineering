# 21 · Stage F4 · 约束系统普查：成本不在门的数量里

> 只读普查，不改电路。probe 在 keygen 处 dump 两次（`compress_selectors` 前 / 后），
> 两次 dump 之间证明字节完全不变（`0xa6bcd171d69e969a`，与 H6、H7 记录逐位相同）。
> 本篇不含任何预注册、不含任何优化实施；第 5 节的候选都是"普查给出的线索"，
> 收益一栏凡是外推的都标了外推。

---

## 1. 一句话

**Orchard 的配置本身没有冗余可捡，但 selector 压缩给证明者开了一张 5.93% 的账单，
其中 4.28% 是同一道门里重复算的。**

三条结论，按证据强度排：

| 结论 | 证据 |
|---|---|
| 56 个 selector → 15 个固定列，**压缩已在规则上限**，再压不动 | 52 个 simple 里 50 个进 9 组，每组 `max_deg−1 + k = 9` 恰好顶满；另 2 个（deg 9）与 4 个 complex 按规则各占一列；用同一个贪心做 best-fit 重排，仍是 11 列 |
| **一列固定列只值 44.2 µs**，29 列合计 1.28 ms = 证明的 0.047% | 同一二进制里 `eval_polynomial` 在 n=2048 上的实测（200 次，中位数） |
| **压缩替身（`q → q·∏(rᵢ−q)`）给商多项式树加了 869 个整块乘 = 5.93% 的证明** | 逐分量预测 post 树并与 F2 独立走查**逐位相等**（§2.2）；价格用 H6 的 490.8 ms / 2638 外推 |

第三行是这轮唯一真正的发现，而它**不是电路设计问题，是 halo2 的压缩机制问题**：
56 个逻辑 selector 挤进 15 列，代价是每一处 `q` 出现都要展开成 `k−1` 次乘加。
同一个门的 14 条约束共用同一个 `q`，于是这条恢复链被算了 14 次。

---

## 2. 做了什么

### 2.1 两个记录点

补丁 [`halo2_proofs-0.4.0-f4-recon.patch`](../experiments/transaction_e2e/vendor/halo2_proofs-0.4.0-f4-recon.patch)，
`+479/−0`，3 个文件，**没有一处改动 `ConstraintSystem` / `Gate` / `Selector` / 布局**：

| 文件 | 行数 | 内容 |
|---|---:|---|
| `src/f4.rs` | +469 | 新增：只读普查（门表达式走查、selector 激活矩阵、压缩前后对照、`eval_polynomial` 微基准） |
| `src/lib.rs` | +2 | 模块声明 |
| `src/plonk/keygen.rs` | +8 | 两个 hook，`compress_selectors` 前后各一次（vk 与 pk 各调一次，模块自己做去重） |

为什么必须挂在 keygen：**虚拟 selector 和它的激活矩阵只存在于压缩之前**，
`compress_selectors` 把 `Vec<Vec<bool>>` 消费掉之后，56 个逻辑 selector 就只剩
`selector_map` 里的 15 个列号了。

### 2.2 对着 F2 对账：差值是压缩，且能算准

同一个 `ConstraintSystem`，压缩前 3186 个节点、压缩后 7531 个。差值不是噪声，
是 `compress_selectors` 的替换规则，可以逐分量预测：

每次 `q` 出现被替换成 `q·(r₁−q)···(r_{k−1}−q)`（k = 该列装的 selector 数），
净增 **product / sum / negated / constant 各 k−1 个、fixed k 个、selector −1 个**，
节点净增 `5(k−1)`。

| 分量 | 压缩前（普查） | 压缩带来的 | 预测 | F2 独立走查 | |
|---|---:|---:|---:|---:|---|
| constant | 133 | +869 | 1002 | 1002 | SAME |
| product | 555 | +869 | 1424 | 1424 | SAME |
| sum | 645 | +869 | 1514 | 1514 | SAME |
| negated | 394 | +869 | 1263 | 1263 | SAME |
| fixed | 99 | +1062 | 1161 | 1161 | SAME |
| selector | 193 | −193 | 0 | 0 | SAME |
| nodes | 3186 | +4345 | 7531 | 7531 | SAME |

**六行全中，一位不差。** 这条对账的意义：F2 数的是"证明者实际走的树"，
F4 数的是"电路作者写的树"，两者之间的全部差异归因于一个已知机制，
而不是"两个数不一样"。后面所有关于压缩代价的推理都站在这个等式上。

---

## 3. 第 1 项：Gate 结构普查（55 门 / 193 约束）

### 3.1 总量

| 项 | 值 | 说明 |
|---|---:|---|
| Gate | **55** | 与 F2 一致 |
| 约束多项式 | **193** | 每条都是 `q · body`，所以 selector 叶子恰好 193 个 |
| 表达式节点（作者形态） | **3186** | 其中 sum 645、product 555、negated 394、scaled 199、constant 133、fixed 99、advice 968 |
| 最大深度 | 14 | `Running sum coordinates check` |
| 次数分布（按约束数） | deg 2:16 / 3:104 / 4:27 / 5:15 / 6:23 / **9:8** | **只有 8 条约束到 9 次**，全部来自 `ecc::mul_fixed` 的 3 道门 |
| advice / instance / fixed | 10 / 1 / 14 | configure 阶段 |
| permutation / lookup / constants | 15 / 3 / 1 | |
| selector | 56 | 52 simple + 4 complex |
| 最高启用行 | 1777 | 与 08 号文档的 1779 行布局吻合 |

按来源分：`orchard` 25 门 / 93 约束 / 826 节点，`halo2_gadgets` 30 门 / 100 约束 / 2360 节点。
**四分之三的表达式在 gadget 里**，不在 Orchard 自己的 `circuit.rs` 里——这一条决定了
"改电路"实际上等于"改上游 gadget"。

### 3.2 逐门表

`con`=约束数，`nod`=节点数，`dep`=最大深度，`deg`=最高次数，`sum/mul/neg/scl`=加法/乘法/取负/缩放节点数，
`cst`=常量、`fix`=固定列叶子、`adv`=advice 叶子。
`neg` 与 `scl` 分列，是为了守住 H6 的语义：**`Negated` 是取负、`Scaled` 才是乘常数**，
H6 之后前者映射成 `Ast::Neg`（免费），后者仍是整块乘。旧口径把两者并成 "Scale(−1)"，
本表不再那样数。

| # | gate | con | nod | dep | deg | sum | mul | neg | scl | cst | fix | adv | 来源 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 0 | Orchard circuit checks | 4 | 35 | 4 | 3 | 5 | 8 | 5 | 0 | 2 | 0 | 11 | orchard/circuit.rs:335 |
| 1 | Field element addition | 1 | 8 | 3 | 2 | 2 | 1 | 1 | 0 | 0 | 0 | 3 | orchard/circuit/gadget.rs |
| 2 | Short lookup bitshift | 1 | 9 | 4 | 3 | 1 | 2 | 1 | 1 | 0 | 0 | 3 | gadgets/utilities/lookup |
| 3 | witness point | 2 | 34 | 6 | 5 | 4 | 10 | 4 | 0 | 2 | 0 | 12 | gadgets/ecc/chip |
| 4 | witness non-identity point | 1 | 15 | 6 | 4 | 2 | 4 | 2 | 0 | 1 | 0 | 5 | gadgets/ecc/chip |
| 5 | incomplete addition | 2 | 49 | 6 | 4 | 12 | 7 | 9 | 0 | 0 | 0 | 19 | gadgets/ecc/chip/add_incomplete |
| 6 | complete addition | 12 | 225 | 8 | 6 | 39 | 50 | 35 | 0 | 9 | 0 | 80 | gadgets/ecc/chip/add |
| 7 | q_mul_1 == 1 checks | 1 | 22 | 10 | 4 | 5 | 3 | 4 | 1 | 0 | 0 | 8 | gadgets/ecc/chip/mul |
| 8 | q_mul_2 == 1 checks | 6 | 135 | 11 | 4 | 30 | 18 | 26 | 7 | 2 | 0 | 46 | gadgets/ecc/chip/mul |
| 9 | q_mul_3 == 1 checks | 4 | 107 | 11 | 4 | 24 | 14 | 21 | 6 | 2 | 0 | 36 | gadgets/ecc/chip/mul |
| 10 | q_mul_1 == 1 checks | 1 | 22 | 10 | 4 | 5 | 3 | 4 | 1 | 0 | 0 | 8 | gadgets/ecc/chip/mul |
| 11 | q_mul_2 == 1 checks | 6 | 135 | 11 | 4 | 30 | 18 | 26 | 7 | 2 | 0 | 46 | gadgets/ecc/chip/mul |
| 12 | q_mul_3 == 1 checks | 4 | 107 | 11 | 4 | 24 | 14 | 21 | 6 | 2 | 0 | 36 | gadgets/ecc/chip/mul |
| 13 | Decompose scalar (complete) | 2 | 45 | 8 | 3 | 9 | 9 | 7 | 0 | 6 | 0 | 12 | gadgets/ecc/chip/mul |
| 14 | overflow checks | 5 | 49 | 6 | 5 | 7 | 12 | 6 | 0 | 6 | 0 | 13 | gadgets/ecc/chip/mul |
| 15 | LSB check | 3 | 61 | 8 | 3 | 13 | 8 | 10 | 6 | 3 | 0 | 18 | gadgets/ecc/chip/mul |
| 16 | range check | 1 | 70 | 13 | **9** | 15 | 8 | 15 | 8 | 7 | 0 | 16 | gadgets/ecc/chip/mul_fixed |
| 17 | Running sum coordinates check | 3 | 232 | 14 | **9** | 41 | 43 | 33 | 28 | 10 | 9 | 65 | gadgets/ecc/chip/mul_fixed |
| 18 | Full-width fixed-base scalar mul | 4 | 158 | 11 | **9** | 20 | 51 | 12 | 0 | 17 | 9 | 45 | gadgets/ecc/chip/mul_fixed |
| 19 | Short fixed-base mul gate | 4 | 34 | 4 | 3 | 5 | 8 | 4 | 0 | 2 | 0 | 11 | gadgets/ecc/mul_fixed/short |
| 20 | Canonicity checks | 8 | 88 | 8 | 5 | 14 | 18 | 12 | 4 | 8 | 0 | 24 | gadgets/sinsemilla |
| 21 | full round | 3 | 201 | 9 | 6 | 54 | 39 | 3 | 9 | 0 | 45 | 48 | gadgets/poseidon |
| 22 | partial rounds | 4 | 162 | 10 | 6 | 50 | 12 | 4 | 30 | 0 | 26 | 36 | gadgets/poseidon |
| 23 | pad-and-add | 3 | 22 | 3 | 2 | 5 | 3 | 3 | 0 | 0 | 0 | 8 | gadgets/sinsemilla |
| 24 | Initial y_Q | 1 | 22 | 9 | 4 | 5 | 3 | 4 | 1 | 0 | 1 | 7 | gadgets/sinsemilla |
| 25 | Sinsemilla gate | 2 | 85 | 12 | 6 | 20 | 13 | 14 | 3 | 3 | 4 | 26 | gadgets/sinsemilla |
| 26 | a' = b·swap + a·(1−swap) | 3 | 38 | 7 | 3 | 7 | 8 | 5 | 0 | 3 | 0 | 12 | gadgets/sinsemilla |
| 27 | Decomposition check | 4 | 44 | 8 | 2 | 10 | 4 | 6 | 6 | 0 | 0 | 14 | gadgets/sinsemilla |
| 28 | Initial y_Q | 1 | 22 | 9 | 4 | 5 | 3 | 4 | 1 | 0 | 1 | 7 | gadgets/sinsemilla |
| 29 | Sinsemilla gate | 2 | 85 | 12 | 6 | 20 | 13 | 14 | 3 | 3 | 4 | 26 | gadgets/sinsemilla |
| 30 | a' = b·swap + a·(1−swap) | 3 | 38 | 7 | 3 | 7 | 8 | 5 | 0 | 3 | 0 | 12 | gadgets/sinsemilla |
| 31 | Decomposition check | 4 | 44 | 8 | 2 | 10 | 4 | 6 | 6 | 0 | 0 | 14 | gadgets/sinsemilla |
| 32 | CommitIvk canonicity check | **14** | 119 | 6 | 3 | 21 | 22 | 10 | 9 | 6 | 0 | 37 | orchard/circuit/commit_ivk.rs |
| 33 | NoteCommit MessagePiece b | 3 | 31 | 7 | 3 | 6 | 5 | 3 | 3 | 2 | 0 | 9 | orchard/note_commit.rs |
| 34 | NoteCommit MessagePiece d | 3 | 31 | 7 | 3 | 6 | 5 | 3 | 3 | 2 | 0 | 9 | orchard/note_commit.rs |
| 35 | NoteCommit MessagePiece e | 1 | 9 | 5 | 2 | 2 | 1 | 1 | 1 | 0 | 0 | 3 | orchard/note_commit.rs |
| 36 | NoteCommit MessagePiece g | 2 | 20 | 6 | 3 | 4 | 3 | 2 | 2 | 1 | 0 | 6 | orchard/note_commit.rs |
| 37 | NoteCommit MessagePiece h | 2 | 17 | 5 | 3 | 3 | 3 | 2 | 1 | 1 | 0 | 5 | orchard/note_commit.rs |
| 38 | NoteCommit input g_d | 5 | 38 | 5 | 3 | 6 | 8 | 3 | 2 | 2 | 0 | 12 | orchard/note_commit.rs |
| 39 | NoteCommit input pk_d | 4 | 36 | 6 | 3 | 7 | 6 | 3 | 3 | 2 | 0 | 11 | orchard/note_commit.rs |
| 40 | NoteCommit input value | 1 | 12 | 5 | 2 | 3 | 1 | 1 | 2 | 0 | 0 | 4 | orchard/note_commit.rs |
| 41 | NoteCommit input rho | 4 | 36 | 6 | 3 | 7 | 6 | 3 | 3 | 2 | 0 | 11 | orchard/note_commit.rs |
| 42 | NoteCommit input psi | 5 | 44 | 6 | 3 | 8 | 8 | 3 | 4 | 2 | 0 | 14 | orchard/note_commit.rs |
| 43 | y coordinate checks | 7 | 58 | 6 | 3 | 10 | 11 | 5 | 4 | 3 | 0 | 18 | orchard/note_commit.rs |
| 44 | NoteCommit MessagePiece b | 3 | 31 | 7 | 3 | 6 | 5 | 3 | 3 | 2 | 0 | 9 | orchard/note_commit.rs |
| 45 | NoteCommit MessagePiece d | 3 | 31 | 7 | 3 | 6 | 5 | 3 | 3 | 2 | 0 | 9 | orchard/note_commit.rs |
| 46 | NoteCommit MessagePiece e | 1 | 9 | 5 | 2 | 2 | 1 | 1 | 1 | 0 | 0 | 3 | orchard/note_commit.rs |
| 47 | NoteCommit MessagePiece g | 2 | 20 | 6 | 3 | 4 | 3 | 2 | 2 | 1 | 0 | 6 | orchard/note_commit.rs |
| 48 | NoteCommit MessagePiece h | 2 | 17 | 5 | 3 | 3 | 3 | 2 | 1 | 1 | 0 | 5 | orchard/note_commit.rs |
| 49 | NoteCommit input g_d | 5 | 38 | 5 | 3 | 6 | 8 | 3 | 2 | 2 | 0 | 12 | orchard/note_commit.rs |
| 50 | NoteCommit input pk_d | 4 | 36 | 6 | 3 | 7 | 6 | 3 | 3 | 2 | 0 | 11 | orchard/note_commit.rs |
| 51 | NoteCommit input value | 1 | 12 | 5 | 2 | 3 | 1 | 1 | 2 | 0 | 0 | 4 | orchard/note_commit.rs |
| 52 | NoteCommit input rho | 4 | 36 | 6 | 3 | 7 | 6 | 3 | 3 | 2 | 0 | 11 | orchard/note_commit.rs |
| 53 | NoteCommit input psi | 5 | 44 | 6 | 3 | 8 | 8 | 3 | 4 | 2 | 0 | 14 | orchard/note_commit.rs |
| 54 | y coordinate checks | 7 | 58 | 6 | 3 | 10 | 11 | 5 | 4 | 3 | 0 | 18 | orchard/note_commit.rs |

三点值得先记住：

1. **最大的门是 `complete addition`（225 节点，12 条约束）和 `Running sum coordinates check`（232 节点）**，
   两者合计 14.3% 的节点，但——见 §5——节点数不是成本，整块乘才是。
2. **18 对（36 道）门是结构重复的**（7–12 的 hi/lo 三对、24/28 与 25/29 与 26/30 与 27/31 的
   两套 Sinsemilla 四对、33–43 与 44–54 的 old/new note 十一对）。它们看起来像"可以合并"，
   但 §5 会说明为什么只有一类真能合。
3. **次数 9 只由 3 道门产生**（16/17/18），8 条约束。它决定了 h(X) 有 8n 个系数（§6.3）。

---

## 4. 第 2 项：Selector 的实际使用与压缩

### 4.1 56 个逻辑 selector 分成三类

| 类别 | 个数 | 例子 | 为什么单独一列 |
|---|---:|---|---|
| complex（用在 lookup 里） | **4** | 2、3（range/table）、25、29（Sinsemilla lookup） | halo2 禁止 simple selector 出现在 lookup 参数里；它们的固定列存 0/1 掩码，**不产生任何替换代价** |
| simple 但次数预算已满 | **2** | 18（range check, deg 9）、19（full-width mul, deg 9） | `max_deg−1 + 1 = 9`，再加任何成员就越界 |
| simple 且可打包 | **50** | 其余 | 按 `d + k ≤ 9` 贪心装进 **9 列** |

合计 4 + 2 + 9 = **15 个新固定列**（14 → 29）。

### 4.2 压缩是"顶格"的，不是"偷懒"的

| 列 | k | selector | 该列最高次数 d | d + k |
|---|---:|---|---:|---:|
| 18 | 6 | 0,1,4,9,10,11 | 3 | **9** |
| 19 | 4 | 5,6,7,8 | 5 | **9** |
| 20 | 5 | 12,13,14,15,16 | 4 | **9** |
| 21 | 4 | 17,20,21,22 | 5 | **9** |
| 24 | 4 | 23,24,26,27 | 5 | **9** |
| 25 | 6 | 28,30,33,34,35,36 | 3 | **9** |
| 26 | 7 | 31,32,37,38,39,40,41 | 2 | **9** |
| 27 | 7 | 42,43,44,45,46,47,48 | 2 | **9** |
| 28 | 7 | 49,50,51,52,53,54,55 | 2 | **9** |

**九组全部 `d + k = 9`**，一列也没浪费。用同一个贪心规则做 best-fit-decreasing 重排，
52 个 simple 仍然需要 11 列（与 keygen 一致）。所以"再压几列"在 `max_degree = 9` 下
不存在解；要更少的列，只能先把最深那条约束的次数降下来——那是改电路，不是调参数。

### 4.3 行集合关系：91.8% 互斥，1 对完全相同

56 个 selector 的激活行两两比较（1540 对）：

| 关系 | 对数 | 占比 |
|---|---:|---:|
| 互不重叠（可同列） | 1413 | 91.8% |
| 部分重叠（不可同列） | 126 | 8.2% |
| **完全相同** | **1** | 0.1% |

完全相同的那一对是 **selector 9 与 selector 12**（都是 `q_mul_1 == 1 checks`，各只启用 1 行），
**两者都落在第 1 行**——这正是用户问到的第四种情况："两个 selector 在不同 region 中启用，
但最终落在相同物理行"。这里 region 不同（hi / lo 两个 ecc 实例，用不同 advice 列），
但那一行同时是两边 region 的第 0 行，所以压缩不能把它们装进同一列：
同一行上两种掩码都为 1，恢复多项式 `q·∏(rᵢ−q)` 无法区分根。

这条也解释了 126 对部分重叠的来源：多数是 `sel 2/3/7/18/19` 这类"整段启用"的 complex
selector 与细粒度 selector 在同几行上擦到——**行冲突是结构性的，不是偶然的**。

### 4.4 一列固定列到底值多少钱（实测）

同一二进制，证明跑完后单线程跑 200 次 `eval_polynomial`（n = 2048，即 k=11 的真实域）：

```
median 44.2 us   min 41.5 us   max 195.5 us   (21.58 ns/系数)
```

这就是 opening 里每个固定列查询的代价（`fixed_queries` 29 个，每列一次）：

| 量 | 值 | 占 1P 证明（2725.8 ms） |
|---|---:|---:|
| 1 个固定列 | 0.0442 ms | 0.0016% |
| 29 个固定列全部 | 1.282 ms | **0.047%** |
| 其中 15 个 selector 列 | 0.663 ms | **0.024%** |

**固定列几乎不要钱。** 这句话有前提，必须写清楚：固定列的*承诺*在 keygen 里做（进 VK，不进证明），
证明里只多一次 `eval_polynomial` 和 MSM 里的一个点。所以"selector 多 → 固定列多 → 证明变慢"
这条直觉在本电路上是错的，量级差三个数量级。

---

## 5. 第 3 项：合并候选——成本不在门里，在替身里

### 5.1 压缩的账单：869 个整块乘

§2.2 的对账说明 post 树比作者树多 869 个 product。把它按门摊开（`+muls = 该门约束数 × (k−1)`）：

| # | gate | 约束 | selector | k | +整块乘 | 折算 ms（1P，外推） |
|---:|---|---:|---:|---:|---:|---:|
| 32 | CommitIvk canonicity check | 14 | 33 | 6 | **70** | 13.0 |
| 43 | y coordinate checks | 7 | 44 | 7 | 42 | 7.8 |
| 54 | y coordinate checks | 7 | 55 | 7 | 42 | 7.8 |
| 6 | complete addition | 12 | 8 | 4 | 36 | 6.7 |
| 8 | q_mul_2 == 1 checks | 6 | 10 | 6 | 30 | 5.6 |
| 38 | NoteCommit input g_d | 5 | 39 | 7 | 30 | 5.6 |
| 42 | NoteCommit input psi | 5 | 43 | 7 | 30 | 5.6 |
| 49 | NoteCommit input g_d | 5 | 50 | 7 | 30 | 5.6 |
| 53 | NoteCommit input psi | 5 | 54 | 7 | 30 | 5.6 |
| … | （其余 45 门） | | | | 529 | 98.4 |
| | **合计** | 193 | | | **869** | **161.7** |

折算是外推：用 H6 实测的 `490.8 ms / 2638 个整块乘 = 0.1861 ms/个`（1P，quotient 1767.0 → 1276.2）。
Product 与 H6 消掉的 Scale 未必同价，所以这个数只能当量级用，真正的数要等预注册实验。

**这张表把"门太多"这个直觉彻底换掉了**：排在第一位的是 `CommitIvk canonicity check`，
它有 14 条约束却只有 119 个节点——它贵，是因为 14 条约束**各自**把同一个 selector 展开成
5 次乘加。门里的表达式很小，替身很贵。

### 5.2 候选 A：门内共享 selector 恢复链（证明者侧，不进证明）

同一个门的 s 条约束用的是同一个 `q`，恢复链 `q·∏(rᵢ−q)` 因此被算 s 次。
若在构建商表达式时共享这条子链，代价从 `s(k−1)` 降到 `(k−1)`：

| | 现在 | 共享后 |
|---|---:|---:|
| 869 个 product | | **242** |
| 折算（外推） | 161.7 ms | 45.0 ms |
| 节省 | | **627 个整块乘 = 116.7 ms = 1P 证明的 4.28%** |
| 证明大小 | 7264 B | 7264 B（不变） |

依据：F2 的 `cse_saving_vector_muls = 1002` 早就量过这个重复的总量，只是没拆开；
本轮拆开后 627/1002 = 62.6% 来自 selector 恢复链，其余是非 selector 的结构重复。
**风险（这是这个候选最需要先算清的一件事）**：共享要求这条子链**只读**。H7 的规则是
"有一侧 `Owned` 就就地累加"，若 s 条约束都把它当 `Owned` 用，第一条就把它改掉了；所以
共享的实现必然是"链算一次、保持 `Borrowed`，s 个消费者各自 `collect` 一个输出"——
正好是 H7 里分配次数上升的那条路径。省下的 `(s−1)(k−1)` 个乘，会不会被 s 次 collect 的
分配与写回吃掉，必须先量（H7 的 D4 计数器可以直接回答）。另外它把商树从"树"变成跨 term 的
"DAG"，与 H7 的所有权纪律不是天然兼容的，**必须单独预注册、单独验证逐元素相等**。

### 5.3 候选 B：old/new NoteCommit 共享 selector（电路侧）

old note 与 new note 的 11 道 NoteCommit 门 **用的是同一批 advice 列**（6,7,8,9），
只是行区间不同、selector 不同：

| 门对 | advice 列 | selector | 启用行 |
|---|---|---|---|
| 33 / 44 (MessagePiece b) | 6,7,8 | 34 / 45 | 256 / 258 |
| 34 / 45 (MessagePiece d) | 6,7,8 | 35 / 46 | 260 / 262 |
| … | | | |
| 43 / 54 (y coordinate checks) | 5–9 | 44 / 55 | 1645 / 1649 |

约束形式、涉及的列、相对旋转全都相同，**只有 selector 不同**。若两个实例共用一个 selector
（在两个行区间上都启用），就少了 11 个 selector：

| | 现在 | 共享后（同一个贪心重排） |
|---|---:|---:|
| simple selector | 52 | 41 |
| simple 列 | 11 | **10** |
| +整块乘 | 869 | **734** |
| 节省 | | 135 = 25.1 ms = **0.92%** |
| 证明大小 | 7264 B | 7264 B（不变） |

**风险**：要改 `NoteCommit::configure` 的签名，把 selector 传进去（现在是内部 `meta.selector()`）；
两个实例的行区间必须永不重叠（普查显示它们相差 2 行以上，成立，但这是"当前布局"的性质，
不是 API 保证）；共享 selector 后若某一实例改成多行布局，会踩到另一实例。属于**中等改动、中等收益**。

### 5.4 候选 C：干脆不压缩（策略级）

`compress_selectors` 是可以不做的：56 个 selector 各占一列（52 个 simple 每个是 0/1 掩码），
替换代价归零。

| | 现在（15 列） | 不压缩（+41 列） |
|---|---:|---:|
| 固定列 | 29 | **70** |
| 商树 product | 1424 | **555** |
| 整块乘（含 selector 恢复） | +869 | 0 |
| 折算 | — | 省 161.7 ms（**5.93%**） |
| opening 代价 | — | +41 × 0.0442 = 1.8 ms（0.067%） |
| **证明大小** | 7264 B | **≈ 8576 B（+1312 B，+18%）** |

**这是本电路上唯一一个"用证明大小换证明时间"的干净交易，时间上是 90:1 的交换比**
（省 161.7 ms，付 1.8 ms），代价全在证明大小上（+1312 B）。Zcash 选压缩是对的——证明进链，字节比 CPU 贵得多——
所以这条**不建议在本项目里实施**，但它把"压缩不是免费的"这件事量化了：
每个被省下的固定列，代价约 0.2 ms 的商多项式计算，而它只值 0.044 ms。

### 5.5 明确否掉的：合并结构重复的门

| 候选 | 结构依据 | 为什么否 |
|---|---|---|
| 7/10、8/11、9/12（hi/lo 的 q_mul 检查） | 约束形式相同 | **列不同**（advice 0,3,4,5 vs 0,2,7,8），合并没有意义：约束作用在不同信元上 |
| 24/28、25/29、26/30、27/31（Sinsemilla old/new） | 形式相同 | **列不同**（0–4 vs 5–9）；且 25/29 是 complex selector，本来就不产生替换代价 |
| 33–43 / 44–54（NoteCommit old/new） | 形式相同、**列相同** | 唯一真可合并的一类，即候选 B；收益 0.92%，不是"合并门"，是"共享 selector" |
| 任意"把两道门写成一道" | — | 门数减少本身不减少任何表达式：prover 算的是**约束数 × 替身长度**，不是门数 |

第四条是这轮最该记住的一句：**`gates` 从 55 降到 40，若 193 条约束还在，商树一个节点都不会少。**

---

## 6. 第 4 项：与 E2 / E3 / H6 / H7 重新对照

### 6.1 当前分阶段（1P，H6 + H7 之后的树，D2 数据）

| 阶段 | ms | 份额 | 与本节的关系 |
|---|---:|---:|---|
| `quotient` | 1269.4 | 46.6% | 商树求值在这里；1424 个 product 全在这一阶段 |
| `opening` | 471.0 | 17.3% | 29 个固定列查询 + 25 advice + perm + lookup + 8 个 h |
| `advice_commit` | 363.3 | 13.3% | 与门结构无关 |
| `lookup_permute` | 224.7 | 8.2% | 3 个 lookup，受 4 个 complex selector 影响 |
| `lookup_product` | 144.2 | 5.3% | |
| `permutation` | 111.6 | 4.1% | 15 列 |
| `synthesis` | 131.3 | 4.8% | 与门表达式无关（布局） |
| `instance` + `eval_setup` | 21.0 | 0.8% | |

份额的分母是 D1 的 2725.8 ms。**D2 这九个阶段自己加起来是 2736.5 ms，比 D1 高 0.39%**——
那是 `ORCHARD_E2_TIMING=1` 的计时器开销，不是证明变慢了。两套数据分开用：份额看 D2，
结论看 D1。

### 6.2 乘法在哪（同一棵树，三种数法）

| 口径 | 数量 | 说明 |
|---|---:|---|
| 商树 `vector_muls`（H6 后，1P） | 3932 | 每次 `evaluate` 的整块乘节点数（H6 前 6570） |
| 其中 gate 的 product | 1424 | 36.2% of 3932（H6 前是 555 + 869） |
| 其中 **selector 恢复** | **869** | **22.1% of 3932**，61.0% of 1424 |
| F2 的 CSE 上限 | 1002 | 与 869 同源，本节把它拆开了 |

F2 的 `segments` 这一次也给了独立交叉验证：`gates 386 = 193 × 2`（两个 action）、
`permutation 14`、`lookup 30`。**约束数乘以实例数就是项数**，没有隐藏的项。

### 6.3 次数 9 是一道悬崖，不是斜坡

`Domain::new(j, k)` 里 `quotient_poly_degree = j − 1`，`extended_k` 取满足
`2^extended_k ≥ n(j−1)` 的最小值。本电路 j = 9、n = 2048：

| 最高次数 j | h(X) 系数 | 扩展域 | 求值器每个节点处理的元素 |
|---:|---:|---:|---:|
| **9（现状）** | 8n = 16384 | 16384 | **16384** |
| 6 | 5n = 10240 | 16384（仍要 2^14） | 16384 |
| 5 | 4n = 8192 | **8192** | **8192** |

也就是说：**把 `mul_fixed` 的三道门从 9 次降到 6 次，求值器一个元素都不会少**
（扩展域仍是 16384，只是 h 从 8 片降到 5 片，少 3 次承诺与 3 个开点，落在 `opening` 的 471 ms 里）；
只有降到 **5 次**才会把求值域砍半。这是一个"全有或全无"的结构，不是"降一点赚一点"。
结论：**不要为了降次数去拆门**，除非目标是降到 ≤ 5，而那要把 `complete addition`(6)、
`poseidon`(6)、`Sinsemilla`(6) 一起改掉——代价远大于收益，不建议。

### 6.4 固定列相关的承诺与多项式处理

| 项 | 何时发生 | 代价 |
|---|---|---|
| 14 个 configure 列的承诺 | keygen | 进 VK，不在证明里 |
| 15 个 selector 列的承诺 | keygen（压缩后） | 同上 |
| 29 次 `eval_polynomial` | 每次证明的 `opening` | **1.282 ms（0.047%）**，实测 |
| 29 个点在 prover MSM 里 | 每次证明的 `opening` | 计入上面，单列 0.044 ms 含两者 |
| 证明里的 29 个固定列取值 | — | 29 × 32 B = 928 B（7264 B 的 12.8%） |

**"减少 selector 数"能省的是 keygen 时间和 VK 大小，不是证明时间。** 唯一进入证明的是
每列一次取值（32 B）和一次求值（44 µs）。

---

## 7. 第 5 项：候选优先级

### 值得进一步研究（有结构依据 + 可测的收益路径）

| 编号 | 候选 | 依据 | 上界（外推） | 前置条件 |
|---|---|---|---:|---|
| **F4-A** | 门内共享 selector 恢复链 | 869 个 product 里 627 个是同一门内的重复 | 4.28% | 需要改商树构建；必须先证明与 H7 的 Borrowed/Owned 兼容 |
| **F4-B** | old/new NoteCommit 共享 selector | 11 对门的列与形式完全相同，只有 selector 不同 | 0.92% | 需要改 `NoteCommit::configure` 签名；要证明行区间永不重叠 |

两个候选**都还没有预注册，也都没有写一行代码**。按项目惯例，下一步应当先给 F4-A 写预注册
（它的上界最大，且不需要碰电路），或者先做一个零改动的可行性探针：在 H7 的树上能否安全地
让一条子链被多条 term 借用。

### 结构上已经优化过（不要重复计入）

| 项 | 现状 | 为什么不要再动 |
|---|---|---|
| selector 压缩 | 56 → 15 列，9 组全部顶满 `d+k=9` | best-fit 重排也只能到 11 列（simple），没有空间 |
| 固定列复用 | Poseidon 轮常量与 ECC lagrange 共用 fixed 5–10（02 号文档已锁） | 14 列已是跨 chip 复用的结果 |
| 4 个 complex selector 各占一列 | 存 0/1 掩码，替换代价 0 | 无成本 |
| 常数项 `constants` 只有 1 列 | `lagrange_coeffs[0]` 兼作全局常量 | — |

### 不建议优化（改动大、不减少实际计算）

| 项 | 理由 |
|---|---|
| 合并结构重复的门（hi/lo、old/new 的 ecc、Sinsemilla） | 约束作用在不同信元上，合并省不了任何表达式 |
| 为了降次数拆 `mul_fixed` 的门 | 9 → 6 次不减少求值域；只有 ≤5 才有用，而 ≤5 要动 poseidon/complete addition，不现实 |
| 减少 selector 以"省固定列" | 一列值 0.044 ms；29 列合计 0.047% |
| 真的不压缩（候选 C） | 省 5.93%，但证明 +18%；Zcash 的取舍是正确的，这条只在"证明不进链"的场景下成立 |

---

## 8. 本轮的边界

- **没有改任何电路、`ConstraintSystem`、`Gate`、`Selector` 或布局代码。** 补丁只在 keygen 里挂了两个 dump。
- **没有预注册任何优化**，也没有实现 F4-A / F4-B；两个候选的收益都是"普查 + 一个假想重排"，
  不是测量值，凡是外推都用 H6 的 0.1861 ms/整块乘，已在表里标注。
- 已知的不确定：
  1. `Product` 与 H6 消掉的 `Scale` 未必同价（前者读两个向量，后者读一个向量加一个标量），
     实际收益可能偏离 4.28%；
  2. F4-B 的 0.92% 是用**同一个贪心**重排算出来的，真正实施后 selector 索引会变，分组也会变；
  3. 本轮跑的是**不含 H6/H7 的树**（H6/H7 是求值器补丁，不影响约束系统）。
     证明指纹 `0xa6bcd171d69e969a`、7264 B 与 H6/H7 记录逐位相同，这是"约束系统一致"的直接证据，
     但耗时数字（734 / 773 ms，未绑核、默认 rayon 线程池）与本篇的任何结论无关，不引用。

---

## 9. 复现

```bash
# 1. 打探针、编译、跑一次普查（约 40 s 编译 + 3 s 运行）
cd experiments/transaction_e2e/harness
git checkout -- ../vendor/halo2_proofs-0.4.0
git apply ../vendor/halo2_proofs-0.4.0-f4-recon.patch
cargo build --release --offline --features f4 --bin orchard-e2e
./target/release/orchard-e2e --mode recon --reps 200   > ../../../results/transaction_e2e/f4_recon/recon_on.log
ORCHARD_F4=0 ./target/release/orchard-e2e --mode recon --reps 0 > ../../../results/transaction_e2e/f4_recon/recon_off.log

# 2. 出表（门表 / selector 表 / 对账 / 外推）
python scripts/analysis/f4_recon_report.py \
  results/transaction_e2e/f4_recon/recon_on.log \
  --out-dir results/transaction_e2e/f4_recon \
  --md results/transaction_e2e/f4_recon/summary.md

# 3. 还原（务必）
git checkout -- experiments/transaction_e2e/vendor/halo2_proofs-0.4.0/src/lib.rs \
                experiments/transaction_e2e/vendor/halo2_proofs-0.4.0/src/plonk/keygen.rs
rm -f experiments/transaction_e2e/vendor/halo2_proofs-0.4.0/src/f4.rs
python scripts/analysis/f0_verify_frozen.py    # 期望 RESULT: PASS（58 个文件）
```

产物：`results/transaction_e2e/f4_recon/{recon_on,recon_off}.log`、`summary.md`、
`gates.csv`、`selectors.csv`、`build.txt`（工具链、二进制与 `.text` 指纹、补丁行数、回放 md5）。
