# 01 · Orchard Action 电路：`Circuit` 与 `Instance` 数据结构

- 源码：`zcash/orchard` @ `616a669d`，`src/circuit.rs`（2119 行）
- 阅读范围：`L75–L235`（常量与 `Circuit`）、`L1169–L1300`（`Instance`）、`L1417–L1534`（测试夹具）
- 关联文件：`src/bundle.rs:L38–L52`（`Action::to_instance`）、`src/constants.rs:L14`（树深 32）

---

## 1. 电路骨架参数

| 项 | 值 | 出处 |
| --- | --- | --- |
| 电路规模 | `K = 11` → 求值域 2¹¹ = 2048 行 | `L76` |
| Instance 列数 | 1 | `L79` |
| Instance 行数 | 10 | `L80` |
| Advice 列数 | 10（`advices[0..10]`） | `L314–L325` |
| Instance 列 | `primary`，`enable_equality(primary)` | `L385–L386` |
| 选择器 | `q_orchard`（单一选择器承载 4 条约束） | `L334` |
| Fixed 列 | 8 个 `lagrange_coeffs`，其中 `[0]` 兼作常量列 | `L398–L413` |
| Lookup | Sinsemilla 生成元表：1 个 table column + 2 个辅助列 | `L377–L382` |
| 曲线关系 | 电路域 `F = pallas::Base`；承诺群 `E = Vesta`（`Params<vesta::Affine>`） | `L1084`、`L1123` |
| 树深 | `MERKLE_DEPTH_ORCHARD = 32` | `constants.rs:L14` |

> 曲线这层容易被绕晕：`pallas::Base = vesta::Scalar`，所以「电路跑在 `pallas::Base` 上」与「IPA 承诺在 Vesta 群里」是同一件事，不矛盾。**电路内**运算的曲线点（`rk`、`cv_net`、`cmx`、`g_d`、`pk_d`）都是 **Pallas** 点，其坐标恰好在电路域里；**证明自身**的承诺是 **Vesta** 点。

---

## 2. `Circuit`：证明者掌握的私有 Witness

```rust
pub struct Circuit {
    pub(crate) path: Value<[MerkleHashOrchard; MERKLE_DEPTH_ORCHARD]>,
    pub(crate) pos: Value<u32>,
    pub(crate) g_d_old: Value<NonIdentityPallasPoint>,
    ...
    pub(crate) circuit_version: OrchardCircuitVersion,
}
```

19 个 witness 字段 + 1 个非 witness 的版本标记。按语义分三组：

### 2.1 Spend 侧（被花费的 note）

| 字段 | 类型 | 含义 | 在电路中的作用 |
| --- | --- | --- | --- |
| `path` | `Value<[MerkleHashOrchard; 32]>` | 该 note 承诺的 Merkle 认证路径 | 逐层 Sinsemilla 哈希到 `root` |
| `pos` | `Value<u32>` | 该 note 在树中的位置（决定左右） | 决定每层拼接顺序 |
| `cm_old` | `Value<NoteCommitment>` | 被花 note 的承诺 | 作为路径起点（叶子） |
| `g_d_old` | `Value<NonIdentityPallasPoint>` | 发送方地址的 diversified base | 地址合法性与 note 承诺绑定 |
| `pk_d_old` | `Value<DiversifiedTransmissionKey>` | 发送方的 diversified transmission key | 同上 |
| `v_old` | `Value<NoteValue>`（`u64`） | 输入面额 | 值平衡约束 + 决定是否跳过路径检查 |
| `rho_old` | `Value<Rho>` | 输入 note 的 rho | nullifier 派生 |
| `psi_old` | `Value<pallas::Base>` | 输入 note 的 psi | note 承诺随机化输入 |
| `rcm_old` | `Value<NoteCommitTrapdoor>` | 输入 note 承诺随机数 | 重算 `cm_old` |
| `alpha` | `Value<pallas::Scalar>` | SpendAuth 随机化因子 | `rk = ak.randomize(alpha)` |
| `ak` | `Value<SpendValidatingKey>` | spend validating key | 推出 `rk` 的基点 |
| `nk` | `Value<NullifierDerivingKey>` | nullifier 派生密钥 | 约束 `nf_old = PRF_nk(rho_old)` |
| `rivk` | `Value<CommitIvkRandomness>` | `commit_ivk` 的随机数 | 重算 `ivk` → 校验 `g_d_old/pk_d_old` 属于同一地址 |

