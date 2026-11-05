# F4-A.3 · Action 数量适用边界测试（预注册）

**状态**：预注册，先于测量冻结。测量结果另起 §8 追加，预注册正文不改。
**冻结版本**：F4-A 已验收冻结（doc 24），本轮**不改 `halo2_proofs` 的 f4a 补丁、不新增优化**，
只新增 harness 的测量模式。
**上游**：[`24_f4a_implementation.md`](24_f4a_implementation.md)、
[`25_f4a_resource_validation.md`](25_f4a_resource_validation.md)。

---

## 1. 研究问题

F4-A 的 −22.00%（1P）是在**一个固定配置**上测出来的：Orchard V3，1 spend + 1 change，
2 个 action。它没有回答一个更基础的问题：

> 这项收益是随 action 数量增长、保持不变，还是只在当前两 action 配置下才明显？

必须区分三种可能的形状，因为它们对应三种完全不同的机制解释：

| 形状 | `ΔT(n)/n` | 机制解释 | 后续含义 |
|---|---|---|---|
| 恒定 | 常数 | 每个 action 内部就有等量的重复恢复链；跨 action 只是把同一件事再做一遍 | 收益是**每 action 的结构性质**，与规模无关 |
| 增长 | 上升 | memo 的填充成本被更多消费者摊销，且跨 action 的链被合并到同一 slot | 收益是**规模的函数**，大 bundle 才是主战场 |
| 下降 | 下降 | 复用受执行上下文限制，或固定开销占比上升 | 当前 22% 可能是小配置的巧合，需重新界定适用范围 |

**本阶段只回答形状，不回答"Halo2 通用性"。** 后者（换一个同样有 selector compression
恢复链的电路）是下一阶段，见 §7。

---

## 2. 为什么用 action 数量做标度变量

`prover.rs` 的表达式展开是 `advice_cosets.iter().flat_map(...)`：**每个 action 一份**。
所以 action 数量直接控制"同一批恢复链被重复计算多少遍"，而**不改变**：

- 电路版本（`PostNu6_3`）与 `configure()` 产生的门表；
- 选择器压缩的结果（固定列与 `compress_selectors` 的输出）；
- 证明域大小 K。

换句话说，n 是本实验里唯一被改变的变量，而它改的正是 F4-A 要消除的那种重复。
这比"换一个电路"更干净：换电路会同时改门表、K、selector 集合，收益变化无法归因。

---

## 3. 可构造的 n（构造可行性已先行验证）

Orchard 的电路是**每 action 一个 `Circuit` 实例**，`create_proof` 接收实例切片，
K 由单 action 的电路形状决定，与实例数无关。两个 NU6.3 池对"花多少 action"的算法不同：

```
cross-address 关闭（orchard_v3）：actions = spends + outputs
cross-address 开放（ironwood_v3）：actions = max(spends, outputs)
```

因此：

| 池 | 可得的 n | 构造 | flags |
|---|---|---|---|
| `ironwood_v3` | **1, 2, 4, 8** | n spends + n plain outputs，全部同一地址 | spends ✅ outputs ✅ cross-address ✅ |
| `orchard_v3` | **2, 4, 8** | n/2 spends + n/2 change outputs | spends ✅ outputs ✅ cross-address ❌ |

`orchard_v3` 拿不到 n=1：cross-address 关闭时每个 spend 必须与一个独立 action 的
fabricated output 配对，1 spend 就已经是 2 个 action。**这个"拿不到"是协议性质，不是实现缺陷**，
所以 n=1 只有 ironwood 一个点。

**两个池的 flags 不同，但电路相同。** `PostNu6_3` 的 cross-address 约束由 instance 里的
flag 值启用，不是额外的门；两池共用同一 `OrchardCircuitVersion`。所以：

- 同一池内，n 是唯一变量；
- 跨池比较**不是**同一实验（flags 不同），只作为"同一 n 下两条独立曲线是否给出一致形状"的交叉检验；
- 两条曲线在任何报告里都分开画，不拟合成一条。

**K 的固定性**：每个进程只调用一次 `ProvingKey::build(PostNu6_3)`，所有 n 复用同一
`pk`/`vk`。K 因此按构造相同——不是"测出来相同"，而是"只可能相同"。

