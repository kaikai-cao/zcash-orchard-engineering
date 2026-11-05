# 25 · Stage F4-A.2 · 资源代价与阶段归因（预注册，先冻结再测量）

上一阶段：[`24_f4a_implementation.md`](24_f4a_implementation.md)（F4-A 实施，`19e7d2b` 冻结）。
本文分两半：**§0–§8 是预注册**（在任何新代码、新数字之前写定并单独提交），
**§9 起是实测**（测量完成后追加，不改动预注册的判据）。

## 0. 一句话

F4-A 已经证明"少算了 1487 个整块乘、1P 证明快 22%"，但**它换来了多少内存与分配代价，一次都没有测过**。
本阶段只做三件事：**实测内存**、**实测分配**、**把收益拆到 Prover 的九个阶段**。
不新增优化，不改冻结版本。

## 1. 冻结对象与禁止事项

| 项 | 值 |
|---|---|
| 被审计的版本 | `19e7d2b`（F4-A 实施）+ 补丁 `vendor/halo2_proofs-0.4.0-f4a.patch`（`+593 / −44`） |
| 审计方式 | **只读插桩**：新增的诊断补丁不改变任何求值结果、不改变任何控制流 |
| 允许改的代码 | ① 新诊断补丁 `vendor/halo2_proofs-0.4.0-f4a2-mem.patch`；② `harness` 的两个新模式 |
| 禁止改的代码 | `evaluator.rs` 的求值逻辑、`intern_chains`、`Ast::Memo` 语义、任何 `plonk/*` 的阶段划分 |
| 禁止做的事 | 不推广成通用 CSE；不启动 H8；不启动 F4-B；不"顺手"修任何性能问题 |

审计期间若发现回归或代价超阈值，**只记录，不现场修**——是否修由下一轮决定。

## 2. 指标定义（冻结口径）

三个工作块各自有独立的单位与独立的构建，**任何一格数字都不许跨块换算**。

### 2.1 块一：内存（四个量，各有各的定义）

| 指标 | 定义 | 采集方式 | 分辨率 |
|---|---|---|---|
| `ws_peak_kernel_mib` | 进程生命周期内**内核维护**的工作集峰值 | `GetProcessMemoryInfo.PeakWorkingSetSize` | 页（4 KiB），但**单调不减** |
| `ws_sampled_peak_mib` | **证明窗口内**工作集的采样峰值 | 独立线程每 **1 ms** 读 `WorkingSetSize`，取窗口内最大值 | 1 ms × 变化速率 |
| `private_sampled_peak_mib` | **证明窗口内** `PrivateUsage`（提交的私有内存）的采样峰值 | 同上，读 `PROCESS_MEMORY_COUNTERS_EX.PrivateUsage` | 同上 |
| `memo_resident_bytes` | 本次证明里 memo 向量**实际分配**的字节（`Σ cap × size_of::<F>()`） | 在 `evaluate_inner` 填完 memo 后直接读 `Vec::capacity` | 精确，非估算 |
| `chunk_net_peak_bytes` | **单个 chunk 任务窗口内**的净峰值：窗口内 `max(本线程存活字节) − 窗口起点存活字节` | 计数分配器 + 线程局部计数器 + 在 chunk 闭包首尾打标记 | 每次分配即更新，精确到字节 |

两条必须写进结论的限定：

1. **`ws_peak_kernel_mib` 不能逐证明比较。** keygen 之后进程峰值已经是 GB 量级且单调不减，
   几 MiB 的增量落在分辨率以下（doc 24 §8.3 已经踩过一次：A/B 都读到 0.0 MiB）。
   本阶段它只作**上界交叉核对**，逐证明比较一律用 `ws_sampled_peak_mib`。
2. **`ws_sampled_peak_mib` 是采样值，不是峰值。** 1 ms 的采样间隔会漏掉短于它的尖峰。
   报告里的表述固定为"1 ms 采样峰值"，不写成"峰值"。`chunk_net_peak_bytes` 同理只在
   chunk 窗口内有效，跨线程的释放会让线程局部净值偏移 —— 这类样本单独计数并丢弃。

### 2.2 块二：分配（五个量）

