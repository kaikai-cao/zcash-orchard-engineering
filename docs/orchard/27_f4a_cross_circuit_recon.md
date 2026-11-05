# 27 · F4-A 跨电路适用性普查（只读）

> 状态：第一轮，**只读**。不改冻结补丁、不把 F4-A 移植到任何其它项目。
> 目的不是再拿一个性能数字，而是回答一个判定性问题：
> F4-A 发现的共享恢复链，是 Orchard 电路的特殊结构，还是 Halo2 的
> `compress_selectors` 普遍会产生的重复计算？

---

## 0. 一句话结论

**机制是库侧的、普遍的；收益不是。**

恢复链由 `halo2_proofs` 的 `compress_selectors` 生成，不是 Orchard 手写的，
所以任何走同一 `keygen` 路径的电路都会经过它。但链**出现**要满足一个可静态
判定的结构条件——`ConstraintSystem::degree()` 的预算必须**大于**参与组合的门
自身的次数——而链**有收益**还要叠加第二个条件：同一个 selector 被同一门的
多条约束引用（`occ ≥ 2`）。

实测对照（同一个 Poseidon 电路，只改 degree 预算）：

| 变体 | CS degree | 链 | 消费者 | 可省乘法 |
|---|---:|---:|---:|---:|
| `base` | 5 | **0** | 0 | **0** |
| `hi`（加一个 9 次门，不改任何 Poseidon 门） | 9 | **3** | 10 | **14** |

---

## 1. 先更正一个前提：链的来源不是 Orchard

前几份文档（21/22/24）一直把这条链描述成"Orchard 的 selector 恢复"。
普查第一步就把这个归属改正了：

`halo2_proofs/src/plonk/keygen.rs` 在 **每一条** keygen 路径上无条件调用

```rust
let (cs, selector_polys) = cs.compress_selectors(assembly.selectors);
```

而 `compress_selectors::process` 在为一个组合分配新固定列时，为组合里**每个**
selector 生成替换表达式：

```rust
// halo2_proofs/src/plonk/circuit/compress_selectors.rs
let mut expression = query.clone();
let mut root = F::ONE;
for _ in 0..combination_len {
    if root != assigned_root {
        expression = expression * (Expression::Constant(root) - query.clone());
    }
    root += F::ONE;
}
```

即字面上的

```
q · ∏_{i ≠ assigned_root} (i − q)
```

这是库的代码，不是电路的代码。所以：

- **不属于** Orchard 特有实现；
- **不需要**电路作者知道这件事；
- 但**也不是**每个电路都会产生长链——组合长度由 degree 预算决定（§4）。

这一条改变了候选的筛选门槛：不是"找也用 selector compression 的电路"，
而是"找 selector 的门次数明显低于 CS degree 的电路"。

---

## 2. 五条筛选条件，改成可判定形式

| # | 原始条件 | 可判定形式 | 判定手段 |
|---|---|---|---|
| 1 | 使用相同/兼容的压缩实现 | `halo2_proofs` 0.4 + `compress_selectors`（本仓库 vendor 的那一份） | 依赖树 |
| 2 | 存在 `q·∏(rᵢ−q)` 的恢复表达式 | `combination_len ≥ 2` | `census` 的 `k` |
| 3 | 多条约束重复使用同一恢复链 | 同一链 `occ ≥ 2` | `census` 的 `occ` |
| 4 | 存在可安全共享的只读消费者 | 消费者位于约束根（`root_factorable`） | `census` 的 `root_factorable` |
| 5 | 能独立运行完整证明与验证 | `keygen + create_proof + verify_proof` 通过 | 本轮的 `XCENSUS_RUN` |

条件 2 是**门槛**：`k = 1` 时链退化为"换一列"，`saved = (occ−1)(k−1) = 0`，
后面三条都无从谈起。

---

## 3. 候选清单与未覆盖范围