### 3.1 fixture

`notes_at_leaves`：把 `k` 个 note 放在同一棵树的叶子 `0..k`，一次性建出各层子树根，
每个叶子的认证路径取 `(i >> l) ^ 1` 位置的兄弟根（空侧取空子树根），并在构造后断言
所有路径根到同一 anchor。这是上游 builder 测试里 shared-anchor witness 的同一套构造。

- 每个 spend note 100 000 000，每个 output/change 85 000 000；
- n 个 action 的价值平衡 = 15 000 000 × n（对 `i64` 与 `MAX_MONEY` 都安全）；
- 每个 note 的 `rho`/`rseed` 独立抽取，nullifier 互不相同。

**这不是填充电路**：n 个 action 花的是 n 个真实存在的 note，走的是同一条生产代码路径。

### 3.2 已完成的构造探针（1 对，非性能证据）

在写本预注册前，用 `--reps 1` 确认每个 n 都能 build + prove + verify，action 数与请求一致：

| 池 | n | 观测 actions | A 臂 prove ms | B 臂 prove ms | proof B | 指纹 |
|---|---:|---:|---:|---:|---:|---|
| ironwood | 1 | 1 | 2 067.5 | 1 706.1 | 4 992 | `0xe6e272db9558754f` |
| ironwood | 2 | 2 | 3 224.2 | 2 525.0 | 7 264 | `0x06b0dc62e07442e2` |
| ironwood | 4 | 4 | 5 977.0 | 4 357.1 | 11 808 | `0x4c7f4cc14f56efce` |
| ironwood | 8 | 8 | 11 166.3 | 7 964.6 | 20 896 | `0xffdd414075b7d1ea` |
| orchard | 2 | 2 | 3 375.9 | 2 567.1 | 7 264 | `0xa6bcd171d69e969a` |
| orchard | 4 | 4 | 5 784.3 | 4 246.6 | 11 808 | `0x2c90cbe723f5ec2f` |
| orchard | 8 | 8 | 10 906.1 | 7 901.5 | 20 896 | `0xbb8f2405b09378af` |

两条从探针就能读出的事实（**不作为结论，只用于确认实验可做**）：

1. **orchard n=2 的指纹 `0xa6bcd171d69e969a` 与 doc 24 的全部 80 次计时证明完全相同。**
   新的多 note fixture 在 k=1 时退化为原来的单 note 树，A/B 的证明输入与历史基准逐位一致。
   这是本实验与冻结版本之间的接口锚点。
2. 探针已经显示 A/B 比值随 n 下降（0.825 / 0.783 / 0.729 / 0.713），
   但单个样本不足以判定形状，§4 的判据与 §5 的样本量与此无关地预先固定。

---

## 4. 判据（测量前冻结）

主表每一格都是**同会话、同进程、逐对交错**的 A/B（`ORCHARD_F4A=0/1` 每对切换一次）。

### 4.1 门（G）

| # | 判据 | 不通过的后果 |
|---|---|---|
| **G1** | 每个 (pool, n) 的所有计时证明 **proof 字节相同、FNV 指纹 A/B 一致**；`orchard n=2` 的指纹必须为 `0xa6bcd171d69e969a` | 实验作废，收益数字不得引用 |
| **G2** | `orchard n=2` 的 A 臂 1P `prove_ms` 中位数 ∈ [3 400, 4 100]（doc 25 的 3 734.9 ±10%） | 说明本次与 doc 25 不可比，绝对毫秒不得并列 |
| **G3** | 每个 (pool, n) 的逐对残差 `prove − Σ(九阶段)` 中位数 < 该臂 `prove` 的 1% | 阶段切分在该 n 下失真，A4 不得使用 |

### 4.2 预测（A）

