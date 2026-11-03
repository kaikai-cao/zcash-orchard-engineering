# `Circuit::synthesize_base()`（上）：调用骨架、九个模块、Spend Witness 与 Merkle Path

> 上游：`zcash/orchard` @ `616a669d`（`0.16.0`），`src/circuit.rs` L505–L896
> 依赖：`halo2_proofs 0.4.0`、`halo2_gadgets 0.6.0`（`src/ecc/chip/witness_point.rs`、`src/sinsemilla/merkle.rs`）
> 数值证据：`results/halo2/synthesize/merkle_pos_check.txt`
> 复现：`cargo run --release --bin merkle_pos_check`
> 上一篇：`02_configure.md`（结构）；本篇（行为）；下篇待写：`04_synthesize_base_b.md`（cv_net / nf / rk / 两个 NoteCommit）

---

## 0. 一句话

`synthesize_base()` 把 `Circuit` 的 19 个私有字段**分配进 `configure()` 声明的列**，让各 gadget 就地算出 `cm_old`、`cv_net`、`nf_old`、`rk`、`cmx`，再把其中 **6 个结果**用 `constrain_instance()` 钉到 `Instance` 的 10 行公开输入上，最后用**一行 `q_orchard` 区域**把 `root`/`anchor`/金额/开关四件事收口，返回四个地址点供版本化扩展使用。

---

## 1. 调用骨架与职责边界

```rust
fn synthesize(&self, config, mut layouter) -> Result<(), plonk::Error> {
    let addrs = self.synthesize_base(&config, &mut layouter)?;   // L1069

    if self.circuit_version.supports_cross_address_restriction() {
        Self::synthesize_cross_address_checks(&config, &mut layouter, &addrs)?;
    }
    Ok(())
}
```

| 函数 | 行区间 | 职责 | 版本相关？ |
|---|---|---|---|
| `Circuit::synthesize` | **L1064–L1076** | 顺序调用 + 版本分支 | 是（分支点） |
| `synthesize_base` | **L521–L896** | 全部共同约束，返回 `AddressPoints` | 仅通过 `halo2_version()` 影响 ECC 基点锚定 |
| `synthesize_cross_address_checks` | L939–L1055 | 四行复用 `q_orchard` 的坐标相等检查 | 只有 `PostNu6_3` 调 |

`AddressPoints`（L505–L513）不是普通返回值，它是**版本化扩展的挂钩**：

```rust
struct AddressPoints {
    g_d_old, pk_d_old, g_d_new, pk_d_new: NonIdentityPoint<...>,
}
```

只有把 `(g_d, pk_d)` 的**已分配 cell**交出去，NU6.3 的追加约束才能 `copy_advice` 到新行——这也是为什么这些点必须在 `synthesize_base` 里被**保存下来**而不是算完即弃。`FixedPostNu6_2` 下这四行不生成，但四个 cell 照样已经被分配、已进 permutation。

---

## 2. 九个模块与精确行区间

按源码实际执行顺序（**注意与"逻辑顺序"不同**，两处偏差见下）：

| # | 模块 | 行区间 | 关键产出 |
|---|---|---|---|
| 1 | 初始化 | L527–L530 | `SinsemillaChip::load`（表）、`ecc_chip` |
| 2 | Witness 私有输入 | **L533–L592** | `psi_old, rho_old, cm_old, g_d_old, ak_P, nk, v_old, v_new` |
| 3 | Merkle path | **L595–L607** | `root`（未绑 instance） |
| 4 | Value commitment | L610–L665 | `cv_net` + `(magnitude, sign)`；绑 `CV_NET_X/Y` |
| 5 | Nullifier | L668–L684 | `nf_old`；绑 `NF_OLD` |
| 6 | Spend authority | L687–L704 | `rk = [α]SpendAuthG + ak`；绑 `RK_X/Y` |
| 7 | Diversified address | L707–L750 | `ivk = CommitIvk(ak, nk, rivk)`、`pk_d_old = [ivk] g_d_old` |
| 8 | Old note commitment | L753–L778 | `derived_cm_old == cm_old` |
| 9 | New note commitment | L782–L840 | 先见证 `g_d_new/pk_d_new`，再 `cmx = extract_p(cm_new)`；绑 `CMX` |
| 10 | Orchard gate | L843–L888 | 一行 4 条约束 + `q_orchard.enable` |

两处与"读完自然顺序"的偏差，值得记住：