### 2.2 Output 侧（新生成的 note）

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `g_d_new` | `Value<NonIdentityPallasPoint>` | 收款方地址的 diversified base |
| `pk_d_new` | `Value<DiversifiedTransmissionKey>` | 收款方的 diversified transmission key |
| `v_new` | `Value<NoteValue>` | 输出面额 |
| `psi_new` | `Value<pallas::Base>` | 输出 note 的 psi |
| `rcm_new` | `Value<NoteCommitTrapdoor>` | 输出 note 承诺随机数 |

注意：`g_d_new` / `pk_d_new` 是**直接给出**的，不需要 `rivk` 反推——因为收款方可以是任意人，证明者只知道其地址，不知道对方的 `ivk`。这正是「跨地址」能被构造出来的原因。

### 2.3 Action 级与元信息

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `rcv` | `Value<ValueCommitTrapdoor>` | 值承诺 `cv_net` 的随机数 |
| `circuit_version` | `OrchardCircuitVersion` | **不是 witness**：选 `InsecurePreNu6_2` / `FixedPostNu6_2` / `PostNu6_3`，决定 `configure` 与 `synthesize` 的分支 |

### 2.4 为什么用 `Value<T>`

`Value<T>` 是 halo2 的三态容器：`Value::known(x)` / `Value::unknown()`（以及内部的 assignment 语义）。Orchard 用完形相同的两个构造器覆盖了两种用途：

| 构造器 | witness | 用途 |
| --- | --- | --- |
| `from_action_context(..)` / `from_action_context_unchecked(..)` | 全部 `Value::known(..)` | 真实证明 |
| `empty(version)`（`L212`，私有） | 全部 `Value::unknown()` | `keygen_vk` / `keygen_pk` / 布局渲染 |

这一点值得记住：**生成 PK/VK 时电路里没有任何真实数据**，`without_witnesses()` 返回的就是 `Circuit::empty(self.circuit_version)`（`L1056`）。所以「电路形状」完全由 `configure()` 决定，与 witness 无关——这也是为什么 K=11 是常数、PK 可预生成。

另外，`from_action_context` 有一个显式前置条件（`L259`）：

```rust
(Rho::from_nf_old(spend.note.nullifier(&spend.fvk)) == output_note.rho()).then(|| ...)
```

即输出 note 的 `rho` 必须等于本 Action 输入 note 的 nullifier。这是「一个 Action = 一个 Spend + 一个 Output」的链接约束，在电路之外就检查掉了。

---

## 3. `Instance`：验证者必须知道的公开输入

```rust
pub struct Instance {
    anchor: Anchor,
    cv_net: ValueCommitment,
    nf_old: Nullifier,
    rk: VerificationKey<SpendAuth>,
    cmx: ExtractedNoteCommitment,
    enable_spend: bool,
    enable_output: bool,
    cross_address_disabled: bool,
}
```

8 个字段 → `to_halo2_instance()`（`L1271`）展开成 **10 行 × 1 列**：

| 行号 | 常量名 | 内容 | 为什么占这些行 |
| ---: | --- | --- | --- |
| 0 | `ANCHOR` | Merkle 树根 | 1 个域元素 |
| 1–2 | `CV_NET_X` / `CV_NET_Y` | 值承诺 `cv_net` 的坐标 | Pallas 点 → 2 行 |
| 3 | `NF_OLD` | 输入 note 的 nullifier | 1 个域元素 |
| 4–5 | `RK_X` / `RK_Y` | 随机化验证密钥 `rk` 的坐标 | RedPallas 点 → 2 行 |
| 6 | `CMX` | 输出 note 的承诺 `cmx` | 1 个域元素 |
| 7 | `ENABLE_SPEND` | `flags.spends_enabled()` → {0,1} | 布尔 |
| 8 | `ENABLE_OUTPUT` | `flags.outputs_enabled()` → {0,1} | 布尔 |
| 9 | `DISABLE_CROSS_ADDRESS` | `!flags.cross_address_enabled()` → {0,1} | 布尔（NU6.3 新增） |