| # | 预测 | 判据 | 若失败的读法 |
|---|---|---|---|
| **A1** | 收益随 n **稳定或增长**：`ΔT(n)/n` 在 n=2,4,8 上的（极差 / 均值）**≤ 20%** | 实测 | 若 >20% 且单调下降 ⇒ 判为"规模不友好"，必须归因到固定开销或上下文限制 |
| **A2** | 收益位置不变：每个 (pool, n) 的 `Δquotient / Δprove ≥ 0.90` | 实测 | 若某 n 掉到 0.90 以下 ⇒ 说明该规模下有别的阶段被牵连，A1 的解释不成立 |
| **A3** | 比值单调不增：ironwood 的 `B/A` 中位数在 n=1→2→4→8 上非递增（容差 0.01） | 实测 | 若中途回升 ⇒ 摊销假设错误，memo 的固定成本没有被摊薄 |
| **A4** | memo 驻留**不随 n 变化**：每个池的 `memo_bytes` 在各 n 之间相对偏差 ≤ 5% | 实测 | 若随 n 增长 ⇒ 每个 action 的链被分配了独立槽位，跨 action 没有合并 |
| **A5** | memo 读取**随 n 线性**：`memo_reads / n` 的相对极差 ≤ 15% | 实测 | 若偏离 ⇒ 跨 action 的链不是同一批，收益的"重复度"随 n 变化 |
| **A6** | 分配次数的减少**随 n 线性**：`Δalloc_calls(n) / n` 的相对极差 ≤ 25% | 实测 | 若偏离 ⇒ `Val` 的就地折叠在不同 action 数下行为不同（doc 25 §11 的模型需要再修一次） |
| **A7** | 被消除的乘法**随 n 线性**：`structural_mul_eliminations(n) / n` 的相对极差 ≤ 15% | 实测 | 若偏离 ⇒ `intern_chains` 的 `occ ≥ 2` 判定在 n 增大时合并了更多链，收益曲线会超线性（不是坏事，但必须说明） |

### 4.3 具体数值预测（会话前先写下，便于事后对照）

以 1P、ironwood 为基准（doc 25 的 1487 是 orchard n=2 的实测值）：

| 量 | n=1 | n=2 | n=4 | n=8 |
|---|---:|---:|---:|---:|
| `ΔT/n`（ms/action） | ≈ 360 | ≈ 350 | ≈ 400 | ≈ 400 |
| `B/A` | ≈ 0.82 | ≈ 0.78 | ≈ 0.73 | ≈ 0.71 |
| `structural_mul_eliminations` | ≈ 744 | ≈ 1 487 | ≈ 2 974 | ≈ 5 948 |
| `chain_muls` | ≈ 870 | ≈ 1 740 | ≈ 3 480 | ≈ 6 960 |
| `slots` | ≈ 49 | ≈ 49 | ≈ 49 | ≈ 49 |
| `memo_bytes`（MiB/chunk，1P） | ≈ 6.125 | ≈ 6.125 | ≈ 6.125 | ≈ 6.125 |
| `memo_reads`（每 proof） | ≈ 358 | ≈ 716 | ≈ 1 432 | ≈ 2 864 |

`slots` 与 `memo_bytes` 恒定、`memo_reads` 线性，是本实验的核心形状假设：
**共享链的"种类"由电路决定、与 action 数无关；action 数只决定它们被读多少次。**
如果 A4/A5 都命中，这条假设就被三次独立测量同时支撑。

---

## 5. 采样与分区（方法与 doc 25 相同，不得合并）

| 批次 | 构建 | 用途 | 每 (pool,n) 样本 |
|---|---|---|---|
| **T（时间 + 阶段）** | 干净构建，`--features f4aimpl`，`ORCHARD_E2_TIMING=1`，**不设** `ORCHARD_F4A_STATS` | `prove_ms`、九阶段 | 1P 20 对、8P 20 对 |
| **C（结构计数）** | 同上，加 `ORCHARD_F4A_STATS=1` | `chains/slots/chain_muls/structural_mul_eliminations/memo_reads/h_fnv` | 2 对 |
| **M（内存与分配）** | 诊断构建，`--features f4aimpl,f4a2mem` + `halo2_proofs-0.4.0-f4a2-mem.patch` | `ws/private` 采样峰值、`memo_bytes`、`alloc_calls/bytes` | 3 对 |

三条硬规则：

1. **C 与 M 的数字绝不与 T 的毫秒并列。** 计数构建有 atomic 计数，诊断构建有全局计数分配器，
   二者都会污染时间（doc 25 §2.2）。