1. **`cv_net` 在 `nf_old` 之前**（L610 早于 L668）。`nf_old` 不依赖 cv，但 cv 需要尽早拿到 `(magnitude, sign)` 供最后的 gate 使用。
2. **`g_d_new`/`pk_d_new` 直到 L782 才见证**，被夹在 old cm（L753）与 new cm（L803）之间。原因见 §5：old cm 用 `sinsemilla_chip_1 + note_commit_chip_old`，new cm 用 `sinsemilla_chip_2 + note_commit_chip_new`，两者列组不同，见证位置跟着 chip 走。

---

## 3. 第一块：8 个字段的迁移表

这一块的代码只有 60 行（L533–L592），但决定了后面每一块能从哪些 cell 拿到输入。

| `Circuit` 字段 | 类型 | 见证调用（行号） | 落点 | 后续消费者 |
|---|---|---|---|---|
| `psi_old` | `Value<pallas::Base>` | `assign_free_advice`（L535） | **`advices[0]`**，单行区域 | `derive_nullifier`(L675)、`note_commit`(L772) |
| `rho_old` | `Value<Rho>` → `Base` | `assign_free_advice`（L542） | `advices[0]` | `derive_nullifier`(L674)、`note_commit`(L771) |
| `cm_old` | `Value<NoteCommitment>` | **`Point::new`**（L549） | `advices[0]/[1]`（x/y）+ `q_point` | Merkle 叶子取 x(L605)、`derive_nullifier`(L676)、相等检查(L777) |
| `g_d_old` | `Value<NonIdentityPallasPoint>` | **`NonIdentityPoint::new`**（L556） | `advices[0]/[1]` + `q_point_non_id` | `[ivk] g_d_old`(L732)、`note_commit`(L768)、`AddressPoints` |
| `ak` | `Value<SpendValidatingKey>` | `NonIdentityPoint::new`（L564） | 同上 | `commit_ivk`(L721)、`rk` 加法(L699)、`extract_p`(L709) |
| `nk` | `Value<NullifierDerivingKey>` | `assign_free_advice`（L571） | `advices[0]` | `derive_nullifier`(L677)、`commit_ivk`(L722) |
| `v_old` | `Value<NoteValue>`(u64) | `assign_free_advice`（L578） | `advices[0]` | `v_net`(L613)、`note_commit`(L770)、gate(L846) |
| `v_new` | `Value<NoteValue>`(u64) | `assign_free_advice`（L585） | `advices[0]` | `v_net`(L613)、`note_commit`(L830)、gate(L847) |

### 3.1 三种见证原语，语义完全不同

| 原语 | 分配的行 | 生效的门 | 允许恒等？ |
|---|---|---|---|
| `assign_free_advice`（`gadget.rs` L110–L125） | **单行单列**，区域名 `"load private"` | 无（纯赋值） | 不适用（标量） |
| `Point::new` → `witness_point` | 1 行 × 2 列 | `q_point · x · (y²−x³−5) = 0` 与 `q_point · y · (…) = 0` | **允许**（`(0,0)` 走 "x==0 ∨ on_curve" 分支） |
| `NonIdentityPoint::new` → `witness_point_non_id` | 1 行 × 2 列 | `q_point_non_id · (y²−x³−5) = 0` | **不允许** |

三个可以直接落地的结论：

1. **`cm_old` 用 `Point` 不是笔误。** 它后面要参与 `cm.add(&product)`（完整加法，`gadget.rs` L204），且承诺点的恒等性不该被电路拒绝。而 `g_d_old`/`ak` 要作为标量乘的输入，必须非恒等，所以用 `NonIdentityPoint`。
2. **"非恒等"是 `on_curve` 门的副产品，不是额外约束。** 恒等点仿射坐标是 `(0,0)`，而 `0² = 0³ + 5` 不成立，所以只要满足 `y²=x³+5` 就必然不是恒等。`witness_point_non_id` 里那句 `error_if_known_and(|v| v == identity)`（`witness_point.rs` L177）只是**host 侧提前报错**，prover 拿着未知 `Value` 时它什么也不做——真正的保证来自门。
3. **5 个标量都落在 `advices[0]`，但不是"共用同一格"。** 每次 `assign_free_advice` 都开一个新区域、新的一行；它们与消费者之间靠 **permutation 复制约束**连接。上游自己在注释里承认这是冗余的（`gadget.rs` L104–L108："technically superfluous … We could get the best of both worlds with a write-on-copy abstraction"）。所以看到 `advices[0]` 出现 6 次，**不能**读成"同一列被复用 6 次"。

