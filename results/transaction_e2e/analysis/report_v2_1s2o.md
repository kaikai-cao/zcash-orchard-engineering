# Orchard 端到端交易成本剖析（V2 / 历史 Orchard 交易形状）

> 冻结时间：2026-11-03
> 上游：`zcash/orchard` @ `616a669df8c9a59c33d47064d3ce04b25d026b2e`（`0.16.0`）
> 工具链：rustc 1.88.0 / x86_64-pc-windows-msvc
> 机器：i7-14700（8 P-core + 12 E-core / 28 逻辑处理器），Windows 11

---

## 0. 一句话结论

在**库级**（`Builder::build` → `create_proof` → `apply_signatures`）的固定工作负载与合成夹具下，1 Spend + 2 Outputs 的 Orchard bundle，单线程 P 核证明生成 **3.202 s**，`create_proof` 占整个 Bundle 生命周期测量时间的 **98.72%**；`Builder` 构建（12.5 ms）与签名（0.59 ms）占比极低。

因此：**当前工作负载的主要性能优化对象是 Halo2 proving 路径。**

这句话不能外推成「真实钱包的交易构造、Witness 获取、序列化、广播都没有优化空间」——那些环节根本不在本实验的测量范围内，本实验也没有覆盖任何钱包实现。

---

## 1. 为什么这一版必须先讲清楚「不是完整交易」

`Builder::build()` **不生成证明**。它返回的是 `Bundle<InProgress<Unproven, Unauthorized>, i64>`，证明要显式调用 `Bundle::create_proof(&pk, rng)`，授权再调用 `apply_signatures(rng, sighash, &[ask])`。三段分开计时才有归因意义。

本报告覆盖的是 Orchard **bundle** 的完整生命周期，不含：

- 外层交易的 sighash 计算、v5/v6 交易编码与序列化（在 `zcash_primitives` / `zebra`，不在本 crate）；
- 透明输入输出、其他 Pool 的 Bundle；
- 网络广播与确认。

因此 `encoded_bytes` 是按协议布局**算出来**的，不是某个编码器的输出（见 §7，已与 `Proof::expected_proof_size` 交叉验证）。

---

## 2. 实验设置

| 维度 | 取值 |
|---|---|
| 电路版本 | `FixedPostNu6_2`（`BundleVersion::orchard_v2()`） |
| Bundle 类型 | `BundleType::DEFAULT`（最少填充到 2 个 Action） |
| 花费 | 1 个真实 Spend，note 位于叶子 0，认证路径全为空子树根 |
| 输出 | 2 个 Output，收款方是**另一个账户**（真实的跨地址转账，V2 允许） |
| 金额 | 输入 1.0 ZEC，输出 0.45 + 0.45 ZEC，fee 0.1 ZEC |
| 线程 | `RAYON_NUM_THREADS` ∈ {1,2,4,8,16,20}，掩码按物理 P 核进位 |
| 亲和性 | 进程内 `SetProcessAffinityMask`，启动即生效（早于任何 rayon 线程创建） |
| 编译 | `--release`，`orchard` path 依赖，`features = ["circuit","multicore"]` |
| 重复 | 每配置 3–5 次证明，每次都是「重建 bundle → 证明 → 签名 → 验证」完整一遍 |

**必须绑核**：同一台机器上不绑定时证明耗时 6.866 s，比绑 P 核慢 **2.15×**（与阶段 A 在官方 bench 上测到的 2.07× 一致）。不绑定的数字测的是 Windows 把后台计算压到 E-core 的调度策略，不是算法。

Harness 代码：`experiments/transaction_e2e/harness/`（独立 crate，通过 path 依赖接入上游，官方仓库保持 clean）。

---

## 3. 阶段分解（1S + 2O，2 Actions，单线程 P 核）

| 阶段 | 均值 | 占总耗时 |
|---|---:|---:|
| Fixture 构造（note / 路径 / anchor） | 9.0 ms | 0.28% |
| ProvingKey 生成（K=11） | 2 645 ms | — （冷启动一次性） |
| `Builder::build`（含 Action 构造） | 12.5 ms | 0.39% |
| **`create_proof`（Halo2）** | **3 202.4 ms** | **98.72%** |
| `apply_signatures`（binding + spend auth） | 0.59 ms | 0.018% |
| `verify_proof` | 29.5 ms | 0.91% |

热路径合计 3 244.6 ms，其中证明占 98.7%。

几个值得注意的量级关系：

- **签名几乎是免费的**（0.59 ms）。redpallas 签两个 Action 的开销，比证明低了 3 个数量级。
- **验证 29.5 ms，是证明的 1/109**。这是 Halo2 相对 Groth16 的结构性特征：证明重、验证轻，且验证不需要配对。
- **Keygen 2 645 ms 与一次证明同量级**。若某个进程在启动时必须从头生成 ProvingKey、且没有任何缓存或跨进程复用，它的首次证明成本是 5.85 s（keygen + prove）而不是 3.20 s。**这是「无缓存冷进程」的上界，不是任何具体钱包的首笔交易耗时**——实际取决于 Keygen 是否被预生成、是否复用、是否在线程池上并行（8 线程下 keygen 只要 1.36 s）。Sapling/Groth16 没有对应项，因为 CRS 是预生成的。