2. **1P 与 8P 分开统计。** 两者 chunk 数不同（4 vs 32），memo 的每 chunk 驻留差 8 倍。
3. **不与 doc 24/doc 25 的绝对毫秒比较。** 设备、rustc 版本（1.88.0 → 1.98.1）、
   绑定核设置都可能不同；只允许"同会话配对差值"，以及 §4.1 的 G2 那一条区间校验。

固定条件：`PostNu6_3`、同一 pk（K 不变）、`--mask 0x1`（1P）/ `--mask 0xFF`（8P）、
`RAYON_NUM_THREADS=1` / `8`、seed 7、同依赖与编译配置、A/B 在同一进程内交错。

---

## 6. 分析口径

主量：

```
ΔT(n)      = median over pairs of [ T_A(n) − T_B(n) ]
ΔT(n)/n    = ΔT(n) / n
ratio(n)   = median over pairs of [ T_B(n) / T_A(n) ]
```

- 差值用**逐对相减**后的中位数，不用"两个中位数相减"；
- 比值用逐对比值的中位数，配 10 000 次 bootstrap 的 95% CI；
- 方向一致性用符号检验（多少对 B<A）；
- `ΔT(n)/n` 的稳定性用（极差 / 均值）判定，不用回归斜率——n 只有三个点，斜率没有意义。

阶段：`Δstage(n) = median_pairs [ stage_A − stage_B ]`，并要求 `Σ Δstage` 与 `ΔT` 的差
在 1% 以内（G3）。

---

## 7. 本阶段回答什么、不回答什么

**能回答**：

- F4-A 在 Orchard V3 的 1–8 action 范围内，`ΔT(n)` 与 `ΔT(n)/n` 的形状；
- 这种形状是否与"电路决定链的种类、action 数决定读取次数"的机制解释一致；
- 资源代价（memo 驻留、分配）是否随 n 出现非线性。

**不能回答**：

- F4-A 是否是 Halo2 的通用优化。本实验只有一个电路、两个 flags 组合；
- 跨电路是否也能受益。需要另立一项：选一个同样由 selector compression 产生
  `q·∏(r_i − q)` 结构的 Halo2 电路，检查同一机制能否复用；
- n 更大（16、32）时的行为。本阶段只到 8，且 8 已使单次证明超过 10 s（1P）。

**结论措辞的上限**：即使 A1–A7 全过，只能说
"F4-A 在 Orchard V3 的 action 规模上具有稳定的每 action 收益"，**不能**说"通用"。

---

## 8. 停手条件

出现下列任一情况，停止测量并记录，不调参重试：

1. 任一 n 的 A/B proof 指纹不一致（G1 失败）——说明多 note fixture 或 n 改变了证明内容；
2. 某个 n 需要改变 `ProvingKey` 或电路版本才能构造——说明它不是同一实验，必须单独分组；
3. 1P 的某个 n 单对耗时超过 90 s——样本量降到 10 对并在结果里注明，不得悄悄减少。

---

## 9. 复现

```bash
cd experiments/transaction_e2e
git apply vendor/halo2_proofs-0.4.0-f4a.patch

cd harness
# T：时间与阶段（干净构建）
CARGO_TARGET_DIR=target/stage cargo build --release --offline --features f4aimpl --bin orchard-e2e
RAYON_NUM_THREADS=1 ./target/stage/release/orchard-e2e --mode f4ascale \
  --pool ironwood --actions 8 --reps 20 --mask 0x1

# C：结构计数（同一二进制，改环境变量）
ORCHARD_F4A_STATS=1 RAYON_NUM_THREADS=1 ./target/stage/release/orchard-e2e --mode f4ascale \
  --pool ironwood --actions 8 --reps 2 --mask 0x1

# M：内存与分配（诊断构建）
cd .. && git apply vendor/halo2_proofs-0.4.0-f4a2-mem.patch
cd harness && cargo build --release --offline --features f4aimpl,f4a2mem --bin orchard-e2e
ORCHARD_F4A2_MEM=1 RAYON_NUM_THREADS=1 ./target/release/orchard-e2e --mode f4a2mem \
  --pool ironwood --actions 8 --reps 3 --mask 0x1
```