### 3.2 一个容易讲错的地方：`ScalarFixed::new` 此刻没有分配任何 cell

`alpha`(L689)、`rcv`(L646)、`rivk`(L710)、`rcm_old`(L754)、`rcm_new`(L814) 都走 `ScalarFixed::new`。而 `halo2_gadgets` 的实现是**惰性见证**：

```rust
fn witness_scalar_fixed(...) -> Result<EccScalarFixed, Error> {
    Ok(EccScalarFixed { value, windows: None })   // ecc/chip.rs L567–L573
    // "This chip uses lazy witnessing."
}
```

即：`new` 只把 `Value<Scalar>` 打包，**cell 要等到 `mul` 时才分配**。所以"witness 阶段把私有输入都放进电路"这句话对 5 个标量是**不成立**的——它们的落点由后续乘法决定（`ScalarFixed` 走 `mul_fixed` 的 window 布局，`ScalarFixedShort` 走 `mul_fixed_short`）。这直接影响读电路行数时的归因：`alpha/rcv/rivk/rcm_*` 的开销记在乘法和 CommitIvk/NoteCommit 的分解里，不在 §3 这张表里。

---

## 4. 第二块：Merkle Path

```rust
let path = self.path.map(|typed_path| typed_path.map(|node| node.inner()));   // L596–L598
let merkle_inputs = MerklePath::construct(
    [config.merkle_chip_1(), config.merkle_chip_2()],
    OrchardHashDomains::MerkleCrh, self.pos, path,
);                                                                            // L599–L604
let leaf = cm_old.extract_p().inner().clone();                                // L605
merkle_inputs.calculate_root(layouter.namespace(|| "Merkle path"), leaf)?     // L606
```

### 4.1 叶子是 `x(cm_old)`，不是整个点

`extract_p()` 丢掉 y 坐标，只留 `cm_old.x`。这与 `ExtractedNoteCommitment = x(NoteCommitment)`（`note/commitment.rs` L107–L111）一致：**树里存的一直是 cmx，不是 cm**。但 `cm_old` 的 y 并没有白见证——`derive_nullifier` 的 `cm.add(&product)` 需要完整点。同一个 witness 服务两个用途，这是 `Point` 而非 `ExtractedNoteCommitment` 类型的原因。

### 4.2 `pos` 的位序：我做了数值检查，不是只读注释

`calculate_root`（`halo2_gadgets/src/sinsemilla/merkle.rs` L119–L170）：

```rust
let pos: [Value<bool>; PATH_LENGTH] = self.leaf_pos.map(|pos| i2lebsp(pos as u64)) ...;
for (l, ((sibling, pos), chip)) in path.iter().zip(pos.iter()).zip(chips).enumerate() { ... }
```

`i2lebsp` 是**低位在前**，`path` 是**叶到根**，两者按同一索引 `l` 配对，所以：

```
pos 的第 l 位  ↔  path[l]  ↔  树的第 l 层（叶层 = 32，根 = 0）
```

这条约定如果读反，电路不会报错，只会算出一个错的 anchor。因此我用 host 侧 `MerklePath::root()`（`src/tree.rs` L152–L165，`position & (1 << l)`）作参照，另写一份按"电路约定"独立实现，再用 MSB-first 的错读法对比（`results/halo2/synthesize/merkle_pos_check.txt`）：

| `pos` | host 与 LSB 实现 | host 与 MSB 实现 |
|---|---|---|
| `0x00000000` | match | match（全 0，无差别） |
| `0x00000001` | **match** | DIFF |
| `0x00000002` | **match** | DIFF |
| `0x00000003` | **match** | DIFF |
| `0x80000000` | **match** | DIFF |
| `0x12345678` | **match** | DIFF |
| `0xffffffff` | match | match（全 1，无差别） |

LSB 读法 7/7 一致；MSB 读法只在"全 0 / 全 1"这两个退化点上偶然一致。也就是说**位序是数值可观测的**，不是记号偏好。

> 这个检查本身有个坑值得记下来：我第一版把兄弟节点构造成 `sibling(l) = H(l, sibling(l-1), leaf)`，结果 `pos=1` 与 `pos=3` 给出同一个根——因为此时第 1 层的"兄弟"恰好等于上一层的节点，交换两者是同一个哈希。改成每层独立构造才暴露真实差异。**构造测试向量时，任何"交换不可区分"的对称性都会把要测的东西测没。**

