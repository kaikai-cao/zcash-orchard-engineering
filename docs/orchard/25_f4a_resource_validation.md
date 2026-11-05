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

---

# 实测（预注册之后追加，预注册正文未改动）

## 9. 三个工作块的答案

产物在 `results/transaction_e2e/f4a2_audit/`：`mem_1p.log`、`mem_8p.log`、`stage_1p.log`、
`stage_8p.log`、`drift_check_1p_unpinned.log`、`build.txt`、`summary.md`。

### 9.0 一句话

F4-A 的资源代价是 **+6.1 MiB（1P）/ +6.7 MiB（8P）的采样峰值**、**每证明 24.5 MiB 的 memo 写入**，
换来 **每证明少 8 617 次分配、少 1.60 GiB 分配字节**；时间的收益 **100% 落在 `quotient`**，
其它八个阶段一格没动。

### 9.1 正确性（G1）

| 几何 | 计时证明数 | proof 字节 | proof 指纹 | 结论 |
|---|---:|---|---|---|
| 1P | 40 | 7264（唯一值） | `0xa6bcd171d69e969a`（唯一值） | **PASS** |
| 8P | 40 | 7264 | `0xa6bcd171d69e969a` | **PASS** |

内存/分配那两批（各 10 次证明）也全部是 7264 B / 同一指纹。**插桩没有改变语义。**

### 9.2 块一：内存（5 对，中位数）

**1P**

| 指标 MiB | A | B | Δ |
|---|---:|---:|---:|
| `ws_start`（证明起点） | 47.14 | 47.04 | −0.10 |
| **`ws_sampled_peak`（1 ms 采样峰值）** | **115.34** | **121.43** | **+6.09** |
| `ws_delta`（峰值 − 起点） | 68.45 | 74.36 | +5.91 |
| `private_sampled_peak` | 105.68 | 112.13 | +6.45 |
| `private_delta` | 69.14 | 74.29 | +5.15 |
| `ws_peak_kernel`（单调计数，仅上界） | 121.52 | 121.52 | **+0.00** |

**8P**

| 指标 MiB | A | B | Δ |
|---|---:|---:|---:|
| `ws_start` | 50.81 | 50.95 | +0.14 |
| **`ws_sampled_peak`** | **118.68** | **125.40** | **+6.72** |
| `ws_delta` | 67.45 | 74.68 | +7.23 |
| `private_sampled_peak` | 111.19 | 117.03 | +5.84 |
| `private_delta` | 70.13 | 74.62 | +4.49 |
| `ws_peak_kernel` | 125.04 | 125.40 | +0.36 |

三点必须一起读：

1. **`ws_peak_kernel` 差分不开**（1P 是 +0.00）。这不是"内存没变"，是内核的进程生命周期峰值
   在 keygen 之后就已经 121 MiB，比 F4-A 的 6 MiB 增量粗一个量级——doc 24 §8.3 的困境在这里被
   完整复现，也正是预注册要求改用采样峰值的理由。
2. **增量 ≈ memo 的驻留量**（6.42 MB = 6.12 MiB）。1P 四 chunk 串行，所以进程增量就是一个 chunk 的 memo；
   8P 八个 chunk 并行、每个 0.77 MiB，合计 6.1 MiB，量级相同（+6.72 略高，含 rayon 任务栈与采样抖动）。
3. **private 增量与工作集增量同量级**，说明这 6 MiB 是真实提交的私有内存，不是被映射出去的文件页。

### 9.3 块二：分配（5 对，中位数）

| 指标 | 1P A | 1P B | 1P Δ | 8P A | 8P B | 8P Δ |
|---|---:|---:|---:|---:|---:|---:|
| `alloc_calls` / 证明 | 96 596 | 87 979 | **−8 617**（−8.9%） | 301 104 | 200 592 | **−100 512**（−33.4%） |
| `alloc_bytes` / 证明 | 3.599 GiB | 1.996 GiB | **−1.603 GiB**（−44.5%） | 3.599 GiB | 1.996 GiB | **−1.603 GiB** |
| `dealloc_calls` | 96 596 | 87 979 | −8 617 | 301 104 | 200 592 | −100 512 |
| `memo_bytes` / 证明 | 0 | 24.50 MiB | +24.50 | 0 | 24.50 MiB | +24.50 |
| `memo_bytes_max_chunk` | 0 | **6.12 MiB** | | 0 | **0.77 MiB** | |
| `memo_slots` / 证明 | 0 | 196（= 49 × 4） | | 0 | 1 568（= 49 × 32） | |
| `chunk_peak_max` | 1.25 MiB | 7.38 MiB | +6.13 | 0.16 MiB | 0.92 MiB | +0.77 |
| `chunk_peak_mean` | 0.16 MiB | 0.63 MiB | +0.47 | 0.02 MiB | 0.08 MiB | +0.06 |
| `chunk_allocs_mean` | 561 | 308 | −253 | 554 | 301 | −253 |
| `chunk_allocs_max`（最重一个 chunk） | 7 161 | 3 879 | −3 282 | 7 071 | 3 789 | −3 282 |
| `chunk_count` / 证明 | 52 | 52 | 0 | 416 | 416 | 0 |
| `chunk_anomalies` | 0 | 0 | 0 | 0 | 0 | 0 |

