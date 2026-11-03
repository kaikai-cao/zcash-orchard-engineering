# Orchard / Halo2 研读索引（01–12）

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
| 11 ★ | `11_stage_f_scope.md` | **Stage F0**：证据冻结、三套百分比口径、优化假设登记表（H1–H5）与验收门槛 | 扰动口径统一为 +5.6%/+10.3% |
| 12 ★ | `12_fp_mul_microbench.md` | **Stage F1**：`Fp::mul` 微基准 + 汇编审计（三配置对照、asm 上界、调用开销） | **H1 否决；真瓶颈是调用形态（7.06 ns/次）** |

## 阶段主线（Stage A–F）

- **Stage A**：PostNu6_2 单 Action 基线（P-core 绑定，1.93 s）。
- **Stage E1**：PostNu6_3 双 Action 可重复基线（1P 3.37 s / 8P 0.93 s，3.62×）。
- **Stage E2**：源码级分阶段计时（文档 09）——定位到 quotient 55% / opening 14%，synthesis 仅 4.6%。
- **Stage E3**：CPU 采样剖析（文档 10）——quotient 内是域乘 55% + 表达式求值 30%，**FFT 子树仅 3.0%**；
  全程序 `Fp::mul` 33%、常量时间屏障 `subtle::black_box` 15%。
- **Stage F0**（文档 11）：冻结 E3 证据，统一扰动口径，登记假设 H1–H5。
- **Stage F1**（文档 12）：`Fp::mul` 微基准与汇编审计——**33% 的 mul 里"算术"已贴硬件上限
  （24 次乘法 = 端口下限）**，多余的是每次 7.06 ns 的未内联调用开销；换 asm 后端上限 4.0%，
  消除调用开销上限 10.6%。
- **Stage F2（下一步）**：表达式求值器分析（节点统计 / 重复率 / 分配计数），候选 H2。

## 核心研究问题与三阶段答案

**问题**：「电路中占用最多行的部分，是否也是实际证明过程中最耗时的部分？」

- **E2（阶段级）**：**否。** 行数最多的 Merkle/NoteCommit 所在的 witness 执行阶段（synthesis）仅占 4.6%；
  耗时主导是 quotient（54.9%）+ opening（14.4%）= 69.3%，对全部约束一视同仁。
- **E3（函数级）**：**连"耗时主导 = FFT"也不成立。** quotient 的 1.9 s 里 85% 是域乘与表达式求值，
  FFT 子树全程序只覆盖 3.0%；另有 14.6% 是 `subtle::black_box` 常量时间屏障（prover 侧无保密必要）。
  opening 才真正是 MSM（`Buckets::sum` 覆盖 28.7%）。
- **F1（原语级）**：**"最热的函数"也不等于"最该改的函数"。** `Fp::mul` 的算术已贴着 24 次乘法的
  端口下限（上游手写汇编只换来 2.6% 吞吐），真正的可优化项是它在 prover 中 91.8% 以**未内联调用**形态
  出现，每次 7.06 ns 的调用开销占该路径 32.5%。

## E1→F1 的方法链条

| 层 | 手段 | 产出 | 可信度锚点 |
|---|---|---|---|
| E1 | 墙钟计时（Keygen/Prove 分离，5 次重复） | 3.37 s / 0.93 s，3.62× | 掩码绑核，避免 E-core 污染 |
| E2 | vendor + patch，create_proof 内 9 段互不重叠计时 | 阶段占比表 | 阶段求和 vs 外层差 2.7 ms |
| E3 | 进程内统计采样（挂起线程 + DbgHelp 走栈） | 阶段×函数×行×调用者 | 逐阶段 CPU 秒 ≈ E2 墙钟秒（偏差 ≤11%） |
| F1 | 独立微基准 + 链接后二进制反汇编 + Python 大整数向量 | 4 种形态的 ns、指令构成、上限估算 | 5 轮 CV < 2%；592 + 12 000 项正确性全过 |