| 候选 | 是否走同一压缩 | 能否独立 prove/verify | 定稿归类 | 说明 |
|---|---|---|---|---|
| `orchard` 0.16（PostNu6_3） | 是 | 是 | 基准 | 已有 21/24/26 的完整数据 |
| `halo2_gadgets` 0.6 · Poseidon (`Pow5Chip`) | 是 | 是 | **第二电路（唯一入选）** | 3 门 / 3 selector，无需固定基点 |
| `halo2_gadgets` 0.6 · ECC chip | 是 | 理论可以 | **未覆盖范围（非候选）** | 见 §3.1；未测、未证伪，不产生结论 |
| `halo2_gadgets` 0.6 · Sinsemilla chip | 是 | 理论可以 | **未覆盖范围（非候选）** | 同上，依赖 `FixedPoints` |
| `halo2_poseidon` 0.2 | — | 否 | 排除 | 只有 hash 函数，不含电路 |
| `halo2_proofs` 自带 examples | 是 | 是 | 排除 | 1–3 个门、1–2 个 selector，规模不足以产生组合 |
| 其它生产电路（zkEVM 等） | 未知 | — | **不可及** | 本机没有第二份 halo2 工程，见 §9 |

> **归类口径（与 doc 26 §20 一致）**：ECC 与 Sinsemilla **不是"待验证的候选"**。
> 它们没有被测过，也没有被证伪；在本研究里不产生任何结论，也不计入样本量（样本量 = 2，
> 其中一个还是合成对照）。本表保留这两行，是为了记录"尝试过、被 API 可见性挡住"这一事实，
> 不是为它们保留候选资格。若将来要覆盖，需要先补 `FixedPoints` 窗口表或改上游可见性，
> 那是一个新立项，不是本轮的遗留项。

### 3.1 为什么 ECC chip 没做成（如实记录）

`EccChip<FixedPoints>` 需要一组实现了 `halo2_gadgets::ecc::FixedPoint` 的固定基点。
Orchard 的 `OrchardFixedBases` 正好是这样一个实现，但它在 `orchard::constants`，
而该模块在非 `unstable-voting-circuits` 下是**私有**的：

```rust
// orchard/src/lib.rs
#[cfg(not(feature = "unstable-voting-circuits"))]
mod constants;
```

打开那个 feature 会往电路里加投票门，等于换了电路，不能用。
自己实现 `FixedPoints` 需要完整的窗口表（`generator()`、`u()`、`z()`、
`lagrange_coeffs()`），是几百行的工程量，且与"只读普查"的定位不符。

ECC 的 range check 表 (`GeneratorTableConfig`) 同样是 `pub(crate)`，
外部无法 `load`。这是这条路的第二个硬阻塞。

**结论**：这一轮没有拿到 ECC/Sinsemilla 的结构数据，不假装拿到了。
按定稿口径（doc 26 §20），它们进入**未覆盖范围**，不再作为候选出现在任何结论里。

---

## 4. 第二电路：Poseidon-only

`experiments/transaction_e2e/harness/src/bin/xcensus.rs`：只 configure
`halo2_gadgets::poseidon::Pow5Chip`，对 `[1, 2]` 做一次 `ConstantLength<2>` 的
sponge 哈希。**不含任何 Orchard 门、列、selector。**

选它的理由不是"小"，而是它的门形状与 Orchard 的对应门同构：

| | Orchard `Orchard circuit checks` | Poseidon `full round` |
|---|---|---|
| 一个 selector 下的约束数 | 4 | 3（`WIDTH`） |
| 门的代数次数 | 2–4 | 5 |
| CS degree | 9 | 5（`base`）/ 9（`hi`） |

如果 F4-A 的机制在 Orchard 里成立是因为"多约束共享一个 selector"，
那么 Poseidon 的三个门**每一个**都具备这个形状，应该能复现。

`hi` 变体只加一个门：