| 指标 | 定义 | 来源 |
|---|---|---|
| `alloc_calls` / `alloc_bytes` / `dealloc_calls` | 每次证明窗口内的全局分配次数 / 请求字节 / 释放次数 | 全局计数分配器（`System` 外包一层原子计数） |
| `memo_inits` | memo 向量被创建的次数 | `f4a_impl::slot_fills`（每 chunk 49 次） |
| `memo_hits` | memo 被读取的次数 | `f4a_impl::memo_reads`（每 chunk 358 次） |
| `chunk_live_bytes` | 一个 chunk 求值期的存活数据规模（memo + 中间向量 + 输出） | 由 `chunk_net_peak_bytes` 与 `memo_resident_bytes` 给出 |

**计数构建与性能构建严格分离。** 计数版把 `System` 包一层，每次 malloc/free 多两次原子操作；
它测出来的时间**不进任何性能结论**，连配对比值都不用。性能结论只认 `f4aimpl` 那套干净构建（doc 24 §5）。

### 2.3 块三：阶段（九个量）

沿用 E2 的九个**首尾相接、互不嵌套**的阶段（`vendor/README.md` 的阶段表）：
`instance / synthesis / advice_commit / eval_setup / lookup_permute / permutation / lookup_product / quotient / opening`。
口径不变：墙钟秒、非重叠、可直接相加（阶段和与外层 `prove_ms` 的差要单独报，作为"未归因残差"）。

## 3. 预注册预测与判据

预测全部**基于 doc 24 已实测的结构常数**（`stats_1p.log` 与 `tree_crosscheck.log`），不是拍脑袋：

- 每 chunk 被移出树的节点 = 5 × 1726 = **8630**（`Constant → Scale(−1) → Add → Mul` 四层 + 一次叶子拷贝）
- 每 chunk memo 填充自身要花的分配 = 239 个链乘法 × 4 次分配 + 49 个 memo 向量 ≈ **1005**
- 每 chunk 净减少分配 ≈ **7625**；1P 四 chunk ≈ **30 500**；8P 32 chunk ≈ **244 000**
- 每次分配的大小 = `chunk_size × 32 B`（F = 256 bit）：1P 131 072 B、8P 16 384 B
- 因此**总字节数对 chunk 大小不变**：`Δ ≈ −8630 × 16384 × 32 B ≈ −4.52 GB`，扣掉填充代价 ≈ **−4.0 GB**，几何无关

| # | 预测 | 判据（PASS 条件） |
|---|---|---|
| **A1** | 收益 ≥ 90% 落在 `quotient` | `Δquotient / Δprove ≥ 0.90` |
| **A2** | `instance` / `synthesis` / `advice_commit` / `eval_setup` 不受影响 | 各自 `|B/A − 1| ≤ 1%`，或 CI 含 0 |
| **A3** | `opening` 不随多项式工作量变化 | `|B/A − 1| ≤ 2%`（与 doc 24 §5.3 同口径） |
| **A4** | 1P `Δalloc_calls ≈ −30 500 ± 15%`；8P `≈ −244 000 ± 15%` | 实测落在区间内 |
| **A5** | `Δalloc_bytes ≈ −4.0 GB ± 20%`，且 1P / 8P 之比 ∈ [0.8, 1.25] | 两条同时满足 |
| **A6** | memo 驻留 1P / 8P 都是 49 × `chunk_size` × 32 B（6.42 MB / 0.80 MB 每 chunk），8P 并发 8 chunk ≈ 6.4 MB | 实测与算式的偏差 ≤ 2%（容量应按 `with_capacity` 精确命中 `slots × chunk`） |
| **A7** | 1P / 8P 的采样峰值增量都是 **+6 ~ +7 MiB**，且 < 64 MiB（F4A-4 门槛） | 两个几何都满足 |

**Go / No-Go（审计本身）：**

- **G1 正确性**：80 次计时证明的 proof 字节与指纹、`h` 系数指纹必须与 doc 24 逐位相同。
  任一处不同 ⇒ 立即停，按"插桩改变了语义"处理。
- **G2 内存上限**：memo 驻留 ≤ 64 MiB，采样峰值增量 ≤ 64 MiB。
- **G3 归因闭合**：`Σ Δstage` 与 `Δprove` 的差 ≤ 2%（未归因残差）。
- **G4 账目闭合**：A4 与 A5 同时 PASS。

G1–G4 全过 ⇒ **F4-A 的资源证据补足，进入"可推广性验证"**；
G2 或 G4 不过 ⇒ F4-A **仍冻结**，但对外表述必须补上代价那一半，并把推广暂缓；
G1 不过 ⇒ 撤回本阶段全部结论。

## 4. 测量协议