### 4.3 为什么是**两个** `MerkleChip`

`MerklePath::construct([chip_1, chip_2], …)` 之后 `layers_per_chip = ceil(32 / 2) = 16`（`merkle.rs` L122），`chips[i / 16]` 分配：chip_1 算 `l = 0..15`（叶侧 16 层），chip_2 算 `l = 16..31`（根侧 16 层）。

它们的配置互不重叠（`circuit.rs` L439–L471）：

| chip | advice 列 | Sinsemilla 域常量列 | 表 |
|---|---|---|---|
| `sinsemilla_config_1` / `merkle_config_1` | `advices[..5]` | `lagrange_coeffs[0]` | `table_idx` |
| `sinsemilla_config_2` / `merkle_config_2` | `advices[5..]` | `lagrange_coeffs[1]` | `table_idx` |

单个 Sinsemilla 实例只用 5 个 advice 列，若用一套实例走完 32 层，就要占 32 层的**行**、同时空着另外 5 列。拆成两套是把行数占用**减半**（各 16 层，分别落在两组列上）。

**但不要写成"并行加速"。** 第 16 层的输入是前 16 层的输出（`node` 在循环里串起来），依赖是严格串行的；拆列只改善面积/行数占用，不给证明时间带来并行收益。

### 4.4 `root` 不进 `Instance`——这是本块最重要的结构事实

Merkle 段**没有任何** `constrain_instance`。`root` 只是被 `copy_advice` 到 `advices[4]`（L861），`anchor` 则是从 `Instance[ANCHOR]` 读进 `advices[5]`（L862–L868），两者由 gate 的第 2 条约束绑定：

```
v_old · (root − anchor) = 0        // circuit.rs L357–L360
```

于是得到一个**必须写清楚的边界**：

> 当 `v_old = 0` 时，"算出的 root 等于公开 anchor"这条约束被 `v_old` 整个中和。此时 `path`、`pos`、`cm_old` 作为叶子仍然被见证、仍然被 Sinsemilla/条件交换的约束检查"算得对"，但**它们算出的 root 不与任何公开值比较**。

这正是 output-only Action 的形态（`enable_spend = 0`、`v_old = 0`）：见证值不被约束，但**仍然被合成**——32 层哈希、32 次条件交换、以及它们占的行，一个不少。这与两条已冻结的结论互相印证：

- V3 的 unpadded 输出 Action 与完整 Action 的 proving 成本差异在测量上未检出（`report_nu63_migration.md`），因为成本由**合成形状**决定，不由"这条见证是否被约束"决定；
- `configure()` 与 `synthesize_base()` 都不按 `enable_spend` 裁剪区域（没有 `if enable_spend` 分支），所以形状对空 Action 是刚性的。

### 4.5 门控方向是单向的，别读成"等价"

第 3、4 条约束：

```
v_old · (1 − enable_spend)  = 0     // L361–L364
v_new · (1 − enable_output) = 0     // L365–L368
```

这两条是**单向蕴含**：`v_old ≠ 0 ⇒ enable_spend = 1`。展开成真值表更清楚：

| `enable_spend` | 约束含义 | 允许的 `v_old` |
|---|---|---|
| `1` | `v_old · 0 = 0`，恒成立 | 任意 |
| `0` | `v_old · 1 = 0` | **只能为 0** |

即"关掉 spend 就不许有金额"，反过来"有金额就必须开着 spend"。而 `enable_spend` 本身来自公开输入，**电路不检查它与 `v_old` 之外任何东西的一致性**——这就是为什么 `disableCrossAddress` 那一行（`Instance` 第 9 行）在 `FixedPostNu6_2` 下可以随便填：那时候根本没有门去读它。

---

## 5. 本块实际落地的约束（"见证"≠"约束"）

| 来源 | 约束 | 数量级 |
|---|---|---|
| `witness_point` | `y² = x³ + 5`（`cm_old`） | 1 行 |
| `witness_point_non_id` | `y² = x³ + 5`（`g_d_old`、`ak`） | 2 行 |
| Merkle 32 层 | 每层 1 次条件交换 + 1 次 Sinsemilla 哈希（含其内部 range check / 分解门） | 32 × (常数) |
| `q_orchard` | 4 条多项式，1 行 | 1 行 |
| `assign_free_advice` × 5 | 无 | 5 行（纯赋值） |