```rust
meta.create_gate("degree filler", |meta| {
    let q = meta.query_selector(q_hi);
    let x = meta.query_advice(state[0], Rotation::cur());
    let x8 = /* x^8 */;
    vec![q * (x8 * x.clone() - x)]   // degree 9
});
```

这个门不参与哈希、只有 1 条约束、`occ = 1`，因此**不会**给链贡献消费者。
它的唯一作用是让 `ConstraintSystem::degree()` 从 5 变成 9。

> **诚实标注**：`hi` 是对照变体，不是真实电路。它的用途是把"degree 预算"
> 这一个变量单独隔离出来，而不是声称"某个生产电路长这样"。

---

## 5. 实测

命令与产物：

```bash
cd experiments/transaction_e2e
git apply vendor/halo2_proofs-0.4.0-f4a-probe.patch     # 只读探针
cd harness
cargo build --release --offline --bin xcensus
ORCHARD_F4A=1 XCENSUS_K=6 XCENSUS_VARIANT=base ./target/release/xcensus
ORCHARD_F4A=1 XCENSUS_K=6 XCENSUS_VARIANT=hi   ./target/release/xcensus
```

日志在 `results/transaction_e2e/f4a4_xcircuit/`。

### 5.1 结构指标

| 指标 | Orchard V3（单 `ConstraintSystem`） | Poseidon `base` | Poseidon `hi` |
|---|---:|---:|---:|
| CS degree | 9 | **5** | **9** |
| 门数 | 55 | 3 | 4 |
| 约束数 | 193 | 10 | 11 |
| 逻辑 selector（simple） | 52 | 3 | 4 |
| **恢复链** | **50** | **0** | **3** |
| **重复链（`occ ≥ 2`）** | **39** | 0 | **3** |
| **消费者** | **181** | 0 | **10** |
| 根位置消费者比例 | 98.9% | — | **100%**（10/10） |
| **结构可省乘法 `saved`** | **621** | 0 | **14** |
| `occ_muls`（基线要算的量） | 869 | 0 | 20 |
| 每条链的 `k` | 2–7 | — | 3 |

Orchard 一列来自 `21_constraint_system_recon.md` §5.1 与 `22_f4a_feasibility_prereg.md`
的同一份探针，口径是**单个 `ConstraintSystem`**（证明者每个 action 展开一遍，
真实是 360 消费者 / 1487 可省，见 doc 24 §4）。

关于 `saved` 的两个数：探针原始输出是 `saved_muls = 627`，doc 22 §5 用真实节点形状
（`Product` 之外的 `Sum` 也要算）重算后修正为 **621**。本表取 621，与 doc 24/26 一致；
627 只作为探针原始读数保留。这是一次口径修正，不是数据冲突。

### 5.2 三个数独立对上

`hi` 变体的 14 不是数出来的，是三个来源互相对上的：

| 来源 | 值 |
|---|---|
| `saved_muls`（探针按 `Σ(occ−1)(k−1)` 累加） | **14** |
| 手算：`(3−1)(3−1) + (4−1)(3−1) + (3−1)(3−1) = 4 + 6 + 4` | **14** |
| `occ_muls − consumers`：`20 − 10`（`k−1 = 2` 时恒等） | 10 ≠ 14 |

第三行的偏差是恒等式的适用条件：`Σ(occ−1)(k−1) = (k−1)(Σocc − 链数)`，
本例是 `2 × (10 − 3) = 14`，不是 `20 − 10`。**用链数而不是消费者数**才对。
这条正好是审计 doc 24 时踩过的同类陷阱（口径的单位必须是链，不是消费者）。

### 5.3 为什么 `base` 是 0

`Pow5Chip` 的三个 selector 所在门的次数都等于 CS degree（5），
`compress_selectors` 的组合循环第一步就命中短路：

```rust
let mut d = selector.max_degree - 1;      // = 4
...
if d + combination.len() == max_degree {  // 4 + 1 == 5 → 立刻 break
    break 'try_selectors;
}
```