5×32 字节的 Action 公开数据（anchor / cv_net / nf / rk / cmx）在这里被拆成 8 个域元素，加 3 个标志位凑成 10 行。

### 3.1 三个必须注意的实现细节

**（1）`rk` 的坐标是「解回来」的。** `to_halo2_instance` 并不直接存 `rk` 的坐标，而是把 `rk` 的规范字节编码 `from_bytes` 解回点，再取 `coordinates()`（`L1279–L1286`）。`rk` 在构造时已被保证非单位元（`Instance::from_parts` 里 `(!rk.is_identity()).then_some(..)`），所以 `.expect(...)` 是安全的。

**（2）第 10 行的加入没有改变旧承诺。** 注释 `L1290–L1294` 说得很清楚：instance 列在求值域上会被**零填充**，所以当 `disableCrossAddress = 0` 时，10 行编码与历史上的 9 行编码是 commitment-identical 的。这就是为什么加一行不破坏旧 VK 的兼容性。

**（3）字段全私有，只给 `pub(crate)` 访问器。** `anchor()` / `cv_net()` / `nf_old()` / `rk()` / `cmx()` 都是 `pub(crate)`，外部只能通过 `from_parts` 构造。这是有意的 API 收紧——`Instance` 是安全边界，不能让调用方随手改。

### 3.2 `Instance` 从哪里来

`Instance` 不是凭空构造的，正常路径是 `Action → Instance`（`bundle.rs:L41`）：

```rust
pub fn to_instance(&self, flags: Flags, anchor: Anchor) -> Instance {
    Instance::from_parts(
        anchor, self.cv_net().clone(), *self.nullifier(),
        self.rk().clone(), *self.cmx(), flags,
    ).expect("...rk is non-identity by construction...")
}
```

三个 `bool` 全部来自 **bundle 级** `Flags`，不是 Action 自己的属性。也就是说：同一个 Bundle 里所有 Action 的这三行取值必然相同，这一点是 bundle 层的不变量，电路层不检查。

---

## 4. Circuit 与 Instance 的边界

| | `Circuit` | `Instance` |
| --- | --- | --- |
| 谁掌握 | 证明者 | 证明者与验证者都掌握 |
| 内容 | Merkle 路径、note 明文、各类密钥与随机数 | 5 个公开值 + 3 个标志 |
| 传递接口 | `Proof::create(pk, &circuits, ..)` | `Proof::create(.., &instances, ..)` **和** `Proof::verify(vk, &instances)` |
| 缺了会怎样 | 无法生成证明 | 无法验证；且验证者无法知道该证明绑定了哪棵树、哪个 nullifier |

最容易被忽略的一条：**`v_old` / `v_new` 的具体数值不在 instance 里**。验证者只看到 `cv_net`（值承诺），看不到面额。值守恒是靠电路约束 + 承诺绑定共同保证的，不是靠公开数字。

反方向也有一条：**`cm_old` 在 Circuit 里，`cmx` 在 Instance 里，两者不是同一个东西**。`cm_old` 是被花 note 的承诺（私有，用来算 Merkle 根），`cmx` 是新 note 的承诺（公开，写在交易里）。

---

## 5. `plonk::Circuit` 的三个方法在 Orchard 的落地