---

## 4. Action 数量扩展（单线程 P 核）

| Actions | Prove (s) | Verify (ms) | Build (ms) | Sign (ms) | Proof (B) | Bundle (B) | Peak WS (MiB) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | 3.202 | 29.5 | 12.5 | 0.59 | 7 264 | 9 009 | 114.8 |
| 4 | 5.756 | 36.3 | 19.3 | 1.10 | 11 808 | 15 193 | 157.2 |
| 8 | 10.948 | 51.6 | 32.2 | 2.04 | 20 896 | 27 561 | 242.0 |

最小二乘拟合：

$$
T_{\text{prove}}(n) \approx 0.606 + 1.292\,n \quad \text{(s)}
$$

**这个拟合有一个独立的验证点。** 阶段 A 在官方 `orchard_k11_prover` 上测的是 n=1、单线程、P 核、**不同的 fixture**（coinbase、无 Spend），1.929 s。把 n=1 代回上式得 1.898 s，差 1.6%。两条完全独立的代码路径（官方 bench vs 我们的 harness）、不同的交易形状，落在同一条直线上——说明这条线性关系是电路本身的，不是我们 harness 的。

推论：

- 每增加一个 Action，边际成本 **+1.29 s**；固定开销只有 **0.61 s**（共享的商多项式、承诺、IPA opening）。
- 常见的 2-Action 交易里，**81% 的证明时间是可归因到单个 Action 的**。这意味着任何「按 Action 摊销」的优化（Gadget 约束数、Sinsemilla 哈希、ECC 标量乘）都有 4/5 的杠杆；而**拟合截距项 0.61 s（19%）**最多只能碰这么多。

  注意：截距是「与 Action 数无关的那部分」的统计描述，**不等于 IPA 固定开销**。它可能包含 IPA opening、实例处理、证明系统的固定流程等多个成分，本实验没有把它们拆开。要拆需要源码级计时，属于阶段 E。

---

## 5. 线程扩展（1S + 2O）

掩码按**物理** P 核进位，不是简单的连续位：`0x1`(1) / `0x5`(2) / `0x55`(4) / `0x5555`(8) / `0xFFFF`(16，即 8 核 SMT) / `0xFFFFF`(20，含 4 个 E 核)。

| 线程 | 掩码 | Prove (s) | 加速比 | 并行效率 | Verify (ms) | Keygen (s) |
|---:|---|---:|---:|---:|---:|---:|
| 1 | 0x1 | 3.202 | 1.00× | 1.00 | 29.5 | 2.645 |
| 2 | 0x5 | 1.724 | 1.86× | 0.93 | 16.6 | 1.889 |
| 4 | 0x55 | 1.010 | 3.17× | 0.79 | 8.98 | 1.491 |
| 8 | 0x5555 | 0.699 | 4.58× | 0.57 | 6.04 | 1.361 |
| 16 | 0xFFFF | 0.713 | 4.49× | 0.28 | 6.89 | 1.298 |
| 20 | 0xFFFFF | 1.028 | 3.12× | 0.16 | 9.60 | 1.347 |

三点结论：

1. **8 线程是拐点，16 线程（SMT）零收益**。0.699 → 0.713 s。SMT 兄弟共享执行单元，对一个已经打满 FP/整数吞吐的负载没有帮助。
2. **20 线程明显变差，而且是不稳定的差**：三次测量是 0.696 / 0.671 / **1.717** s。掩码 `0xFFFFF` 里只有 16 个 P 逻辑核，剩下 4 个是 E 核，rayon 把 20 个任务均分下去，落在 E 核上的 4 个成了长尾，整个证明等它们。这是典型的 straggler，不是带宽问题（两次快的时候和 16 线程持平）。
3. **Keygen 的扩展性远差于 prove**：最多只有 2.03×（16 线程）。这符合预期——keygen 的 `keygen_vk`/`keygen_pk` 里有大量串行的多项式构造。

对后续实验的直接约束：**线程扩展实验的上限应设为 8（物理 P 核），超过 8 的数字没有解释价值，只会引入调度噪声。**

### 5.1 扩大负载能否改善扩展性？

8 Actions × 8 线程：2.629 s，相对单线程 10.948 s 是 **4.16×**，效率 0.52——**比 2 Action 的 0.57 还低**。

如果瓶颈是「每任务固定开销 + 同步」，负载越大效率应该越高；实际相反。

**但这是一个假设，不是结论。** 它可能指向内存带宽或共享缓存竞争，也可能来自 rayon 在更大工作集上的切分策略。关键是不能用「峰值工作集 242 MiB 超过 L3 容量」去证明缓存竞争——**工作集大小和竞争强度是两件不同的事**，工作集超容量只是竞争的必要条件之一，不是证据。验证需要硬件性能计数器（cache miss rate、内存带宽），并且要设计成可证伪的形式，属于阶段 E。

---

## 6. 内存

峰值工作集（`GetProcessMemoryInfo` 的 `PeakWorkingSetSize`）随 Action 数**精确线性**：