| 项 | 设置 |
|---|---|
| 机器 / 系统 | 同一台，Windows，`--mask 0x1`（1P）/ `0xFF`（8P），进程内 pin |
| 配对 | **同一进程内 A/B 交错**（`ORCHARD_F4A=0/1` 逐对切换，每次证明重读环境变量），与 doc 24 §5 完全一致 |
| 内存 / 分配 | 1P、8P 各 **5 对**（诊断构建，带分配器计数） |
| 阶段 | 1P、8P 各 **20 对**（干净构建 + `ORCHARD_E2_TIMING=1`） |
| 统计 | 每对 `B/A` 比值的中位数 + bootstrap 95% CI（10 000 次重抽）+ 符号检验，与 doc 24 同一套代码（`f4a_ab.py` 的 `bootstrap_ci` / `sign_test`） |
| 样本量的理由 | 内存是结构量（由 `chunk_size` 与槽数决定），5 对足以看出 ±5% 的偏差；阶段是时间量，噪声大，沿用 20 对 |

## 5. 四条方法学边界（写进论文时必须保留）

1. **22% 只认本轮同会话配对。** `6939.6 → 5399.9 ms`、`B/A = 0.7800`、CI `[0.7676, 0.7862]`
   是 `19e7d2b` 那一次 20 对同进程交错的结果。任何重跑都另立数字，不与这一次混算。
2. **不拿本轮绝对值与历史会话比较。** 本轮 A 侧 1P 是 **6939.6 ms**，H6 冻结值是 **2781.4 ms**，
   相差 2.49 倍。两者**同几何**（PostNu6_3 / 2 Action / 1P），但**不同批次**：构建产物、
   机器状态、进程历史都不同，本阶段**不做归因**，也不允许写成"H6 之后又慢了一倍"或
   "F4-A 把 2781 变成 5399"。跨会话只能比**比值**，不能比**毫秒**。
3. **95% CI 保留原始配对口径。** 不因为"看起来太宽"就换成分位数区间或去掉极端对；
   doc 24 §5.1 那对相邻的 1.49 / 0.39 保留在样本里。
4. **1487 / 621 / 806 是三个不同层次的数，永不混用**：

| 数 | 名字 | 定义 | 能回答什么 |
|---:|---|---|---|
| **1487** | `structural_mul_eliminations` | 证明者**实际不再执行**的整块乘法数（每 chunk；2 Action 展开后） | 时间收益的来源 |
| **621** | 单实例普查可省数 | 对一个 `ConstraintSystem`（193 约束）数出来的可省乘数 | 结构普查的结论，不能配 22% |
| **806** | `product_equivalent_saved_work` | 把 621 按 product 单价归一化后的等价工作量 | 与 F4-B 等候选横向比价 |

## 6. 与本阶段无关的东西

- H8（常量物化，乐观上界 ~1%）：**不启动**。
- F4-B（old/new selector 共享，理论上界 0.92%）：**不启动**。
- 推广（不同 Action 数、其它 Halo2 电路、不同规模）：**等 G1–G4 全过之后再立项**，本阶段不碰。

## 7. 复现骨架（测量前写定）

```bash
cd experiments/transaction_e2e
git apply vendor/halo2_proofs-0.4.0-f4a.patch
git apply vendor/halo2_proofs-0.4.0-f4a2-mem.patch     # 本阶段新增，仅诊断

cd harness
# 块一 + 块二：诊断构建（分配器计数，时间不可用于性能结论）
cargo build --release --offline --features f4aimpl,f4a2mem --bin orchard-e2e
# 块三：干净构建（只有 E2 的 env-gated 计时）
cargo build --release --offline --features f4aimpl --bin orchard-e2e
```

## 8. 预注册时的已知不确定性

1. **`PrivateUsage` 含堆外预留**，与工作集不是一回事；两个都报，不合并成一个"内存"。
2. **分配器的计数包含 harness 自身的分配**（bundle、rng、proof 缓冲），所以 A5 只能比**差值**，
   不能把 `alloc_bytes` 总量当成"证明的内存"。
3. **1P 的 chunk 是串行的**，`chunk_net_peak_bytes` 就是进程增量的主项；8P 是并行的，
   进程增量 ≈ 8 × 单 chunk 峰值，两者要分别报，不能互相推。
4. **E2 计时本身有开销**（每阶段两次 `Instant::now()` + 一次 mutex），绝对时间会比 doc 24 略慢；
   块三只报**阶段间**的配对差值，不复述端到端百分比。