也就是说，`synthesize_base` 的前两块里，**8 个 witness 中只有 3 个点各自换来 1 行 on-curve 门，5 个标量此刻不引入任何门**——它们只是 cell，其值要到被消费者以复制约束连上时才产生意义。Merkle 段则是本块唯一的"重"计算。这解释了为什么后面讨论成本时，必须区分"见证了一个值"和"约束了一个值"：前者占用 advice 列并进 permutation，后者才额外引入 gate / lookup 多项式。

`Instance` 的 10 行在这一块之后的使用情况也值得先记下来（完整表见下篇）：

| 行 | 常量 | 方向 | 在哪 |
|---|---|---|---|
| 0 | `ANCHOR` | instance → circuit | L862 |
| 1–2 | `CV_NET_X/Y` | circuit → instance | L660–L661 |
| 3 | `NF_OLD` | circuit → instance | L681 |
| 4–5 | `RK_X/Y` | circuit → instance | L702–L703 |
| 6 | `CMX` | circuit → instance | L839 |
| 7 | `ENABLE_SPEND` | instance → circuit | L870 |
| 8 | `ENABLE_OUTPUT` | instance → circuit | L878 |
| 9 | `DISABLE_CROSS_ADDRESS` | instance → circuit | **只在 `PostNu6_3`**（L967） |

**6 个读出、3 个读入（+1 个版本相关读入）**——这个不对称是理解"电路到底证明了什么"的关键：`cv_net/nf/rk/cmx` 由电路算出并**断言等于**公开值；`anchor/enable_*/disableCrossAddress` 是电路**接受**的外部条件，电路只负责"在这些条件下算对了"。

---

## 6. 本篇的可核查结论

| # | 结论 | 依据 |
|---|---|---|
| F1 | `AddressPoints` 是 NU6.3 的接口预留，不是普通返回值 | L505–L513、L890–L895、L944–L949 |
| F2 | 5 个标量落在 `advices[0]` 但各自独立单行区域，靠 permutation 连接 | `gadget.rs` L110–L125 及其注释 |
| F3 | `cm_old` 用 `Point`（允许恒等、需完整加法），`g_d_old/ak` 用 `NonIdentityPoint` | L549/L556/L564；`witness_point.rs` L48–L85 |
| F4 | 非恒等由 `on_curve` 门排除 `(0,0)` 得到，host 侧 `error_if_known_and` 只是提前报错 | `witness_point.rs` L53–L54、L177 |
| F5 | `ScalarFixed::new` 惰性见证，`alpha/rcv/rivk/rcm_*` 此刻不分配 cell | `ecc/chip.rs` L567–L573 |
| F6 | Merkle 叶子 = `x(cm_old)`；`pos` 位序为 LSB，bit l ↔ level l | L605；`merkle.rs` L119–L170；`merkle_pos_check.txt` |
| F7 | `root` 不绑 `Instance`；`v_old = 0` 时 Merkle 检查整体失效但区域照旧合成 | L861–L868 + L357–L360 |
| F8 | `enable_spend = 0 ⇒ v_old = 0`，反向不约束；`disableCrossAddress` 在旧版本无人读取 | L361–L368、L967 |

---

## 7. 下一步

第二块（`cv_net` 与 `nf_old`，L610–L684）需要回答四个问题：

1. `v_net = v_old − v_new` 是在**host 侧**算的（`Value<ValueSum>`，`value.rs` L142–L152），电路里没有减法门；电路只见证 `(magnitude, sign)` 并约束 `v_old − v_new = magnitude·sign`。那 `magnitude` 为什么落在 `advices[9]`——恰好是 `LookupRangeCheckConfig` 的列？范围检查是怎样被 `ScalarFixedShort` 触发的？
2. `derive_nullifier` 是 `cm + [Poseidon(nk, rho) + psi]·NullifierK`。`psi` 用普通加法（`AddChip`，落在 `advices[6..9]`），为什么不是再进一次 Poseidon？`NullifierK` 是 `FixedPointBaseField`（基域标量乘）而非 `ScalarFixed`，区别在哪？
3. `nf_old` 只 `extract_p()` 取 x 再绑 `Instance[NF_OLD]`——y 为什么不需要？
4. 这一步同时产出 `rho_new = nf_old`（L805）：新 note 的 `ρ` 直接复用旧 nullifier 的 x 坐标，跨 note 的链式关系是怎么在**约束层面**表达出来的（copy 而非计算）？
