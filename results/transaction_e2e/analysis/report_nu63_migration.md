# NU6.3 迁移交易的端到端成本（V3：Orchard V3 + Ironwood V3）

> 冻结时间：2026-11-03
> 上游：`zcash/orchard` @ `616a669d`（`0.16.0`）｜rustc 1.88.0 msvc｜i7-14700
> 对照基线：[`report_v2_1s2o.md`](report_v2_1s2o.md)（V2，1S+2O，2 Actions，3.202 s）

---

## 0. 结论

一笔 NU6.3 迁移形状的交易（Orchard V3 的 1 Spend + 1 Change，加 Ironwood V3 的 1 Output），合计 **3 个 Action、2 个独立 Bundle**，单线程 P 核证明生成合计 **5.285 s**，是 V2 的 1S+2O（2 Actions、1 Bundle、3.202 s）的 **1.65 倍**。

增量的分解（这是本轮最重要的结果）：

| 来源 | 增量 | 占总增量 |
|---|---:|---:|
| 多一个 Action（3 vs 2） | +1.29 s | 62% |
| **拆成两个 Bundle** | **+0.80 s** | **38%** |

第二项与 Action 数无关，是**纯粹的结构性代价**：两个 Bundle 各自要付一次证明系统的固定开销。同样 3 个 Action 如果放在一个 Bundle 里，模型预测 4.48 s；实际拆成两个 Bundle 是 5.28 s。

证明体积上有完全同构的现象：两个 Bundle 合计 12 256 B，而单 Bundle 3 Action 只需 9 536 B，差 **恰好 2 720 B** —— 正好是一个 proof 的 base size。

---

## 1. V3-A：形状验收

不能只凭"1 Spend + 1 Change = 2 Actions"的算式认定结果。跨地址限制打开后，`num_actions` 的公式本身就是 **`spends + outputs`** 而不是 `max`（`BundleType::num_actions` 里明确写了这一点），所以必须回读 Builder 的 metadata。

实测（`SHAPE` 行，harness 直接打印）：

| 项 | Orchard V3 | Ironwood V3 |
|---|---|---|
| `bundle_version` | `orchard_v3` | `ironwood_v3` |
| 电路版本 | `PostNu6_3` | `PostNu6_3`（共用同一 PK） |
| `flag_byte` | **3**（`0b011`） | **7**（`0b111`） |
| spends / outputs enabled | true / true | true / true |
| `cross_address_enabled` | **false** | **true** |
| `value_balance` | **+15 000 000** | **−10 000 000** |
| Actions | **2** | **1** |
| spend 落在 action | **1** | （无真实 spend） |
| output 落在 action | **0** | 0 |

关键结论：**spend 在 action 1、change 在 action 0，两者不共享 action**。这正是 `spends + outputs = 2` 的直接证据——如果两个 Action 数只是"1+1 恰好等于 2"，那和"共享 action 后补一个填充"是无法区分的，而这两种结构的电路完全不同。

`flag_byte` 也符合预期：Orchard V3 的第 2 位是 0（协议强制），Ironwood 是 7（三位置满）。`value_balance` 一正一负、绝对值差 5 000 000，就是留在交易层的 fee。

两侧证明都通过 `verify_proof`，共用同一个 `PostNu6_3` ProvingKey。

---

## 2. V3-B：证明成本

单线程 P 核，5 次重复：

| Bundle | Actions | Build | **Prove** | Sign | Verify | Proof | Bundle 编码 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Orchard V3 | 2 | 12.2 ms | **3.2608 s** | 0.70 ms | 32.2 ms | 7 264 B | 9 009 B |
| Ironwood V3 | 1 | 4.5 ms | **2.0240 s** | 0.38 ms | 26.8 ms | 4 992 B | 未测（v6 编码不在本 crate） |
| **合计** | **3** | 16.7 ms | **5.2847 s** | 1.08 ms | 59.0 ms | 12 256 B | — |

8 线程（掩码 `0x5555`）：

| Bundle | Prove | 加速比 |
|---|---:|---:|
| Orchard V3（2 acts） | 0.8043 s | 4.05× |
| Ironwood V3（1 act） | 0.4794 s | 4.22× |
| 合计 | 1.2837 s | **4.12×** |

并行效率和 V2 的 2-Action（4.58×，效率 0.57）处在同一量级，说明拆池没有改变并行结构。

### 2.1 逐次分布

`Orchard V3`：3.229 / 3.329 / 3.344 / 3.191 / 3.211 s（均值 3.261，min 3.191，RSD 2.0%）
`Ironwood V3`：1.940 / 2.068 / 2.188 / 1.928 / 1.996 s（均值 2.024，min 1.928，RSD 5.5%）

**Ironwood 的抖动比 V2 大得多（5.5% vs 0.7%）**，而且均值被 2.188 s 这一次拉高。所以下面比较时我优先看 min 值：Orchard V3 min 3.191 s 对 V2 min 3.182 s，差 0.3%；Ironwood min 1.928 s 对阶段 A 官方单 Action 基线 1.929 s，差 0.05%。

