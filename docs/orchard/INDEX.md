# Orchard / Halo2 研读索引（01–13）

> 学习记录系列总览。按阅读顺序排列；带 ★ 的是阶段结论/测量报告，其余为逐模块拆解。

| 编号 | 文件 | 主题 | 关键结论 |
| --- | ---: | --- | --- |
| 01 | `01_circuit_instance.md` | Orchard 电路实例与 Action 结构 | — |
| 02 | `02_configure.md` | `configure()` 约束系统搭建 | — |
| 03 | `03_synthesize_base.md` | `synthesize_base()` 基础赋值 | — |
| 04 | `04_cv_net.md` |  Commitment Value 网络 | — |
| 05 | `05_nf_old.md` |  Nullifier（旧 note） | — |
| 06 ★ | `06_variable_base_mul.md` | 变基乘法（ScalarVar::from_base 零成本边界；overflow 五约束；k_254 精确下界） | 已冻结 |
| 07 | `07_note_commit_new.md` | 新 NoteCommit 五层结构 + old/new 对照 | 已冻结 |
| 08 ★ | `08_action_cost_map.md` | Action 级电路成本地图（Merkle 58% / NC 22% / 固定基乘 425 行） | 行数布局基线 |
| 09 ★ | `09_create_proof_timing.md` | **Stage E2**：create_proof 分阶段计时（quotient 55% 主导，synthesis 仅 4.6%） | 回答「占行≠耗时」 |
| 10 ★ | `10_cpu_sampling_profile.md` | **Stage E3**：进程内 CPU 采样（阶段打标 + 函数/行/调用者归因） | 回答「阶段内部谁在花时间」 |
| 11 ★ | `11_stage_f_scope.md` | **Stage F0**：证据冻结、三套百分比口径、优化假设登记表（H1–H6）与验收门槛 | 扰动口径统一为 +5.6%/+10.3% |
| 12 ★ | `12_fp_mul_microbench.md` | **Stage F1**：`Fp::mul` 微基准 + 汇编审计（三配置对照、asm 上界、调用开销） | **H1 否决**；调用开销上限 10.6% → F2 修正为 ≈1.8% |
| 13 ★ | `13_expression_evaluator_analysis.md` | **Stage F2**：表达式求值器成本归因（节点/深度/重复、逐项归因、隔离计时） | **quotient 的 89.1% 是求值器；38.4% 的乘法是乘 −1** |

## 阶段主线（Stage A–F）

- **Stage A**：PostNu6_2 单 Action 基线（P-core 绑定，1.93 s）。
- **Stage E1**：PostNu6_3 双 Action 可重复基线（1P 3.37 s / 8P 0.93 s，3.62×）。
- **Stage E2**：源码级分阶段计时（文档 09）——定位到 quotient 55% / opening 14%，synthesis 仅 4.6%。
- **Stage E3**：CPU 采样剖析（文档 10）——quotient 内是域乘 55% + 表达式求值 30%，**FFT 子树仅 3.0%**；
  全程序 `Fp::mul` 33%、常量时间屏障 `subtle::black_box` 15%。
- **Stage F0**（文档 11）：冻结 E3 证据，统一扰动口径，登记假设 H1–H6。
- **Stage F1**（文档 12）：`Fp::mul` 微基准与汇编审计——`+bmi2,+adx` 无效（H1 否决）；
  上游手写汇编只换来 2.6% 内联吞吐；prover 里 `Fp::mul` 90%+ 未内联。
- **Stage F2**（文档 13）：把表达式树数清楚——quotient 的 1.79 s 里 **1.59 s 是求值器（89.1%）**，
  一次求值 **6570 次整块乘 × 16384 元素 = 1.076 亿次域乘**；归因**门约束 94.0%**（lookup 仅 3.9%）；
  最大单项是 **`Negated` 被实现成 `Scale(−1)`，占 38.4%**（上限 12.6%）；顺带把 H1c 从 10.6% 修正到 ≈1.8%。
- **Stage F3（下一步）**：落地 H6（`Ast::Neg`）并端到端验证。

## 核心研究问题与四阶段答案

**问题**：「电路中占用最多行的部分，是否也是实际证明过程中最耗时的部分？」

- **E2（阶段级）**：**否。** 行数最多的 Merkle/NoteCommit 所在的 witness 执行阶段（synthesis）仅占 4.6%；
  耗时主导是 quotient（54.9%）+ opening（14.4%）= 69.3%，对全部约束一视同仁。
- **E3（函数级）**：**连"耗时主导 = FFT"也不成立。** quotient 的 1.9 s 里 85% 是域乘与表达式求值，
  FFT 子树全程序只覆盖 3.0%；另有 14.6% 是 `subtle::black_box` 常量时间屏障。
  opening 才真正是 MSM（`Buckets::sum` 覆盖 28.7%）。
- **F1（原语级）**：**"最热的函数"也不等于"最该改的函数"。** `Fp::mul` 的算术吞吐已接近该实现的
  可用水平（24 次乘法给出量级，非端口饱和证明），而它在 prover 中 90%+ 以**未内联调用**形态出现。
- **F2（结构级）**：**"最热的原语"也不等于"最该改的原语"。** 那 1.076 亿次乘法里，**38.4% 是把整块
  乘以 −1**——成本来自求值器的表达式表示，而不是 Montgomery 归约的效率。**问题从"算术快不快"
  变成"这棵树长什么样、每个节点值多少钱"。**

## E1→F2 的方法链条

| 层 | 手段 | 产出 | 可信度锚点 |
|---|---|---|---|
| E1 | 墙钟计时（Keygen/Prove 分离，5 次重复） | 3.37 s / 0.93 s，3.62× | 掩码绑核，避免 E-core 污染 |
| E2 | vendor + patch，create_proof 内 9 段互不重叠计时 | 阶段占比表 | 阶段求和 vs 外层差 2.7 ms |
| E3 | 进程内统计采样（挂起线程 + DbgHelp 走栈） | 阶段×函数×行×调用者 | 逐阶段 CPU 秒 ≈ E2 墙钟秒（偏差 ≤11%） |
| F1 | 独立微基准 + 链接后二进制反汇编 + Python 大整数向量 | 4 种形态的 ns、指令构成 | 5 轮 CV < 2%；592 + 12 000 项正确性全过 |
| F2 | 结构计数（表达式/Ast 遍历）+ 隔离计时 + 逐项归因 | 节点/深度/重复/1.076 亿次域乘 | 三段归因与表达式层**逐位相符**；1.076 亿 × 10.70 ns = E3 的 33.1%（采样 vs 计数独立吻合） |
