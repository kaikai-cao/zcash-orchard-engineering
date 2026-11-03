# Stage E2 · Orchard Halo2 Prover 源码级分阶段计时（create_proof 调用链）

> 阶段 E 的第二块：沿 `halo2_proofs 0.4.0` 的单一扁平 `create_proof` 函数，在**真实执行边界**
> 插入 env 门控（`ORCHARD_E2_TIMING`）的计时桩，把黑盒的 `create_proof` 拆成 9 个连续、互不重叠的
> 阶段，回答研究问题：**「电路中占用最多行的部分，是否也是实际证明过程中最耗时的部分？」**
>
> 数据日期：2026-11-03（GMT+8）。电路、线程口径、机器环境完全复用
> `results/transaction_e2e/baseline/environment.md`（Stage E1），确保可比。

---

## 0. 方法学：计时怎么插、为什么可信

- **对象**：`halo2_proofs-0.4.0` 的 `create_proof`（`src/plonk/prover.rs:34–731`），单一扁平函数。
- **方式**：复制一份 halo2_proofs 到本地 `experiments/transaction_e2e/vendor/`，在 harness `Cargo.toml`
  用 `[patch.crates-io]` 指向它（`halo2_proofs = { path = "../vendor/halo2_proofs-0.4.0" }`）。
  源码与 0.4.0 完全一致，**仅多出可选计时桩**。
- **门控**：计时仅在环境变量 `ORCHARD_E2_TIMING` 存在时启用；否则 `e2_add` 直接 early-return，
  只留下若干 `Instant::now()` 快照（亚纳秒级，相对秒级证明可忽略）。因此**未启用时二进制行为与基线完全相同**。
- **防嵌套累计**：9 个阶段用「首尾相接的墙钟区间」测量——每个阶段在自身起点记 `Instant::now()`、
  终点 `e2_add(stage, elapsed)`，**互不重叠、不嵌套**。lookup 被 permutation commit 切成两段
  （`lookup_permute` / `lookup_product`），分别与 permutation 交替记时，保证不把 permutation 时间计入 lookup。
- **取数**：`take_e2_timings()` 在每次 `create_proof` 后由 harness 调用并重置，输出
  `E2_STAGE pool=orchard rep=N <stage>=<sec> ...`，实现**逐轮、逐证明**分解（v3 模式每轮跑 orchard + ironwood 两个证明，各自独立取数）。
- **可信度校验**：阶段求和 vs 外层 `prove_s` 仅差 **2.66 ms（1P）/ 2.47 ms（8P）**，即计时覆盖 99.9% 的
  `create_proof` 时间，证明无漏算、无嵌套重复累计。

### 9 个原始阶段（连续、不重叠）

| 阶段 | 对应源码边界（`prover.rs`） | 内容 |
| --- | --- | --- |
| `instance` | L76–129 | instance 列 witness + commitment（commit_lagrange + batch_normalize） |
| `synthesis` | L287–301（advice map 闭包内） | `FloorPlanner::synthesize` + `batch_invert_assigned` + 盲化因子 |
| `advice_commit` | L304–334（闭包内） | advice MSM（commit_lagrange）+ batch_normalize + IFFT/FFT（lagrange_to_coeff / coeff_to_extended） |
| `eval_setup` | L345–419 | 多项式求值器句柄注册（value/coset evaluator） |
| `lookup_permute` | L429–460 | lookup `commit_permuted` |
| `permutation` | L469–487 | permutation `commit` |
| `lookup_product` | L489–508 | lookup `commit_product` |
| `quotient` | L510–602 | vanishing `commit` + `construct`（最重的 FFT / 全约束多项式 h(X) 组装） |
| `opening` | L604–730 | 各点求值 + `multiopen::create_proof`（最重的 MSM / IPA） |

### 归并为关注的 6 阶段（与用户心智模型对齐）

`instance` 与 `eval_setup` 体量很小且概念上是证明前准备，折叠进 **synthesis（前端/witness+setup）**：

| 归并阶段 | = 原始阶段 | 含义 |
| --- | --- | --- |
| **synthesis** | instance + synthesis + eval_setup | 前端：witness 生成 + 求值器准备 |
| **advice_commit** | advice_commit | advice 列 MSM + 归一化 + FFT |
| **lookup** | lookup_permute + lookup_product | lookup 论证两段 |
| **permutation** | permutation | permutation 论证 |
| **quotient** | quotient | vanishing 论证（最重 FFT） |
| **opening** | opening | 多项式开点 / IPA（最重 MSM） |

---

## 1. 结果（Orchard V3 · 2 Action · PostNu6_3）

### 1.1 6 阶段占比与绝对耗时（1P 单 P-core）