四个可验证的关系：

- **`alloc_calls = dealloc_calls`**，A/B 与两个几何都逐位相等 ⇒ 计数器没有漏记（每证明窗口内
  没有跨窗口的残留分配）。字节差 4 000 B（1P）/ 4 000 B（8P）是进程级常驻，不是泄漏。
- **`memo_bytes_max_chunk` 与算式逐位相等**：1P `49 × 4096 × 32 = 6 422 528 B = 6.125 MiB`；
  8P `49 × 512 × 32 = 802 816 B = 0.766 MiB`。偏差 0.0%（A6 PASS）。
- **`chunk_peak_max − chunk_peak_max(A) = memo_bytes_max_chunk`**：1P 7.38 − 1.25 = 6.13 vs 6.12；
  8P 0.92 − 0.16 = 0.77 vs 0.77。**memo 的代价就是它自己的驻留量，没有额外放大。**
- **`Δalloc_bytes` 对 chunk 大小不变**：1P 与 8P 都是 −1.603 GiB（比值 1.000）。这条是预注册
  算出来的结构不变量（总字节只取决于 `poly_len`，不取决于切多大），实测**逐位命中**。

`chunk_anomalies = 0` 说明没有一个窗口出现"跨线程释放把线程局部净值拉低"的情况，
§2.1 里预留的丢弃规则一次都没触发。

### 9.4 块三：阶段（20 对，中位数）

**1P**

| 阶段 | A | B | Δ | 95% CI（Δ） | B/A | 占 Δprove |
|---|---:|---:|---:|---:|---:|---:|
| `instance` | 14.1 | 14.4 | +0.1 | [−0.6, +1.2] | 1.0089 | −0.0% |
| `synthesis` | 145.7 | 146.6 | +1.6 | [−0.1, +4.5] | 1.0112 | −0.2% |
| `advice_commit` | 404.7 | 407.3 | +1.0 | [−4.1, +7.6] | 1.0025 | −0.1% |
| `eval_setup` | 10.9 | 11.0 | +0.3 | [−0.9, +0.9] | 1.0235 | −0.0% |
| `lookup_permute` | 252.9 | 254.8 | −0.1 | [−2.5, +5.2] | 0.9996 | +0.0% |
| `permutation` | 126.3 | 126.4 | +0.0 | [−3.0, +1.9] | 1.0000 | −0.0% |
| `lookup_product` | 162.7 | 164.1 | +1.5 | [−1.0, +4.8] | 1.0097 | −0.2% |
| **`quotient`** | **2 089.0** | **1 310.2** | **−790.9** | **[−815.3, −768.2]** | **0.6222** | **+100.4%** |
| `opening` | 516.7 | 514.1 | +0.7 | [−4.5, +5.6] | 1.0014 | −0.1% |
| 九阶段合计 | 3 731.0 | 2 963.4 | −767.6 | | | |
| `prove_ms` | 3 734.9 | 2 966.8 | **−787.4** | [−807.9, −748.2] | 0.7944 | 100% |
| 未归因残差 | 3.0 | 3.3 | +0.3 | | | |

**8P**

