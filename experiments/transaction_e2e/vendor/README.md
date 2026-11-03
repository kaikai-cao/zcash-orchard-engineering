# Vendored `halo2_proofs` 0.4.0（带 Stage E2/E3 插桩）

本目录保存的是一份 **只改了一个文件** 的 `halo2_proofs` 0.4.0 副本，用于 Stage E2/E3
的源码级计时与采样归因。放在仓库里是为了让实验可复现：`harness/Cargo.toml` 通过
`[patch.crates-io]` 直接指向它，删掉这个目录 harness 就编译不过。

## 与上游的差异

上游来源：crates.io `halo2_proofs 0.4.0`（`Cargo.lock` 锁定版本，未升级）。

```
src/plonk/prover.rs   +115 / -2
```

完整差异见 `halo2_proofs-0.4.0-e2e3.patch`（相对 `vendor/halo2_proofs-0.4.0/` 可用 `patch -p1` 应用）。

改动只有两类，全部塞进 `create_proof` 内部，**不改任何约束、算法或数据结构**：

| 插桩 | 作用 | 开关 |
| --- | --- | --- |
| `E2_TIMINGS` + `e2_add()` + `take_e2_timings()` | 9 个连续不重叠阶段的墙钟计时 | 环境变量 `ORCHARD_E2_TIMING`，**默认关闭**；关闭时只剩几次 `Instant::now()` |
| `E3_STAGE: AtomicU8` + `e3_set()` + `e3_stage_name()` | 把「当前处于哪个阶段」写进原子量，供采样线程给栈样本打阶段标签 | 无条件写一个 `AtomicU8`（单次 store，可忽略） |

9 个阶段的编号与顺序（E2 与 E3 共用同一套定义）：

| code | 阶段 | code | 阶段 |
| --- | --- | --- | --- |
| 1 | `instance` | 6 | `permutation` |
| 2 | `synthesis` | 7 | `lookup_product` |
| 3 | `advice_commit` | 8 | `quotient` |
| 4 | `eval_setup` | 9 | `opening` |
| 5 | `lookup_permute` | 0 | 不在证明中（Keygen / 空闲） |

各阶段以 `Instant::now()` 起点 + `e2_add(stage, t.elapsed())` 收尾，区间首尾相接、互不嵌套，
因此**阶段耗时可以直接相加**，不会重复计账（实测：阶段和 3288 ms vs 外层 `prove_s` 3291 ms）。

## 为什么用插桩而不是 profiler 直接归因

Windows 上 ETW/WPR 需要管理员权限（本机被拒绝，`0xc5585011`），VTune / samply / perf 均不可用。
自研采样器只能拿到 PC 栈，无法回答「这个样本属于 Halo2 的哪个阶段」——
所以在 prover 里放一个阶段码，让每个栈样本自带标签。详见 `docs/orchard/10_cpu_sampling_profile.md`。

## 注意

- `E3_STAGE` 是**进程级全局量**，只在单证明串行执行时有意义（1P/8P 各自进程、不并发多证明）。
- 这份副本仅服务测量，**不是**要提交给上游的补丁，也不应用于生产构建。
- 冻结指纹（git commit、`Cargo.lock` sha256、依赖指纹、编译参数）见
  `results/transaction_e2e/f0_freeze/manifest.json` 与 `docs/orchard/11_stage_f_scope.md`。