```rust
impl plonk::Circuit<pallas::Base> for Circuit {
    type Config = Config;
    type FloorPlanner = floor_planner::V1;   // L1054

    fn without_witnesses(&self) -> Self {    // L1056
        Self::empty(self.circuit_version)
    }

    fn configure(meta: &mut ConstraintSystem<pallas::Base>) -> Self::Config {  // L1060
        Config::configure(meta)
    }

    fn synthesize(&self, config, mut layouter) -> Result<(), plonk::Error> {  // L1064
        let addrs = self.synthesize_base(&config, &mut layouter)?;
        if self.circuit_version.supports_cross_address_restriction() {
            Self::synthesize_cross_address_checks(&config, &mut layouter, &addrs)?;
        }
        Ok(())
    }
}
```

三点值得注意：

1. `type Config = Config;` 是**同名的两个东西**：`plonk::Circuit::Config`（关联类型）和 `circuit::Config`（结构体）。`configure` 只做转发，真正的配置在 `impl Config` 里（`L312`），共约 200 行。
2. `FloorPlanner = floor_planner::V1`，不是 V2。V1 是按 `assign_region` 声明顺序放置的旧版规划器，配合 `Cargo.toml` 里 `halo2_proofs` 启用的 `floor-planner-v1-legacy-pdqsort` feature。
3. `synthesize` 只有两层：`synthesize_base`（永远执行）+ `synthesize_cross_address_checks`（仅 NU6.3 且版本支持时执行）。**版本差异不在 `configure` 里，而在 `synthesize` 的第二个分支**——两个版本用的是同一套列、同一个 `q_orchard` 门，`PostNu6_3` 只是多铺了 4 行。

---

## 6. 三组 MockProver 检查到底在检查什么

`post_nu6_3_cross_address_restriction_is_conditional`（`L1657`）跑的是**完整 Action 电路**：

```rust
let mock_verify = |circuit, instance| {
    MockProver::run(K, circuit, instance.to_halo2_instance().iter().map(|p| p.to_vec()).collect())
        .unwrap().verify()
};
```

三组断言：

| 组 | circuit | `cross_address_disabled` | 期望 |
| --- | --- | --- | --- |
| 1 | 输出地址 ≠ 输入地址 | `false` | `Ok(())` |
| 2 | 输出地址 ≠ 输入地址（同一个 circuit） | `true` | `Err(..)` |
| 3 | 自转账（地址相同） | `true` | `Ok(())` |

要理解为什么，得看 `q_orchard` 的 4 条约束（`L350–L370`）：

| # | 约束 | 正式含义 |
| ---: | --- | --- |
| 1 | `v_old − v_new − magnitude·sign = 0` | 值平衡：`v_old − v_new = magnitude·sign` |
| 2 | `v_old·(root − anchor) = 0` | 要么 `v_old = 0`（空 Spend），要么 Merkle 根必须等于公开 anchor |
| 3 | `v_old·(1 − enable_spend) = 0` | 非零面额必须 `enable_spend = 1` |
| 4 | `v_new·(1 − enable_output) = 0` | 非零面额必须 `enable_output = 1` |

关键在于 **`synthesize_cross_address_checks`（`L939`）复用了同一个 `q_orchard`，但把 10 个 advice 单元重新解释了一遍**：

| 单元 | 主电路含义 | 地址检查行的含义 |
| --- | --- | --- |
| `advices[0]` | `v_old` | `disableCrossAddress`（从 instance 拷入） |
| `advices[1]` | `v_new` | 常量 `0` |
| `advices[2]` | `magnitude` | `disableCrossAddress` |
| `advices[3]` | `sign` | 常量 `1` |
| `advices[4]` | `root` | 旧坐标（`g_d_old` / `pk_d_old` 的 x 或 y） |
| `advices[5]` | `anchor` | 新坐标（`g_d_new` / `pk_d_new` 的 x 或 y） |
| `advices[6]` | `enable_spend` | 常量 `1`（把约束 3 中和掉） |
| `advices[7]` | `enable_output` | 常量 `1`（把约束 4 中和掉） |

代进 4 条约束（记 `D = disableCrossAddress`）：