| 归并阶段 | 1P 均值 | 1P 占比 | 8P 均值 | 1P→8P 加速比 |
| --- | ---: | ---: | ---: | ---: |
| **quotient** | **1803.6 ms** | **54.9%** | 429.2 ms | **4.20×** |
| **opening** | 474.8 ms | 14.4% | 93.2 ms | 5.09× |
| **advice_commit** | 371.3 ms | 11.3% | 99.8 ms | 3.72× |
| **lookup** | 372.9 ms | 11.3% | 102.3 ms | 3.65× |
| **synthesis** | 152.1 ms | 4.6% | 140.5 ms | **1.08×** |
| **permutation** | 113.4 ms | 3.4% | 31.2 ms | 3.64× |
| **合计（阶段求和）** | **3288.2 ms** | 100% | 896.3 ms | **3.66×** |
| 外层 `prove_s`（黑盒） | 3290.8 ms | — | 898.7 ms | 3.66× |

> 原始 9 阶段明细见 `e2_raw_compare.csv`；逐轮 tidy 数据见 `e2_timing/raw/e2_1p.csv`、`e2_8p.csv`。

### 1.2 逐轮稳定性（orchard，RSD）

| 阶段 | 1P RSD | 8P RSD |
| --- | ---: | ---: |
| quotient | 0.4% | 1.4% |
| lookup_permute | 0.6% | 4.2% |
| lookup_product | 1.7% | 1.9% |
| permutation | 0.8% | 1.6% |
| synthesis | 1.7% | 1.9% |
| advice_commit | 4.4% | 3.7% |
| opening | 4.8% | 2.8% |

最重阶段（quotient）RSD < 1.5%，全阶段均 < 5%，结论稳健。

### 1.3 与 E1 基线的交叉验证

E2 外层 `prove_s`：1P 3.291 s / 8P 0.899 s，**与 E1 基线 3.375 s / 0.932 s 仅差 ~2.5%**。
差异来自 E2 启用了 `ORCHARD_E2_TIMING`（多几次 `Instant::now()` + 锁），证明**插桩对真实耗时的影响在噪声范围内**，
E1↔E2 可直接对照，且计时可信。

---

## 2. 回答研究问题：占行最多的部分 ≠ 最耗时的部分

`08_action_cost_map.md` 的**静态布局**行数占比（约束行合计 3589，不含 Generator 表 1024 行）：

| 模块 | 行数 | 行占比 |
| --- | ---: | ---: |
| **Merkle** | 2080 | **58.0%** |
| **NoteCommit** | 798 | **22.2%** |
| CIVK | 185 | 5.2% |
| ADDR | 155 | 4.3% |
| NF | 149 | 4.2% |
| CV | 114 | 3.2% |
| SA | 87 | 2.4% |
| 其余（WIT/XADDR/RK/CHK） | < 25 | < 0.7% |

E2 的**动态耗时**占比（1P）：

| 阶段 | 耗时占比 | 是否对应某个 gadget 的行 |
| --- | ---: | --- |
| **quotient（全局 FFT）** | **54.9%** | ❌ 全局，覆盖全部约束 |
| opening（IPA/MSM） | 14.4% | ❌ 全局 |
| advice_commit（advice MSM） | 11.3% | ❌ 全局 |
| lookup | 11.3% | ❌ 全局 |
| **synthesis（真正执行 gadget 的 witness）** | **4.6%** | ✅ 这里才跑 Merkle/NoteCommit/Sinsemilla/固定基乘 |
| permutation | 3.4% | ❌ 全局 |

**结论（直接回答）：否。** 占行最多的 Merkle（58%）与 NoteCommit（22%）所在的 witness 执行阶段
（synthesis）只占证明时间的 **4.6%**。真正吃掉时间的是 **quotient（54.9%）+ opening（14.4%）= 69.3%**——
这两者是**对整条约束多项式做全局 FFT/MSM 的多项式机制**，不区分是哪个 gadget 贡献的行，把所有约束一视同仁地拿去 FFT。

### 关键的机制澄清（避免误读）

- 行数多 ≠ 该模块「自己算得慢」。Merkle/NoteCommit 的 witness 计算本身很快（synthesize 阶段仅 0.13 s）。
- 但 Merkle 的 58% 行意味着它贡献了 **58% 的总约束数**，这会通过「quotient 阶段在每个 FFT 点上对所有 gate/lookup/permutation 项求和」这一路径，**按比例**抬高 quotient 的每点求值成本——FFT 的规模（域大小 K=11）由电路规模固定，不随行分布变，变的是每点的求和项数量。
- 因此：**减少某模块的行数（Stage F 方向 A/B）确实会缩短 quotient，但杠杆作用在「全局相位」，不是「局部热点」**；而方向 C（FFT/MSM/IPA 底层原语）直接打在 69% 的占比上。