| Actions | Peak WS (MiB) |
|---:|---:|
| 2 | 114.8 |
| 4 | 157.2 |
| 8 | 242.0 |

拟合：`Peak ≈ 72.4 + 21.2·n` MiB，三个点全部落在 0.1 MiB 内。每个 Action 约 21 MiB，基础开销 72 MiB。

需要注意的两件事：

- 证明**前后**的工作集几乎不变（43 → 45 MiB）。峰值是 `create_proof` 内部瞬时分配的，用完就释放，所以「常驻内存」和「峰值内存」在这里差 2.5 倍。只报常驻会严重低估。
- 峰值随 n 线性，而证明时间也随 n 线性，但**两者斜率不同源**：如果内存是瓶颈，时间应该超线性。§5.1 的效率下降说明存在竞争，但不是硬性的容量墙。

---

## 7. 尺寸

| Actions | Proof (B) | Bundle 编码 (B) |
|---:|---:|---:|
| 2 | 7 264 | 9 009 |
| 4 | 11 808 | 15 193 |
| 8 | 20 896 | 27 561 |

- 证明大小 `= 2720 + 2272·n`，与 `Proof::expected_proof_size(n)` **逐字节相等**（该常量由 crate 用 `CircuitCost` 交叉校验过）。
- Bundle 编码按协议布局计算：每 Action `cv_net 32 + nf 32 + rk 32 + cmx 32 + epk 32 + enc_ct 580 + out_ct 80 = 820`；Bundle 级 `flags 1 + value_balance 8 + anchor 32 + proof + bsk_sig 64 = 105`。这是按规范算出来的字节数，**不是任何编码器的输出**——Zcash 的交易编码在 `zcash_primitives`，不在本 crate。

这里有三个不同的量，必须分开：

| 概念 | 值 | 来源 |
|---|---:|---|
| **证明载荷**（proof 字节） | 7 264 B | 实测 `proof.as_ref().len()`，与 `expected_proof_size` 一致 |
| **Bundle 编码长度** | 9 009 B | 按协议布局计算，**不是编码器输出** |
| **完整交易字节** | 未测 | 需要 v5/v6 交易编码 + 透明部分 + 其他 Pool 的 bundle |

能与 Sapling 比较的**只有第一项对第一项**：

- Sapling Groth16 的 proof 是常数 192 B/份，1 Spend + 2 Outputs 需要三份（1 个 Spend proof + 2 个 Output proof）= **576 B**。
- Orchard 2 Actions 的 proof 是 **7 264 B**，即 **12.6 倍**。

这是**证明载荷之比，不是交易大小之比**。Sapling 的 Spend / Output 描述本身还各带约 384 B / 948 B 的编码开销（cv、anchor、nullifier、rk、epk、密文、签名等），Orchard 的 Action 是 820 B——两边完整 bundle 的差距远小于 12.6 倍。本报告不做完整交易大小的比较。

12.6 倍的真正含义是：Halo2/IPA 用**证明体积**换掉了可信设置，同时把验证从"配对"变成了"内积论证"。这个权衡值得在最终报告里单独成节，但要用完整交易字节来支撑，不能只拿 proof 载荷说事。

---

## 8. 局限与未完成

1. **本报告只覆盖 V2（`FixedPostNu6_2`）**，即 NU6.3 之前的历史工作负载。NU6.3 的迁移形状（Orchard V3 的 1 Spend + 1 Change，加 Ironwood V3 的 1 Output，合计 3 Actions / 2 Bundles）已单独测量，见 [`report_nu63_migration.md`](report_nu63_migration.md)。
2. **fixture 是合成数据**：叶子 0、空兄弟路径。Merkle 路径长度固定 32，所以电路成本不受影响，但这不是从真实链状态导出的 witness。
3. **不含交易层**：sighash、v5/v6 序列化、fee 计算、广播。`encoded_bytes` 是算出来的，不是编码器输出的。
4. **未测** `InsecurePreNu6_2` 版本；未做 CPU 采样剖析（阶段 E）。
5. **单次运行的重复数偏少**（3–5 次）。证明耗时本身抖动很小（单线程 5 次：3.189–3.242 s，RSD 0.7%），够用；但 20 线程那个离群值说明多线程配置需要更多重复才能给置信区间。

---

## 9. 下一步

V2 的这条基线是本轮所有对比的参照。后续的 NU6.3 迁移实验已经在 [`report_nu63_migration.md`](report_nu63_migration.md) 完成：同样 3 个 Action，拆成两个 Bundle 后证明成本是 5.285 s，其中 +0.80 s 与 Action 数无关，纯粹来自"多起一次证明"。

端到端实验到此收束，转入 `Config::configure()`。

---

## 附：原始数据

- `results/transaction_e2e/raw/*.log` — 每次运行的完整 STAGE/RESULT 输出
- `results/transaction_e2e/analysis/e2e_runs.csv` — 机器可读汇总
- `scripts/benchmark/run_e2e.ps1` — 运行器（掩码 + 线程数）
- `scripts/analysis/collect_e2e.py` — 汇总