于是每个 selector 单独成一个长度 1 的"组合"，替换表达式就是新列本身，
`k = 1`，探针按 `k ≥ 2` 的规则不计为链。

**这不是探针漏检**：`hi` 变体在同一份代码、同一个二进制、同一个 seed 下
立刻报出 3 条链，说明识别器工作正常，差别只在 degree 预算。

---

## 6. 机制出现的解析条件

把 §5 的观察写成可判定的式子。设 CS 的 `degree()` 为 `D`，某 selector 所在门的
最高次数为 `d_s`，它所在的组合长度为 `m`。`process` 的约束是

```
(d_s − 1) + m ≤ D        （组合形成时）
```

因此 `m ≥ 2`（即链存在）要求

```
d_s ≤ D − 1
```

也就是说：**只要一个门的次数顶满了 `D`，它的 selector 就永远拿不到长度 ≥ 2 的组合，
也就永远不会产生恢复链。**

收益侧是 doc 22 已经写下的式子：

```
saved = Σ_chains (occ − 1) · (k − 1)
```

两个条件相乘，才是"这个电路能不能从 F4-A 受益"的完整判据：

| 条件 | 含义 | 不满足时的后果 |
|---|---|---|
| **C1** `d_s ≤ D − 1` | 门次数没有顶满 degree 预算 | `k = 1`，链不存在 |
| **C2** `occ ≥ 2` | 同一 selector 被同门多条约束引用 | `saved = 0`，链存在但不省 |
| **C3** 消费者在约束根 | 值可只读借用 | 需要复制，收益被吃回 |

Orchard 三条全中；Poseidon `base` 缺 C1；Poseidon `hi` 三条全中但规模小
（`saved = 14` 对 `621`，差 44 倍）。

**这不是"两个电路碰巧不一样"，而是一条可以先算后测的判据。**
给定一个新电路，只要有它的 `ConstraintSystem`，C1 和 C2 在跑证明之前就能算出来。

---

## 7. 判据

| # | 判据 | 实测 | 结论 |
|---|---|---|---|
| **X1** | 第二电路能独立 prove/verify | 两个变体都通过，proof 2400 B / 2368 B | **PASS** |
| **X2** | 第二电路存在 `k ≥ 2` 的恢复链 | `base` 0 条、`hi` 3 条 | **条件性 PASS**（依赖 degree） |
| **X3** | 第二电路存在 `occ ≥ 2` 的重复链 | `hi` 3/3 条，`occ = 3,4,3` | **PASS**（`hi`） |
| **X4** | 消费者可只读共享（根位置） | `hi` 10/10 = 100% | **PASS**（`hi`） |
| **X5** | 第二电路的 `saved` 达到 Orchard 的 10% | 14 / 621 = 2.3% | **FAIL** |
| **X6** | 单因子对照能翻转机制存在性 | 只改 degree，链 0 → 3 | **PASS** |

按预注册式的读法：**X5 不过 ⇒ 不在第二电路上做移植**，与用户"先普查、不移植"
的决定一致。X2/X3/X4 的价值在于它们证明**机制不是 Orchard 专属**；
X5 的价值在于它证明**收益是规模专属的**。

---

## 8. 结论与措辞上限

### 8.1 可以说

1. **机制来源是库，不是 Orchard。** `q·∏(i−q)` 由
   `halo2_proofs::plonk::circuit::compress_selectors` 生成，Orchard 只是第一个
   被测量的使用者。
2. **机制的出现由 degree 预算决定，可先验判定。** 同一批门、同一批 selector、
   同一个哈希电路，只把 CS degree 从 5 提到 9，恢复链从 0 条变成 3 条。
3. **机制的收益由 `occ` 与 `k` 决定，也可以先验估算。** `saved = Σ(occ−1)(k−1)`，
   Poseidon `hi` 的 14 与手算逐位相等。