| 阶段 | A | B | Δ | 95% CI（Δ） | B/A |
|---|---:|---:|---:|---:|---:|
| `instance` | 5.5 | 5.4 | −0.1 | [−0.3, +0.4] | 0.9837 |
| `synthesis` | 147.4 | 148.6 | +1.4 | [−1.3, +5.4] | 1.0096 |
| `advice_commit` | 112.3 | 111.8 | +0.9 | [−0.5, +1.9] | 1.0084 |
| `eval_setup` | 10.8 | 9.9 | **−1.0** | **[−1.3, −0.6]** | 0.9138 |
| `lookup_permute` | 79.6 | 78.9 | −0.5 | [−3.3, +0.4] | 0.9934 |
| `permutation` | 40.5 | 39.8 | −0.5 | [−2.3, +0.7] | 0.9872 |
| `lookup_product` | 47.5 | 47.3 | −0.5 | [−2.5, +0.9] | 0.9889 |
| **`quotient`** | **551.0** | **338.0** | **−213.1** | **[−220.4, −203.6]** | **0.6117** |
| `opening` | 119.0 | 118.6 | +0.3 | [−0.7, +1.5] | 1.0029 |
| 九阶段合计 | 1 114.1 | 902.4 | −213.8 | | |
| `prove_ms` | 1 116.7 | 908.2 | **−210.7** | [−222.3, −200.7] | 0.8117 |
| 未归因残差 | 2.9 | 3.3 | +0.4 | | |

**问题的答案**：`Synthesis` 不变、`Advice commitments` 不变、`Lookup / Permutation` 无回归、
`Opening` 不随多项式工作量变化、**收益几乎全部在 `Quotient`**（1P 100.4%，8P 101.2%，
多出来的那 0.4%/1.2% 是其它阶段合计 +9 ms 的反向噪声）。

**唯一一个"字面异常"**：8P 的 `eval_setup` 从 10.8 → 9.9 ms（−8.6%，CI 不含 0）。它是**变快**，
不是回归，绝对量 0.9 ms（占 8P prove 的 0.08%）。E2 的这个阶段只做 `new_evaluator` 与
`register_poly`（把 15 个固定列、若干 advice/instance coset 注册进去），A/B 之间没有任何代码差异，
因此这 0.9 ms 只能是**分配器状态差异**（B 少分配 1.6 GiB，堆的碎片与页状态不同）带来的副作用，
按"机制上不可能是 F4-A 的直接效果"记录，不当作收益。

**关于表中"九阶段合计"与 `prove_ms` 的 4 ms 差**：这是**按对求**的残差（每对 `prove − Σstage`），
A/B 都是 3 ms 量级，占各自 prove 的 0.08–0.3%，且两臂一致 ⇒ 阶段划分没有在 F4-A 之后漏掉一段时间。
注意不要用"中位数之和减中位数"去算这个差：那样会得到 19.8 ms（2.5%），那是中位数不可加的假象，
逐对相减后 `Δ(Σstage) = −787.5 ms` 与 `Δprove = −787.4 ms` 只差 **0.1 ms**（8P 差 3.1 ms）。

## 10. 判据逐条

| # | 判据 | 实测 | 结论 |
|---|---|---|---|
| **A1** | `Δquotient / Δprove ≥ 0.90` | 1P 100.4% / 8P 101.2% | **PASS** |
| **A2** | 非 quotient 阶段 `|B/A−1| ≤ 1%` 或 CI 含 0 | 1P 全部满足（最大 2.35%，CI 含 0，绝对 0.3 ms）；8P 七个满足，`eval_setup` 0.914、CI [−1.3,−0.6] 不含 0 | **1P PASS / 8P 一格 FAIL**（变快，0.9 ms） |
| **A3** | `opening` 变化 ≤ 2% | 1P 0.14% / 8P 0.29% | **PASS** |
| **A4** | 1P ≈ −30 500、8P ≈ −244 000（±15%） | −8 617（28%）/ −100 512（41%） | **FAIL** |
| **A5** | Δbytes ≈ −4.0 GiB（±20%），两几何比 ∈ [0.8, 1.25] | −1.603 GiB（40%）；比值 **1.000** | **量级 FAIL / 不变量 PASS** |
| **A6** | memo 驻留 = 49 × chunk × 32 B（≤2%） | 6 422 528 B / 802 816 B，偏差 0.0% | **PASS** |
| **A7** | 峰值增量 +6~+7 MiB 且 < 64 MiB | +5.9~+7.2 MiB（四组读数） | **PASS** |
| **G1** | proof 字节 / 指纹逐位不变 | 80 次证明全同 | **PASS** |
| **G2** | memo ≤ 64 MiB、峰值增量 ≤ 64 MiB | 6.12 MiB/chunk、+7.2 MiB | **PASS** |
| **G3** | `Σ Δstage` 与 `Δprove` 差 ≤ 2% | 1P 0.1 ms（0.01%）/ 8P 3.1 ms（1.5%） | **PASS** |
| **G4** | A4 与 A5 同时 PASS | 两者都 FAIL | **FAIL** |

