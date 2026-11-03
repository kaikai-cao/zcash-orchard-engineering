# zcash-orchard-engineering

Engineering experiments and performance analysis for Zcash Orchard and Halo2.

官方源码始终独立放在 `D:\Research\orchard-upstream`，本仓库只保存实验材料与研究成果。

## 当前状态

阶段 A–E 已完成，Stage F 进行中（F0/F1 已收口）。研究问题从「哪个阶段最慢」推进到
「哪些底层操作消耗了 CPU」，并已用微基准否定掉第一个假设。

| 阶段 | 内容 | 结论 | 文档 |
| --- | --- | --- | --- |
| A | 环境与原始基线 | 单 Action 1.9293 s（K=11，1P，绑 P-core） | `docs/environment/` |
| B–C | Halo2 / Orchard 源码研读 | 成本地图：Merkle 58% + NoteCommit 22.2% 行数 | `docs/halo2/`、`docs/orchard/07–08` |
| D–E1 | 端到端基线（1P/8P） | 3.375 s / 0.932 s，加速比 3.62× | `results/transaction_e2e/baseline/` |
| E2 | `create_proof` 九阶段计时 | quotient 54.9%、opening 14.4%；**占行最多 ≠ 最耗时**（synthesis 仅 4.6%） | `docs/orchard/09` |
| E3 | 进程内 CPU 采样 + 阶段标签 | quotient 内不是 FFT（3.0%）；`Fp::mul` self 33.1%、`subtle::black_box` 14.6% | `docs/orchard/10` |
| F0 | 证据冻结 | 扰动口径统一为 1P +5.6% / 8P +10.3% | `docs/orchard/11` |
| F1 | `Fp::mul` 微基准 + 汇编审计 | **H1 否决**：`+bmi2,+adx` 无收益；真问题是未内联（调用开销 32.5%） | `docs/orchard/12` |

| 冻结项 | 值 |
| --- | --- |
| 上游 Commit | `616a669df8c9a59c33d47064d3ce04b25d026b2e`（`0.16.0-1-g616a669`） |
| 工具链 | `rustc 1.88.0` / `x86_64-pc-windows-msvc` |
| 官方测试 | 173 passed / 0 failed / 1 ignored |
| 单 Action 证明（K=11，1P，P-core 绑定） | **1.9293 s**（n=3，RSD 0.44%） |

- 全部研究文档索引：**`docs/orchard/INDEX.md`**
- 冻结清单（commit / lock sha256 / 依赖指纹 / 编译参数）：`results/transaction_e2e/f0_freeze/manifest.json`
- 环境记录：`docs/environment/orchard_environment.md`

## 测量协议（强制）

1. **必须用 `cargo +1.88`**。本机默认工具链是 1.98.1/gnu，漏写会编译出不可比的产物。
2. **必须绑定 P-core**。不绑定时该负载会被 Windows Thread Director 放到 E-core，单 Action 证明从 1.93 s 变成 4.16 s（2.16×）。用 `scripts/benchmark/run_orchard_k11_pinned.ps1`，掩码写进 `metadata.md`。
3. **不修改上游源码**，`git status --porcelain` 必须为空；runner 会做 preflight 检查。
   Stage E2/E3 需要插桩，做法是 `experiments/transaction_e2e/vendor/` 下的**副本** + `[patch.crates-io]`，
   `D:\Research\orchard-upstream` 始终干净（改动只有 `prover.rs`，见 vendor 内 patch）。
4. **每轮单独归档**，不要把多轮结果合并成一个数字；Criterion 的 `change` 只在同一掩码口径内可比。
5. 记录 Commit SHA + `Cargo.lock` SHA256，两者缺一不可。

## 目录

```
docs/environment/    环境与版本记录
docs/halo2/          Halo2 学习笔记（阶段 B）
docs/orchard/        Orchard 源码分析（阶段 C）+ 证明性能剖析（E2–F1），索引进 INDEX.md
docs/experiments/    实验协议
experiments/         实验设计
  transaction_e2e/   端到端证明 harness（E1/E2/E3）
    vendor/          halo2_proofs 0.4.0 插桩副本，见其中 README.md
  f1_fp_mul/         F1：pasta_curves 0.6.0 Fp::mul 微基准
  f1_fp_mul_asmref/  F1 参照组：0.6.1 + asm 后端（性能上界，非生产路径）
scripts/benchmark/   基准运行脚本（未绑定 / 单核绑定）
scripts/analysis/    结果汇总（E1/E2/E3/F1 各自的 CSV 生成脚本）
results/raw/         每次运行的原始日志 + Criterion 产物
results/analysis/    汇总表与报告
results/transaction_e2e/  E1 基线 / E2 计时 / E3 采样 / F0 冻结 / F1 微基准
```

## 常用命令

```powershell
# 官方测试
cd D:\Research\orchard-upstream; cargo +1.88 test --locked

# 单 Action 基线（推荐口径：绑定 P-core 0）
& D:\Research\zcash-orchard-engineering\scripts\benchmark\run_orchard_k11_pinned.ps1 `
    -RunId p0_run_004 -AffinityMask 0x1

# 单 Action 基线（未绑定，仅作调度对照）
& D:\Research\zcash-orchard-engineering\scripts\benchmark\run_orchard_k11_baseline.ps1 `
    -RunId run_005

# 汇总所有轮次
python scripts/analysis/collect_runs.py
```

> Windows PowerShell 5.1 下若脚本被拦截，先执行
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force`（仅影响当前进程）。
