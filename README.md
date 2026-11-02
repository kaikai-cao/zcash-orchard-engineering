# zcash-orchard-engineering

Engineering experiments and performance analysis for Zcash Orchard and Halo2.

官方源码始终独立放在 `D:\Research\orchard-upstream`，本仓库只保存实验材料与研究成果。

## 当前状态

阶段 A（环境检查与原始基线）已完成，基线已冻结：

| 项目 | 值 |
| --- | --- |
| 上游 Commit | `616a669df8c9a59c33d47064d3ce04b25d026b2e`（`0.16.0-1-g616a669`） |
| 工具链 | `rustc 1.88.0` / `x86_64-pc-windows-msvc` |
| 官方测试 | 173 passed / 0 failed / 1 ignored |
| 单 Action 证明（K=11，单线程，P-core 绑定） | **1.9293 s**（n=3，RSD 0.44%） |

- 环境记录：`docs/environment/orchard_environment.md`
- 基线报告：`results/analysis/baseline_summary.md`
- 机器可读汇总：`results/analysis/all_runs.csv`

## 测量协议（强制）

1. **必须用 `cargo +1.88`**。本机默认工具链是 1.98.1/gnu，漏写会编译出不可比的产物。
2. **必须绑定 P-core**。不绑定时该负载会被 Windows Thread Director 放到 E-core，单 Action 证明从 1.93 s 变成 4.16 s（2.16×）。用 `scripts/benchmark/run_orchard_k11_pinned.ps1`，掩码写进 `metadata.md`。
3. **不修改上游源码**，`git status --porcelain` 必须为空；runner 会做 preflight 检查。
4. **每轮单独归档**，不要把多轮结果合并成一个数字；Criterion 的 `change` 只在同一掩码口径内可比。
5. 记录 Commit SHA + `Cargo.lock` SHA256，两者缺一不可。

## 目录

```
docs/environment/    环境与版本记录
docs/halo2/          Halo2 学习笔记（阶段 B）
docs/orchard/        Orchard 源码分析（阶段 C）
docs/experiments/    实验协议
experiments/         实验设计
scripts/benchmark/   基准运行脚本（未绑定 / 单核绑定）
scripts/analysis/    结果汇总
results/raw/         每次运行的原始日志 + Criterion 产物
results/analysis/    汇总表与报告
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