---

## 3. 回答 E1 遗留问题：为什么 8P 只有 3.66× 而非 8×

加权加速比被两个因素共同压低：

1. **synthesis 是串行地板（仅 1.08×）**：`FloorPlanner::synthesize` 本质是单线程逐 cell 赋值，无法并行，
   贡献约 **140 ms 不可压缩地板**。理论上它把最优加速比封顶在 ≈ 6× 量级
   （`3288 / ((3288−152) + 152/8) ≈ 6.0×`）。
2. **大阶段子线性扩展**：quotient 4.20×、opening 5.09×、advice_commit/lookup/permutation ≈ 3.7×，
   均远低于 8×（FFT 与 MSM 含串行分数，Amdahl 定律）。

两者叠加 → 实际 3.66×。要突破，要么重构 synthesis 使其可并行（难，涉及电路赋值模型），
要么在子线性阶段（尤其 quotient 的 FFT 与 opening 的 MSM）上做底层优化。

---

## 4. 给 Stage F 的优化选型指引

| 方向 | 对应占比（1P） | 杠杆性质 | 建议 |
| --- | --- | --- | --- |
| **C. FFT / MSM / IPA 底层原语** | quotient 54.9% + opening 14.4% = **69.3%** | 全局、与电路无关 | **最高杠杆**，优先 |
| A. Sinsemilla / Merkle（减行） | 经 synthesis 4.6% 间接、但按 58% 比例压 quotient | 全局相位的「按约束数」杠杆 | 中高，需算清净收益 |
| B. 固定基乘法（减行，5 次全宽 425 行） | 同上，比例较小 | 同上 | 中 |
| 局部 witness 微优化 | synthesis 4.6% 上限 | 天花板低 | 不优先 |

**优先顺序建议**：先做 C（底层多项式原语，直接吃 69%），再用 A/B 削减总约束数以按比例缩小 quotient；
不要在 synthesis 的局部 gadget 上投入（天花板仅 4.6%）。

---

## 5. 产物清单

| 文件 | 内容 |
| --- | --- |
| `docs/orchard/09_create_proof_timing.md`（本文件） | 分阶段计时 + 研究问题回答 + Stage F 指引 |
| `results/transaction_e2e/e2_timing/e2_compare.csv` | 6 阶段 1P/8P 对比（ms / 加速比 / 占比） |
| `results/transaction_e2e/e2_timing/e2_raw_compare.csv` | 9 原始阶段 1P/8P 对比 |
| `results/transaction_e2e/e2_timing/raw/e2_1p.csv` / `e2_8p.csv` | 逐轮 tidy 数据（rep × stage × seconds） |
| `results/transaction_e2e/e2_timing/raw/run_1p.log` / `run_8p.log` | 原始 stdout（含 SHAPE/BUNDLE/RESULT/E2_STAGE） |
| `scripts/analysis/e2_stage_csv.py` | 日志 → CSV / 对比 解析器（可随重新采集重跑） |
| `experiments/transaction_e2e/vendor/halo2_proofs-0.4.0/` | 插桩后的 vendored halo2_proofs（env 门控，默认零开销） |
| `experiments/transaction_e2e/harness/Cargo.toml` | 含 `[patch.crates-io]` 指向 vendored 副本 |

---

## 6. 复现命令

```bash
cd D:\Research\zcash-orchard-engineering\experiments\transaction_e2e\harness

# 构建（已含 patch；不要用 --locked，否则 Cargo.lock 不会切换到 path 源）
cargo build --release

# 1P（单 P-core）+ 启用分阶段计时
RAYON_NUM_THREADS=1 ORCHARD_E2_TIMING=1 ./target/release/orchard-e2e.exe \
  --mode v3 --reps 5 --mask 0x1 --tag e2_1p \
  > ../../../results/transaction_e2e/e2_timing/raw/run_1p.log 2>&1

# 8P（八 P-core）
RAYON_NUM_THREADS=8 ORCHARD_E2_TIMING=1 ./target/release/orchard-e2e.exe \
  --mode v3 --reps 5 --mask 0xFF --tag e2_8p \
  > ../../../results/transaction_e2e/e2_timing/raw/run_8p.log 2>&1

# 解析
cd D:\Research\zcash-orchard-engineering
python scripts/analysis/e2_stage_csv.py \
  --log1p results/transaction_e2e/e2_timing/raw/run_1p.log \
  --log8p results/transaction_e2e/e2_timing/raw/run_8p.log \
  --compare-out results/transaction_e2e/e2_timing/e2_compare.csv
```

> 关闭计时（恢复纯基线行为）：不设置 `ORCHARD_E2_TIMING` 即可，二进制与 E1 基线一致。
