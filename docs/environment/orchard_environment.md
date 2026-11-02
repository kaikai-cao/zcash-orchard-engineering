# Orchard 实验环境记录

> 阶段 A（A1）产出。任何一次基线或优化实验都必须能回溯到这份记录中的具体值。
> 采集时间：2026-11-02 21:32 – 21:49 (GMT+8)

## 1. 源码基线

| 项目 | 值 |
| --- | --- |
| 仓库 | `https://github.com/zcash/orchard` |
| 本地路径 | `D:\Research\orchard-upstream` |
| 分支 | `main` |
| 完整 Commit SHA | `616a669df8c9a59c33d47064d3ce04b25d026b2e` |
| Commit 时间 | 2026-09-30 16:15:05 -0600 |
| Commit 标题 | `Merge pull request #564 from zcash/release/0.16.0` |
| `git describe` | `0.16.0-1-g616a669` |
| crate 版本 | `orchard 0.16.0` |
| 工作树状态 | clean（`git status --porcelain` 为空） |
| `Cargo.lock` SHA256 | `fac8acd70b798d588a5199c82b1008f103e01ed56b035a0ce76a54a9470f31fb` |

**基线实验期间未对上游源码做任何修改。**

## 2. 工具链

| 项目 | 值 |
| --- | --- |
| Rust | `rustc 1.88.0 (6b00bc388 2025-06-23)` |
| LLVM | 20.1.5 |
| Host triple | `x86_64-pc-windows-msvc` |
| Cargo | `cargo 1.88.0 (873a06493 2025-05-10)` |
| 指定方式 | `cargo +1.88 ...`，与 `rust-toolchain.toml` 的 `channel = "1.88"` 一致 |
| 编译配置 | 测试：`test` profile（unoptimized + debuginfo）<br>基准：`bench` profile（`opt-level = 3`、`debug = true`，由 `Cargo.toml` 的 `[profile.bench]` 指定） |

注意：机器上另有 `stable-x86_64-pc-windows-gnu`（rustc 1.98.1）作为默认工具链。**若省略 `+1.88`，会落到 1.98.1 上编译**，产物与基线不可比。

## 3. 锁定依赖

| crate | 版本 |
| --- | --- |
| `halo2_proofs` | 0.4.0 |
| `halo2_gadgets` | 0.6.0 |
| `pasta_curves` | 0.6.0 |
| `halo2_poseidon` | 0.2.0 |
| `reddsa` | 0.6.1 |
| `sinsemilla` | 0.2.0 |
| `incrementalmerkletree` | 0.9.0 |
| `zcash_note_encryption` | 0.5.1 |
| `rayon` | 1.12.0 |
| `criterion` | 0.5.1 |

`halo2_proofs` 启用的 feature：`batch`、`floor-planner-v1-legacy-pdqsort`（见 `Cargo.toml`）。

## 4. 机器

| 项目 | 值 |
| --- | --- |
| 机型 | Dell Pro Tower QCT1250 |
| 操作系统 | Microsoft Windows 11 家庭版 Insider Preview 中文版 |
| 版本 / 构建号 | 10.0.26220 / Build 26220 |
| CPU | Intel Core i7-14700（Raptor Lake Refresh） |
| 物理核 / 逻辑核 | 20 / 28（8 个 P-core 含 SMT + 12 个 E-core） |
| 标称主频 | 2100 MHz（基准），P-core 睿频上限约 5.4 GHz |
| 内存 | 31.69 GiB |
| 电源方案 | **平衡（Balanced）**，GUID `381b4222-f694-41f0-9685-ff5bb260df2e` |

### 4.1 逻辑 CPU 与核心类型的实测映射

Windows 没有直接暴露「逻辑 CPU → P/E」的查询接口，以下结论由**实测**确定（见 `results/analysis/baseline_summary.md`）：

| 逻辑 CPU | 掩码 | 实测单 Action 证明耗时 | 判定 |
| --- | --- | --- | --- |
| 0 | `0x1` | 1.927 s | P-core |
| 0–7 | `0xFF` | 2.072 s | 全部为 P-core |
| 16 | `0x10000` | 3.995 s | E-core |

即：**逻辑 0–15 为 8 个 P-core 的两个 SMT 线程，逻辑 16–27 为 12 个 E-core。**
该映射是后续所有绑定实验的前置假设，换机器必须重新测定，不能沿用。

## 5. 复现命令

```powershell
# 官方测试套件
cd D:\Research\orchard-upstream
cargo +1.88 test --locked

# 单 Action 证明基线（未绑定，仅作对照，见报告中的调度问题）
$env:RAYON_NUM_THREADS = "1"
cargo +1.88 bench --locked -p orchard --features circuit --bench orchard_k11_prover

# 单 Action 证明基线（绑定 P-core，推荐口径）
& D:\Research\zcash-orchard-engineering\scripts\benchmark\run_orchard_k11_pinned.ps1 `
    -RunId p0_run_001 -AffinityMask 0x1
```

## 6. 已知环境注意事项

1. **必须使用 `+1.88`**，否则落到默认的 1.98.1/gnu 工具链。
2. **必须显式绑定 P-core**。不绑定时该负载会被调度到 E-core，耗时翻倍（4.2 s vs 1.93 s）。详见基线报告第 4 节。
3. **电源方案为「平衡」**。当前未切换到「高性能」，因为基线就是在这个状态下采集的；若以后要比较不同机器的绝对数值，需同时记录电源方案，或统一固定为同一方案。
4. `criterion` 的历史结果保存在 `target/criterion/`，会参与「相对上次」的比较。跨口径（绑定 / 不绑定）比较时，Criterion 打印的 `change` 百分比没有物理意义，只能看绝对区间。