**这说明电路版本从 `FixedPostNu6_2` 换到 `PostNu6_3`，对单个 Action 的证明成本没有可观测的影响。** 均值上的 +2%~+5% 差异落在本轮的噪声范围内，不能当作版本代价的证据。

---

## 3. V3-C：与 V2 的对比

| | V2 1S+2O | V3 迁移 | 变化 |
|---|---:|---:|---:|
| Actions | 2 | 3 | +1 |
| Bundles | 1 | **2** | +1 |
| Prove | 3.202 s | 5.285 s | **+65.0%** |
| Verify | 29.5 ms | 59.0 ms | +100% |
| Proof 合计 | 7 264 B | 12 256 B | +68.7% |
| Peak WS | 114.8 MiB | 114.5 MiB | 持平 |

### 3.1 增量分解

用 V2 拟合出的 `T(n) = 0.606 + 1.292·n`：

| 项 | 计算 | 值 |
|---|---|---:|
| V2 基线（2 Actions，1 Bundle） | 实测 | 3.202 s |
| 多一个 Action | $+1.292$ | +1.292 s |
| 同样 3 Actions 放在**一个** Bundle 的预测值 | $0.606 + 1.292×3$ | 4.482 s |
| 实测（拆两个 Bundle） | — | 5.285 s |
| **拆池的结构代价** | $5.285 - 4.482$ | **+0.803 s（+17.9%）** |

也就是说，NU6.3 迁移交易比 V2 贵 2.08 s，其中 **1.29 s 来自多出来的那个 Action，0.80 s 来自必须分成两个 Bundle**。后者不产生任何额外的约束或证明内容，纯粹是"多起一次证明"的固定开销。

### 3.2 证明体积上的同构现象

| 情形 | Actions | Bundles | Proof |
|---|---:|---:|---:|
| V2 基线 | 2 | 1 | 7 264 B |
| 假设：3 Actions 单 Bundle | 3 | 1 | 9 536 B |
| 实测：3 Actions 双 Bundle | 3 | 2 | **12 256 B** |
| 差值 | — | +1 | **+2 720 B** |

2 720 B 正是 `Proof::expected_proof_size` 里的 `BASE` 常数（`BASE + PER_ACTION × n`）。**多一个 Bundle 就多一份 proof header，与 Action 数无关。**

这条和 3.1 的 0.80 s 是同一个现象的两个侧面：proof header 在字节上直接可见，在时间上表现为一次完整的固定开销（IPA 参数、commitment、opening 的固定部分）。

### 3.3 验证侧同样翻倍

Verify：29.5 ms → 59.0 ms。单个 3-Action Bundle 按 V2 的拟合（斜率 3.7 ms/action、截距 22 ms）应该是 ~33 ms。实测 59 ms，几乎正好是两次独立验证（32.2 + 26.8）。

**这是给协议设计的直接含义**：把一次价值转移拆成两个 Pool 的 Bundle，验证方要付两次固定验证开销。在区块验证是瓶颈的场景下，这个代价和证明时间一样值得记录。

---

## 4. 局限

1. **合成 fixture**：Spend 的 note 仍在叶子 0、空兄弟路径；Ironwood 侧没有真实 spend，anchor 用的是 `Anchor::empty_tree()`。这测的是电路与证明系统的成本，**不构成"我们构造了一笔真实链状态的主网交易"的证据**。
2. **不含交易层**：sighash、v6 交易编码、fee 计算、序列化都没有。Orchard V3 的 `encoded_bytes` 仍按 v5 时代的 Orchard 布局估算；Ironwood 的 v6 编码本 crate 没有实现，所以没报。
3. **Ironwood 的 note 版本是 V3**（V3 明文格式），与 Orchard 的 V2 不同，但两者的密文长度是否一致我**没有验证**——所以不给 Ironwood 的编码长度。
4. 只测了 1 线程和 8 线程两组，没有重铺线程矩阵；这也正是本轮的设计意图。
5. `value_balance` 一正一负加起来是 +5 M，不是 0——差额是交易层的 fee，两者的关系由外层交易保证，本 crate 不管。

---

## 5. 下一步

Orchard 端到端实验到此收束。不再扩展 Action / 线程矩阵。

转入 `Config::configure()`，要回答的四个问题：

1. 10 个 Advice Column 各自的职责；
2. `q_orchard` 的四条核心约束分别约束什么；
3. 各 Gadget Config 如何复用列、Selector 与 Lookup；
4. `PostNu6_3` 新增的跨地址限制加了哪些约束、如何改变电路布局。

第 4 项现在有了实验对照：我们知道它**没有**改变单 Action 的证明成本（min 值差 0.3%），但**改变**了 Action 的计数规则（`spends + outputs` 而非 `max`）。前者说明那 4 行约束被 2¹¹ 的填充吸收掉了，后者说明它改变了整个 Bundle 的结构。这两件事在源码里应该是分开的两处，需要分别定位。