4. **Orchard 是"高收益"的一端，不是"唯一"的一端。** 它的 `saved = 621`
   （单实例）/ 1487（每 action 展开），来自 39 条重复链与 4 条约束/门的形状；
   Poseidon 同构但规模只有 14。

### 8.2 不能说

- **不能说 F4-A 是 Halo2 的通用优化。** 本轮只有一个真实的第二电路
  （Poseidon，且它只有在人为抬 degree 后才出现链）；ECC / Sinsemilla 属于**未覆盖范围**
  （doc 26 §20），既不支持也不反对任何结论。样本量是 2，其中 1 个是合成对照。
- **不能把 `hi` 的 14 当成"预期收益"。** 它是结构计数，不是端到端时间；
  而且 `hi` 变体本身不是生产电路。
- **不能由本轮的 2.3% 推断"所有电路收益都小"。** 只能推断
  "收益与 `Σ(occ−1)(k−1)` 同阶，而 Orchard 的这个量比 Poseidon 大两个数量级"。

### 8.3 给论文的表述

> F4-A 利用的是 Halo2 `compress_selectors` 在 keygen 阶段生成的 selector 恢复
> 表达式在约束树中的重复出现。该重复不是 Orchard 电路特有的：它由压缩算法在
> `d_s ≤ D − 1` 时会自动产生，并在同一 selector 被同门多条约束引用时产生
> `occ ≥ 2` 的重复。Orchard V3 的结构使这两个条件同时以较大规模成立
> （39 条重复链、181 个消费者、621 个可省整块乘），因此在该电路上取得
> 1P −14.35%…−28.43% 的端到端收益。在一个 Poseidon 电路上的对照实验表明：
> 当 CS degree 等于门次数时该机制完全不存在（0 条链），把 degree 抬高后
> 机制出现但规模仅 14 个可省乘法。**该优化的跨电路通用性尚未得到验证；
> 已验证的是其适用条件可以被先验判定。**

---

## 9. 未做与不可及

| 项 | 状态 | 原因 |
|---|---|---|
| ECC / Sinsemilla chip 的结构普查 | **未覆盖（非候选）** | 见 doc 26 §20：`OrchardFixedBases` 与 `GeneratorTableConfig` 均不可从外部访问；自建固定基点表超出只读普查范围 |
| 其它生产 Halo2 电路（zkEVM、halo2-lib 等） | **不可及** | 本机只有 `orchard-upstream` 一份 halo2 工程；`cargo registry` 里只有 `halo2_gadgets` / `halo2_poseidon`，后者不含电路 |
| 第二电路上的端到端 A/B | **未做** | X5 已 FAIL，按决定不移植 |
| 第二电路的 `h` 指纹对照 | **未做** | 无 F4-A 实现臂，无对照可做 |
| `base` 与 `hi` 的绝对时间 | **未测** | 本轮只取结构计数；`hi` 多一个门，时间差会被门本身混淆 |

---

## 10. 复现

```bash
cd experiments/transaction_e2e
git apply vendor/halo2_proofs-0.4.0-f4a-probe.patch

cd harness
CARGO_TARGET_DIR=target/xc cargo build --release --offline --bin xcensus

for v in base hi; do
  ORCHARD_F4A=1 XCENSUS_K=6 XCENSUS_VARIANT=$v RAYON_NUM_THREADS=1 \
    ./target/xc/release/xcensus > ../../../results/transaction_e2e/f4a4_xcircuit/poseidon_$v.log 2>&1
done

# 还原
cd ../vendor/halo2_proofs-0.4.0
git checkout -- src/f2.rs src/lib.rs src/plonk/prover.rs src/plonk/vanishing/prover.rs src/poly/evaluator.rs
rm -f src/f4a.rs
python ../../../scripts/analysis/f0_verify_frozen.py     # 期望 RESULT: PASS
```