按预注册的规则：**G4 不过 ⇒ F4-A 保持冻结、对外表述补上代价那一半、推广暂缓**。
这不是"F4-A 有问题"，是**我的分配模型错了**（下一节），预注册不改，结论照判据走。

## 11. 为什么 A4 / A5 高估了 2.5–3.5 倍：模型数的是节点，实测数的是分配点

预注册的模型是：每消掉一个链乘法 = 5 次分配（`Constant` / `Scale` / `Add` / `Mul` 四层各一次 + 一次叶子拷贝），
于是每 chunk `5 × 1726 = 8 630`、1P 四 chunk 34 520。

实测是每 proof **−8 617**（1P，为预测 34 520 的 **25%**）、**−100 512**（8P，为预测 244 000 的 **41%**）。

原因是 **`Val` 的就地折叠**：F4-A 的补丁把 `recurse` 改成返回 `Val::{Owned, Borrowed}`，
`Add` / `Mul` / `Scale` 在左侧是 `Owned` 时**直接改那块缓冲区**，不再 `collect()` 一块新的。
所以一个恢复链因子的四层里，只有

| 节点 | A 臂实际分配次数 | 说明 |
|---|---:|---|
| `ConstantTerm(r)` → `vec![r; n]` | 1 | 必须新建 |
| `Scale(q, −1)` | 0 | `q` 是 `Owned`，就地取负 |
| `Add(c, −q)` | 0 | 左侧 `Owned`，就地累加 |
| `Mul(acc, c−q)` | 0 | 同上 |
| 叶子 `Poly` 物化 | 0–1 | 无旋转时可借、有旋转时新建 |

即 **≈1.8 次分配 / 个被消掉的因子**，不是 5 次。乘回 `1726 × 4 = 6 904` 得 ≈12 400，再扣掉
memo 填充自己新增的 `239 × 4 + 49 × 4 ≈ 1 152` 次，落在 **≈11 250**；实测 8 617 略低，
差在"最重 chunk 的 −3 282 次"这一格上——那个 chunk 的叶子大多不需要物化。

**关键的一点**：这个 1.8 不是 F4-A 才有的，**A 臂也已经在享受它就地折叠**——因为 `Val` 与
`sharing` 在同一个补丁里，A/B 只差 `ORCHARD_F4A` 这个开关。所以预注册里那条"5 次分配"的算式
描述的是**上游 0.4.0 的求值器**，不是这次 A/B 的基线。**同一条补丁同时带来了"少算"和"少分配"，
A/B 只能测出"少算"这一半**——这句话必须写进论文，否则会把 1.6 GiB 的分配减少错误归因给共享机制。

字节那一侧可以按同一个口径读：`1.603 GiB ÷ (1726 × 4) ≈ 249 KB ≈ 1.9 × 131 072 B`，
即每消掉一个因子少申请约两块 chunk 缓冲区（常量 + 叶子），而不是预注册假设的 5 块。

## 12. 顺带测出来的一件事：doc 24 的绝对时间有一半来自"没绑核"

预注册 §5 边界 2 只要求"不比较"，不要求解释。但审计过程中出现了可解释的机会，于是补了一次**同二进制**的对照：

| 条件（同二进制、同 seed、1P、`RAYON_NUM_THREADS=1`） | A 臂 prove ms | B 臂 prove ms |
|---|---|---|
| `--mask 0x1`（本轮审计） | 3 734.9（20 对中位） | 2 966.8 |
| `--mask 0`（不绑核，3 对） | 7 324 / 6 479 / 7 392 | 5 132 / 5 770 / 5 146 |

**不绑核时 A 臂 ≈ 7 s，绑 0x1 后 ≈ 3.7 s，差 1.96 倍。** doc 24 的 `F4A_META` 里记的是
`"affinity":"0x0"`——那两批 20 对是**不绑核**跑的。所以：

- `6939.6 ms` vs `3734.9 ms` 的 1.86 倍差，**主因是绑定核，不是 F4-A、也不是机器变慢**；
- 比值仍然稳：不绑核 0.70（3 对，噪声大）、绑核 0.7944（20 对）——**方向与量级一致**，
  所以 doc 24 的 −22.00% 与 8P −15.27% 作为**比值**仍然成立；