```
1)  D − 0 − D·1 = 0          → 恒成立（占位）
2)  D · (旧坐标 − 新坐标) = 0 → D=1 时强制 旧坐标 = 新坐标；D=0 时自动成立
3)  D · (1 − 1) = 0           → 恒成立
4)  0 · (1 − 1) = 0           → 恒成立
```

于是三组结果就有了确定的解释：

- **组 1 通过**：`D = 0`，约束 2 退化为 `0 = 0`，地址差异根本不进入约束。
- **组 2 失败**：`D = 1`，而夹具用一个 `loop` 保证 `!sender_address.same_expanded_receiver(&output_note.recipient())`（`L1484–L1490`），即 `(g_d, pk_d)` 至少有一个坐标不同 → 4 行里有对应的行违反约束 2。
- **组 3 通过**：自转账让 `g_d_new = g_d_old`、`pk_d_new = pk_d_old`，4 个差值全为 0。

这就是「同一个门、同一个选择器，靠单元格的语义重载实现第二种约束」的典型写法。它的代价是可读性：约束 1/3/4 在这些行上必须被刻意中和成 0，否则会误伤。源码里那几行 `"one (neutralize enable_spend check)"` 的标签就是在提醒这件事。

---

## 7. 本轮运行结果

两个测试均通过（`results/raw/halo2_task1/`）：

| 命令 | 结果 | 耗时 |
| --- | --- | --- |
| `cargo +1.88 test --locked -p orchard --features circuit circuit::tests::post_nu6_3_cross_address_restriction_is_conditional -- --exact --nocapture` | 1 passed | 26.90 s |
| `cargo +1.88 test --locked -p orchard --features circuit circuit::tests::post_nu6_3_restricted_statement_proves_and_verifies -- --exact --nocapture` | 1 passed | 37.19 s |

第二个测试内部走的是真实路径：`Proof::create(pk, ..)` → `Proof::verify(vk, ..)`，用的是 `cached_test_keys(PostNu6_3)` 生成的 PK/VK（debug 构建下 PK 生成是主要耗时）。

> `--nocapture` 在这里没有产生额外输出，因为测试用 `assert_eq!` 而非打印。想看到**具体哪一行约束失败**，需要让 `MockProver` 的 `Err` 被 `{:?}` 打出来；这需要在一个临时 worktree 里加一行 `println!("{:?}", mock_verify(..))`，不改动上游基线仓库。方案留到下一轮需要时再做。

---

## 8. 容易记错的点

1. `K = 11` 是**求值域大小 2048**，不是 2048 条约束。
2. `Instance` 是 10 行**不是** 9 行；第 10 行 `disableCrossAddress` 是 NU6.3 新增，且旧的 9 行编码在 `D = 0` 时与之 commitment-identical。
3. `Circuit` 和 `Instance` 里没有重复字段；看起来像的有两对，但都不是：`cm_old`（输入承诺，私有）vs `cmx`（输出承诺，公开）；`v_old/v_new`（面额，私有）vs `cv_net`（值承诺，公开）。
4. `enable_spend` / `enable_output` / `cross_address_disabled` 是 **bundle 级 `Flags`** 投影到每个 Action 的 instance，不是 Action 自身的属性。
5. `synthesize` 的版本分支不改变 `configure`。`PostNu6_3` 的 VK 与 `FixedPostNu6_2` 不同，原因是 `synthesize` 多铺了一个 region，改变了电路形状（进而改变 keygen 结果），而不是因为多配了列。

---

## 9. 下一步

下一组源码问题：`Config::configure()`（`L312–L506`，约 195 行）。

待回答：

- 10 个 advice 列的**分工**（哪些给 ECC、哪些给 Poseidon、哪些给 Sinsemilla/Merkle、哪些给 range check）；
- `q_orchard` 与各 chip 内部的 selector 如何共存；
- 两组 `SinsemillaConfig` + 两组 `MerkleConfig` + 两组 `NoteCommitConfig`（old/new）为什么要成对配置；
- `LookupRangeCheckConfig` 用的是哪一列、查的表是什么；
- `enable_equality` 为什么把 10 个 advice 全部打开。