- 但 doc 24 的**绝对毫秒**从今天起只能当"那一批、未绑核"的历史记录读，不能与任何绑核实验并列。
- 额外一条：doc 24 的 `build.txt` 记的是 rustc **1.88.0**，本阶段是 **1.98.1**，二进制不可逐字节对齐。
  这也是"跨会话不比毫秒"的又一条硬理由。

## 13. 结论

1. **F4-A 的资源代价很小且可解释**：+6.1 MiB（1P）/ +6.7 MiB（8P）采样峰值，其中 memo 本体 6.12 MiB/chunk；
   每证明向堆申请的次数少 8.6%（1P）/ 33.4%（8P），字节少 44.5%；memo 驻留与算式**逐位相等**，
   `chunk_peak` 的增量恰好等于 memo 驻留量。**门槛 64 MiB 有 9 倍余量。**
2. **收益的位置是干净的**：`quotient` 一个阶段承担 100.4%（1P）/ 101.2%（8P），其余八个阶段
   全部在噪声内（唯一一格 0.9 ms 的 `eval_setup` 是变快，且机制上不可能是 F4-A 的直接效果）。
   ⇒ **F4-A 没有"顺手加速"别的东西，也没有把工作推给别人。**
3. **两个预注册预测失败，失败是可解释的、且已经解释**：A4/A5 高估 2.4–3.6 倍，因为模型按"节点数"
   估分配，而补丁的就地折叠把四个分配点减成一个半。**按预注册，G4 FAIL ⇒ 推广暂缓。**
4. **下一步（需另行立项，本阶段不做）**：把修正后的分配模型（≈1.8 次/因子、字节对 chunk 不变）
   写成新的预注册，再决定是推广到其它 Halo2 电路，还是先用"不同 Action 数"做一次适用边界测试。
   在那之前不动冻结版本、不开 H8、不开 F4-B。

## 14. 复现

```bash
cd experiments/transaction_e2e
git apply vendor/halo2_proofs-0.4.0-f4a.patch
git apply vendor/halo2_proofs-0.4.0-f4a2-mem.patch        # 诊断补丁，仅测量

cd harness
# 块一 + 块二
cargo build --release --offline --features f4aimpl,f4a2mem --bin orchard-e2e
ORCHARD_F4A2_MEM=1 RAYON_NUM_THREADS=1 ./target/release/orchard-e2e --mode f4a2mem --reps 5 --mask 0x1 \
  > ../../../results/transaction_e2e/f4a2_audit/mem_1p.log
ORCHARD_F4A2_MEM=1 RAYON_NUM_THREADS=8 ./target/release/orchard-e2e --mode f4a2mem --reps 5 --mask 0xFF \
  > ../../../results/transaction_e2e/f4a2_audit/mem_8p.log

# 块三：先把诊断补丁摘掉，构建"没有分配器计数"的干净二进制
cd .. && git apply -R vendor/halo2_proofs-0.4.0-f4a2-mem.patch
cd harness && CARGO_TARGET_DIR=target/stage cargo build --release --offline --features f4aimpl --bin orchard-e2e
cd .. && git apply vendor/halo2_proofs-0.4.0-f4a2-mem.patch
cd harness
RAYON_NUM_THREADS=1 ./target/stage/release/orchard-e2e --mode f4a2stage --reps 20 --mask 0x1 \
  > ../../../results/transaction_e2e/f4a2_audit/stage_1p.log
RAYON_NUM_THREADS=8 ./target/stage/release/orchard-e2e --mode f4a2stage --reps 20 --mask 0xFF \
  > ../../../results/transaction_e2e/f4a2_audit/stage_8p.log
RAYON_NUM_THREADS=1 ./target/stage/release/orchard-e2e --mode f4a2stage --reps 3 --mask 0 \
  > ../../../results/transaction_e2e/f4a2_audit/drift_check_1p_unpinned.log

cd ../../..
python scripts/analysis/f4a2_report.py        # → results/transaction_e2e/f4a2_audit/summary.md

# 还原
cd experiments/transaction_e2e/vendor/halo2_proofs-0.4.0
git checkout -- src/f2.rs src/lib.rs src/plonk/prover.rs src/plonk/vanishing/prover.rs \
                src/poly/evaluator.rs
rm -f src/f4a_impl.rs src/f4a2.rs
python ../../../scripts/analysis/f0_verify_frozen.py     # 期望 RESULT: PASS
```
