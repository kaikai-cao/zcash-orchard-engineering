# 10 · Orchard Halo2 Prover 的 CPU 采样剖析（Stage E3）

> 对象：`orchard 0.16.0` + `halo2_proofs 0.4.0`（本地 vendor + patch），PostNu6_3 电路，2 Action，K=11，
> Release 级优化（`opt-level=3`）+ 独立 debuginfo 构建。
> 目的：**只做热点定位与性能归因**，不改约束、不改算法、不做优化。

---

## 0. 结论速览

E2 回答了"时间花在哪个阶段"，E3 回答"阶段内部是谁在花时间"。四条结论：

| # | 结论 | 证据 |
|---|---|---|
| 1 | **E2 的 54.9% `quotient` 不能称为"FFT 耗时"。** 它内部是 **域乘法 55% + 表达式求值递归 30%（合计 85%）**，FFT 子树在全程序只覆盖 **3.0%** | §6、§7 |
| 2 | **全程序最热的是标量域乘 `Fp::mul`（自身占 33.1%）**，热点行是 `pasta_curves/src/fields/fp.rs:368-369`（Montgomery 归约） | §6 |
| 3 | **14.6% 的 CPU 花在 `subtle::black_box` 这个"常量时间屏障"上**，单行 `subtle/src/lib.rs:234` 占 13.7%；调用链是 `curves::add → is_identity → ct_eq → Choice::from(u8)` | §8 |
| 4 | 8P 实际并行宽度只有 **5.76 / 8 核**，且**每份证明多消耗 1.73× CPU**；因此 E1 的 3.66× 加速比不是"某一段拖后腿"，而是**并行宽度不足 + 并行开销**共同结果 | §9 |

**与 E2 的一致性（1P，逐阶段 CPU 秒 vs E2 墙钟秒）**：quotient 1.922 vs 1.804 s，opening 0.499 vs 0.475 s，
synthesis 0.152 vs 0.152 s，advice_commit 0.389 vs 0.371 s，lookup 0.383 vs 0.373 s，permutation 0.126 vs 0.113 s。
两种完全独立的方法（阶段计时 vs 统计采样）在**同一量级、同一排序**上吻合，这是 E2 阶段边界划分正确的独立证据。

---

## 1. E3 要回答的问题

| E2 给出 | E3 必须回答 |
|---|---|
| `quotient` 占 1P 墙钟 54.9% | 这 1.8 秒究竟是 FFT、域运算、承诺，还是别的？ |
| `opening` 占 14.4% | 是 IPA 的 MSM、曲线运算，还是求值？ |
| `advice_commit` 占 11.3% | 是 MSM 桶累加，还是批量归一化？ |
| `synthesis` 只占 4.6% | 它内部是否就是 Merkle/NoteCommit 的曲线运算？ |

E3 的定位是**归因**，不是再测一遍阶段耗时。

---

## 2. E3.1：Profiler 环境侦察（先证伪，再选方法）

不假定任何 profiler 能直接分析当前 MSVC Release 二进制。实测结果：

| 工具 | 状态 | 说明 |
|---|---|---|
| `wpr.exe` / `wpa.exe` / `xperf.exe` | 存在（Windows Kits） | ETW CPU 采样所需 |
| VTune | 未安装 | — |
| samply / perf / cargo-flamegraph | 未安装 | Windows 上本也不适用 |

**决定性阻断**：当前会话**非提权**（`IsInRole(Administrator) = False`），`wpr -start CPU` 返回

```
0xc5585011  Failed to enable the policy to profile system performance
```

ETW 内核采样（CPU profile）需要管理员权限，因此 **WPR/xperf 路线不可用**。

另外两个已确认事实：
- 正式基线 `[profile.release] debug = false`，**没有 PDB**，即使能采样也解析不出符号；
- DbgHelp 可用（`dbghelp.dll` 随系统提供），`windows-sys 0.59` 已在本地 cargo 缓存中（离线可用）。

**结论**：改用**进程内统计采样**——在同一个进程里起一个采样线程，周期性挂起其它线程、读 `CONTEXT`、用
DbgHelp 走栈。这不是插桩（不在证明路径上加任何代码），是标准固定节拍的统计采样，只是采样源从内核 ETW
换成了进程内。

### 记录的可复现状态

| 项 | 值 |
|---|---|
| zcash-orchard-engineering commit | `6d852ff2c07c1db57671df16dfffd05baa6cc510` |
| orchard-upstream commit | `616a669df8c9a59c33d47064d3ce04b25d026b2e` |
| halo2_proofs | 0.4.0（`[patch.crates-io]` → `experiments/transaction_e2e/vendor/halo2_proofs-0.4.0`） |
| pasta_curves | 0.6.0（registry） |
| subtle | 2.6.1（registry） |
| rustc / cargo | 1.88.0 / 1.88.0，`x86_64-pc-windows-msvc` |
| 采样构建 | `[profile.sampling] inherits = "release"` + `debug = true`，产物在 `target/sampling/` |
| 二进制 / PDB | `orchard-e2e.exe` 4.19 MB / `orchard_e2e.pdb` 57 MB |
| 正式基线 | `target/release/orchard-e2e.exe` **未被覆盖**（时间戳仍为 11-03 19:08） |

`debug = true` 下 rustc 用 `/Z7`（调试信息内嵌在对象文件里），链接器把全部 crate 的符号汇入**单个** PDB，
所以 `target/sampling/` 下只有主 PDB（57 MB），没有 per-crate PDB——这正好省掉了拼符号路径的麻烦。

---

## 3. 采样器设计

```
主线程                                 采样线程（每 tick）
  │ create_proof                          │ sleep(interval)
  ├─ e3_set(1..9)  ──► E3_STAGE ─────────►│ 轮转取一个 tid
  │  (AtomicU8, 仅有 E2 计时开启时写)      │ OpenThread → SuspendThread
  │                                       │ GetThreadContext(CONTEXT_FULL)
  │                                       │ StackWalk64 + DbgHelp（≤32 帧）
  │                                       │ 记录 (tid, stage, pc[])
  └─ e3_set(0)                            │ ResumeThread
                                          └─ 停止后统一解析符号并落盘
```

四个设计决定：

1. **阶段打标（关键）**：在 vendored `prover.rs` 的 9 个 E2 阶段边界各加一句 `e3_set(code)`（写一个
   `AtomicU8`，`Relaxed` 序，且仅当 `ORCHARD_E2_TIMING` 开启时才写）。采样线程在抓栈的同一刻读这个原子，
   于是**每条样本自带"当时处在哪个阶段"的标签**。"热点函数属于哪个阶段"因此是**实测**，不是靠名字猜。
   注意这是**相位级**而非线程级变量：create_proof 的阶段推进是主线程驱动的，rayon 并行段发生在阶段内部，
   故相位标签对各工作线程一致。
2. **忙/闲分类**：叶子帧落在等待原语（`NtWaitForWorkViaWorkerFactory`、`NtAlertThreadByThreadId`、
   `NtDelayExecution`、`WaitForSingleObject` …）的样本记为 **idle**。所有百分比都以 **busy 样本**为分母，
   这是 CPU 时间归因的正确分母。空闲/忙碌的绝对值另存 `threads.csv`。
3. **轮转采样**：每个 tick 只采一个线程（`tids[rr % n]`），而不是每 tick 扫全部线程。这样每 tick 的侵入
   成本降为一次挂起+走栈，且各线程单位时间内的样本数期望相同。
4. **三种聚合口径**，避免踩坑：
   - `busy_self_pct`：叶子帧 = 该函数的样本占比（**真实自耗时**，归因主口径）；
   - `busy_incl_pct`：栈上出现次数（**递归会重复计数**，如 `evaluate::recurse` 达 296%，不可直接引用）；
   - `coverage_pct`：**去重覆盖度**——一条样本里该函数出现 ≥1 次即计 1。回答"这个子树的整体占比"用这个
     （FFT、MSM 的占比就是靠它得出的）。

### 三个真实的实现坑（都踩过，记录备查）

| 现象 | 根因 | 修法 |
|---|---|---|
| 采样到 2470 个快照但栈全空 | `STACKFRAME64.AddrPC.Mode` 写了 `0`；`ADDRESS_MODE::AddrModeFlat` 实际是 **3**（0 是 `AddrMode1616`） | 三处 `Mode` 全设为 3 |
| `GetThreadContext` 全部失败，`err=998 (ERROR_NOACCESS)` | x64 `CONTEXT` 要求 **16 字节对齐**，栈上 `mem::zeroed()` 只有 8 字节对齐 | `#[repr(C, align(16))] struct AlignedContext(CONTEXT)` |
| 解析符号时**段错误** | `SYMBOL_INFOW::NameLen` 返回**完整名字长度**（Rust 泛型名常 > 1024），按它切片越界 | `NameLen.min(SYM_NAME_LEN)` 后再切片 |

还有一个非致命但影响读数的细节：Windows 默认定时器精度 ~15.6 ms，`sleep(4ms)` 实际 ~15.6 ms 以上，
叠加走栈开销后**每轮约 30 ms**（1P 实测 21.21 s / 673 轮 ≈ 31.5 ms）。这是采样率的硬上限，不是 bug。

### 符号与行号归因

- 函数名：`SymFromAddrW`（`SYMOPT_UNDNAME`）；
- 源文件:行：`SymGetLineFromAddrW64`（`SYMOPT_LOAD_LINES`），压缩为 `<crate>/src/<path>.rs`；
- 调用者：把样本的第 2 帧记作 leaf 的 caller，输出 `hot_callers.csv`。

**为什么要行号**：优化版 MSVC 链接后，函数符号的 extent 可能缺失/陈旧，`SymFromAddr` 按"最近的前置符号"
归属，会把后面一段代码算到它头上。行号信息是逐指令区间，粒度更细，用来交叉校验。实测确实发现了一个
这样的疑似桶（`_NLG_Return2`，1.8%，CRT 遗留 thunk），已单列不参与结论。

---

## 4. 采样扰动：必须量化的代价

采样要挂起线程，必然扰动被测程序。同一二进制、只切换 `ORCHARD_E3_SAMPLE`：

| 配置 | 无采样器 | 开采样器 | 扰动 | 参考：E1 正式基线 |
|---|---:|---:|---:|---:|
| 1P（mask `0x1`, threads=1） | 3348.8 ms/证明 | 3535.3 ms/证明 | **+5.6%** | 3374.6 ms/证明 |
| 8P（mask `0xFF`, threads=8） | 971.7 ms/证明 | 1071.6 ms/证明 | **+10.3%** | 931.6 ms/证明 |

两条读数：
- **采样构建本身是性能中性的**：无采样器时 3348.8 ms vs 正式基线 3374.6 ms（−0.8%），说明
  `debug = true` 没有引入可观测的运行时代价；
- **采样带来的扰动 1P 约 6%、8P 约 10%**，且并行度越高扰动越大（挂起 12 个线程的调度代价更高）。

因此 E3 的数字只用于**相对归因**（谁占多少比例），不用于替代 E1/E2 的绝对耗时。

---

## 5. 结果一：阶段级归因，并与 E2 交叉验证

采样规模：1P 3366 样本 / 665 busy（19.8%），5 线程，**mean_busy_threads = 1.01**；
8P 3766 样本 / 1769 busy（47.0%），12 线程，**mean_busy_threads = 5.76**。
1P 下同时只有一个线程在跑，**CPU 份额 ≡ 墙钟份额**，这是本表最干净的一列。

| 阶段 | 1P CPU 份额 | 1P CPU s/证明 | E2 1P 墙钟 ms | 偏差 | 8P CPU 份额 | 8P CPU s/证明 |
|---|---:|---:|---:|---:|---:|---:|
| quotient | **55.04%** | **1.922** | 1803.6 | +6.6% | **63.43%** | 3.831 |
| opening | 14.29% | 0.499 | 474.8 | +5.1% | 10.85% | 0.656 |
| advice_commit | 11.13% | 0.389 | 371.3 | +4.8% | 9.10% | 0.550 |
| lookup（permute+product） | 10.98% | 0.383 | 372.9 | +2.7% | 10.74% | 0.649 |
| synthesis | 4.36% | 0.152 | 152.1 | **±0%** | 2.60% | 0.157 |
| permutation | 3.61% | 0.126 | 113.4 | +11% | 2.77% | 0.167 |
| instance / eval_setup | 0.45% | 0.016 | 小 | — | 0.40% | 0.024 |
| **合计** | 100% | **3.492** | 3291 | +6.1% | 100% | **6.040** |

独立方法、独立误差来源，逐阶段偏差都在采样扰动量级（≤11%）以内，**排序完全一致**——E2 的阶段边界划分成立。

> `instance` 与 `eval_setup` 在 1P 分别只有 2 / 1 个 busy 样本，**统计上无意义**，本节不给它们下任何结论。

---

## 6. 结果二：热点函数证据表（E3 核心交付）

`self%` = 叶子帧占比（CPU 时间归因），`cov%` = 去重覆盖度（该子树整体占比）。
"阶段内"列是该函数在所属阶段 busy 样本中的占比；"全程序"列是它在全部 busy 样本中的占比。

| 阶段 | 热点函数 | 阶段内% | 全程序 self% | cov% | 主要操作 | 1P→8P（全程序） |
|---|---|---:|---:|---:|---|---|
| **quotient** | `pasta_curves::fields::fp::Fp::mul` | **55.2** | **33.08** | 33.08 | Montgomery 域乘（`fp.rs:368-369`） | 42.85%（+9.8pp） |
| | `poly::evaluator::evaluate::recurse` | 29.5 | 16.24 | **48.72** | 约束表达式的 coset 逐行求值递归 | 15.04% |
| | `curves::impl$116::add` | 4.6 | 2.56 | — | 盲化多项式承诺的点加 | 基本持平 |
| | `subtle::black_box<u8>` | 4.4 | 2.41 | 2.41 | 常量时间屏障（§8） | — |
| **opening** | `curves::impl$95::double` | 30.5 | 4.36 | 6.77 | MSM 桶内点倍 | 3.50% |
| | `subtle::black_box<u8>` | 28.4 | 4.06 | 4.06 | 常量时间屏障 | — |
| | `curves::impl$116::add` | 21.1 | 3.01 | — | 桶累加的点加 | — |
| | `Buckets::sum`（MSM 主体） | — | 1.20 | **28.72** | 桶填充 + 桶归约 | 1.19% |
| **advice_commit** | `subtle::black_box<u8>` | **29.7** | 3.31 | 3.31 | 常量时间屏障 | — |
| | `curves::impl$116::add` / `impl$115::add` | 23.0 / 20.3 | 2.56 / 2.26 | — | `commit_lagrange` 的 MSM + `batch_normalize` | — |
| | `recursive_butterfly_arithmetic` | 5.4 | 0.60 | **3.01** | coset FFT（`coeff_to_extended`） | 0.85% |
| **synthesis** | `curves::impl$4::double` | **41.4** | 1.80 | — | Sinsemilla/NoteCommit 点倍 | 0.51% |
| | `subtle::black_box<u8>` | 20.7 | 0.90 | 0.90 | 常量时间屏障 | — |
| | `curves::impl$25::add` | 17.2 | 0.75 | — | Merkle CRH、NoteCommit 点加 | — |
| | `ff::Field::pow<Fq>` | 3.4 | 0.15 | — | 基域幂运算 | — |

### 全程序口径（不分阶段）

| 类别 | self% 1P | self% 8P | 变化 |
|---|---:|---:|---:|
| 域运算（`pasta_curves::fields`） | **33.53** | 43.92 | +10.4pp |
| 曲线运算（`pasta_curves::curves`） | 27.07 | 25.55 | −1.5pp |
| 表达式求值（`halo2_proofs::poly::evaluator`） | 16.39 | 15.04 | −1.4pp |
| 常量时间屏障（`subtle::black_box`） | **14.59** | 9.89 | −4.7pp |
| 空闲/等待 | 2.71 | 0.40 | −2.3pp |
| 其它（含 `_NLG_Return2` 1.80） | 2.56 | 2.83 | +0.3pp |
| MSM 自身 | 1.35 | 1.19 | −0.2pp |
| FFT 自身 | 1.20 | 1.02 | −0.2pp |
| 内存/分配 | 0.60 | 0.17 | −0.4pp |

> **注意**：MSM 与 FFT 的"自身"份额很低（1.2% / 1.2%），因为它们的时间几乎全在 `Fp::mul` 和
> `curves::add` 里。**判断它们的真实分量必须看覆盖度**：MSM 子树 **28.72%**（`Buckets::sum`；
> 若连同 rayon 并行驱动的栈帧 `bridge_producer_consumer` 一起算，上界 34.1%）、FFT 子树 **3.01%**。
> 两者相差一个数量级——这是 E3 对 E2 最重要的一处修正。

---

## 7. 结果三：`quotient` 到底在算什么（纠正"这是 FFT"的假设）

阶段内构成（1P，busy self 份额）：

| 阶段 | 构成 |
|---|---|
| quotient | 域运算 **55%**、表达式求值 **30%**、曲线 6%、常量时间屏障 4% |
| opening | 曲线运算 **67%**、常量时间屏障 **29%**、域运算 1% |
| advice_commit | 曲线运算 50%、常量时间屏障 30%、域运算 7%、FFT 7% |
| lookup_permute | 曲线 49%、常量时间屏障 20%、域运算 16% |
| permutation | 曲线 33%、常量时间屏障 33%、域运算 17% |
| lookup_product | 曲线 50%、常量时间屏障 32%、MSM 7%、FFT 7% |
| synthesis | 曲线 **66%**、常量时间屏障 21% |

关键纠正：

1. **`quotient` 不是 FFT 阶段。** halo2 0.4.0 的 `vanishing::construct` 主体是：把每条 gate/permutation/lookup
   表达式在 coset 域上**逐行求值**（`evaluate::recurse` 递归遍历表达式树），再做除以 `Z_H` 的运算。
   光这两项就占该阶段 **85%**（域乘 55% + 求值递归 30%），且求值递归里调用的还是 `Fp::mul`。
   **FFT 子树在全程序只覆盖 3.0%**（其中大部分发生在 `advice_commit` 的 `coeff_to_extended`，不是 quotient）。
2. **`opening` 确实是 MSM 主导**（曲线 67% + MSM 子树覆盖 28.7%），与 E2 的 5.09× 加速比（并行度最高）
   一致：MSM 的桶可以按窗口切分并行。
3. **`synthesis` 的 4.36% 内部是 Sinsemilla/NoteCommit 的曲线运算**（66%）——这与 `08_action_cost_map.md`
   的"Merkle 58% 行 / NoteCommit 22% 行"是同一件事在两个口径下的呈现：**行数多 ≠ 时间多**，因为这个阶段
   总共只占 4.4%。

---

## 8. 结果四：14.6% 的 CPU 花在一个"常量时间屏障"上

单行热点：

```
13.72%  subtle/src/lib.rs:234   subtle::black_box<u8>
```

`subtle::black_box`（`#[inline(never)]`，函数体是 `core::ptr::read_volatile(&input)`）**在整条依赖树里
唯一的调用点是 `impl From<u8> for Choice`**。调用链（已按 vendor/registry 源码逐层核对）：

```
pasta_curves::curves::<impl Add>::add            // curves.rs:474/476
  → bool::from(self.is_identity())               // = z.is_zero()
    → Fp::ct_eq(&ZERO)                           // fp.rs:57-62，4 个 limb
      → u64::ct_eq → Choice::from(u8)            // subtle
        → subtle::black_box<u8>                  // read_volatile
```

每次曲线加法要判两次 identity（`self` 与 `rhs`），每次 4 个 limb，即约 **8 次 `black_box` 调用**。
这解释了为什么它在 `advice_commit`（30%）、`opening`（29%）、`synthesis`（21%）、`permutation`（33%）
这些"曲线运算密集"的阶段里占比都接近 1/3。

**该怎么看待这个数字**：`Choice` 的常量时间语义是为"掩盖秘密标量"设计的。prover 侧的点运算**没有需要
保密的秘密**（witness 最终会进证明），所以这里付出的是**纯粹的常量时间税**。但它是依赖库
（`pasta_curves` / `subtle`）的行为，改动意味着打补丁并重新论证正确性——这属于 Stage F 的取舍，E3 不越界。

同时它也是一个**警示**：如果只看"函数名像不像密码学热点"，很容易漏掉这类横跨所有阶段的固定税率。

---

## 9. 结果五：8P 的并行真实情况

| 指标 | 1P | 8P | 比值 |
|---|---:|---:|---:|
| 墙钟/证明 | 3535 ms | 1072 ms | **3.30×** 加速 |
| CPU/证明 | 3.492 s | 6.040 s | **1.73× 更多 CPU** |
| 实际并行宽度（mean busy threads） | 1.01 | **5.76**（配置 8） | — |
| 线程数 | 5 | 12 | — |

三点读法：

1. **并行宽度只有 5.76 / 8**。create_proof 是"阶段内并行、阶段间串行"的，加上 rayon 线程池没有满载、
   以及每次并行段启动/结束的同步，平均值自然低于 8。E2 里 synthesis 1.08× 的串行地板进一步压低了均值。
2. **CPU 总量膨胀 1.73×**（同一份 witness、同一份证明）。这来自 rayon 的 spin/唤醒、跨核同步、
   以及采样器挂起 12 个线程带来的额外调度。**这是 3.62×→3.30× 的一部分原因**，也是"加核不一定线性加速"的实测依据。
3. **8P 的 CPU 份额不能当墙钟份额用**。二者的换算关系是 `墙钟份额_i ∝ CPU 份额_i / 加速比_i`。
   8P 的 CPU 份额里 quotient 升到 63.4%、synthesis 降到 2.60%，恰恰反映 quotient 并行度高（单位墙钟内
   塞进更多 CPU 工作）、synthesis 并行度低。**把 8P 的 63.4% 直接说成"quotient 占 8P 墙钟 63%"是错的**——
   E2 实测的 8P 墙钟占比是 47.9%。

---

## 10. 证据强度与局限

**可信度**
- 1P 下 CPU 份额 ≡ 墙钟份额（同时仅一个线程忙碌），且与 E2 的独立计时逐阶段吻合（偏差 ≤11%，排序一致）。
- 阶段标签是实测而非规则匹配，`stage_samples.csv` 的份额即阶段占比。
- 采样误差按二项分布估计（1P 共 665 个 busy 样本）：quotient（55.0%）标准误 ≈1.9pp（相对 3.5%）；
  opening（14.3%）≈1.4pp（相对 9.5%）；synthesis（4.4%）≈0.8pp（**相对 18%**）；
  instance（2 个）/ eval_setup（1 个）**完全不可用**。引用时按此量级打折。

**局限（必须和结论一起读）**
1. **栈深上限 32 帧**：递归深或调用链长的函数（`create_proof` 本身覆盖度仅 4.96%）会被截断，
   靠近栈底的函数覆盖度被低估。叶子帧归因（self%）不受影响。
2. **符号 extent 伪影**：`_NLG_Return2` 1.8% 已识别为疑似桶，未参与结论；`subtle::black_box` 已用
   行号+调用者双重核对，确认是真热点而非伪影。
3. **采样扰动 1P ~6% / 8P ~10%**：只做相对归因。
4. **单机、单次会话**（i7-14700，P-core 绑定，电源计划=平衡）；未做多次重复的置信区间合并。
5. **相位标签是进程级**：并行段内部各线程共享同一阶段标签，无法区分"同一阶段内不同子任务"。
6. `instance` / `eval_setup` 样本太少，本文件不对其下结论。

---

## 11. 对 Stage F 的含义（只给证据，不选型）

| 候选方向 | E3 证据 | 可触达的 CPU 份额 |
|---|---|---|
| C-1 域乘原语（`Fp::mul` / Montgomery 归约） | 自身 33.1%，热点行 `fp.rs:368-369` | ~33%（跨所有阶段） |
| C-2 常量时间屏障（`subtle::black_box`） | 自身 14.6%，单行 13.7% | ~15%（prover 侧可用变量时间替换） |
| C-3 表达式求值（`evaluate::recurse`） | 自身 16.2%，覆盖 48.7% | ~16%，且与约束项数线性相关 |
| B 固定基乘法 | 在 synthesis 内，而 synthesis 仅 4.36% | ≤4% |
| A Sinsemilla/Merkle 减行 | 同上；减行只按比例影响 quotient 的表达式项 | 间接、且量级受 §7 约束 |

一条方法论结论：**"减行"能不能换成"减时间"，取决于它减少的是不是 `evaluate::recurse` 遍历的表达式项**。
E2/E3 都表明 Merkle/NoteCommit 的行（58%/22%）不在时间主轴上，真正的杠杆是
**每行每约束的域乘次数**（§7 的 58%）与**每次点运算的常量时间开销**（§8）。

---

## 12. 复现

```bash
# 0) 构建采样配置（带 PDB，独立 target 目录，不覆盖正式基线）
cd experiments/transaction_e2e/harness
cargo build --profile sampling

# 1) 1P 采样（6 次证明，4ms 节拍，绑定 P-core 0）
RAYON_NUM_THREADS=1 ORCHARD_E3_SAMPLE=1 ORCHARD_E2_TIMING=1 \
  ./target/sampling/orchard-e2e.exe --mode profile --reps 6 --mask 0x1 \
  --e3-out ../../../results/transaction_e2e/e3_profile/1p --e3-interval-ms 4

# 2) 8P 采样（20 次证明，mask 0xFF）——必须与 1P 串行执行，避免 CPU 争用
RAYON_NUM_THREADS=8 ORCHARD_E3_SAMPLE=1 ORCHARD_E2_TIMING=1 \
  ./target/sampling/orchard-e2e.exe --mode profile --reps 20 --mask 0xFF \
  --e3-out ../../../results/transaction_e2e/e3_profile/8p --e3-interval-ms 4

# 3) 扰动对照（同一二进制，关掉采样器）
RAYON_NUM_THREADS=1 ./target/sampling/orchard-e2e.exe --mode profile --reps 6 --mask 0x1

# 4) 生成报表
cd ../../.. && python scripts/analysis/e3_profile_report.py --root results/transaction_e2e/e3_profile
```

> `ORCHARD_E2_TIMING=1` 是**必须**的：阶段标签只在 E2 计时开启时才写（保证 release 构建零影响）。

### 产物

```
docs/orchard/10_cpu_sampling_profile.md        # 本文
results/transaction_e2e/e3_profile/
├── comparison.csv                 # 阶段对比：1P/8P CPU 份额 + 绝对 CPU 秒/证明
├── hot_functions_compare.csv      # 函数级 1P vs 8P + 阶段 + 源文件 + 类别
├── operation_categories.csv       # 类别汇总（域/曲线/求值/屏障/FFT/MSM/…）
├── stage_category_mix.csv         # 阶段内构成
├── summary.txt                    # 上述内容的可读汇总
├── 1p/{raw,profile}/              # raw: e3_samples.txt / e3_modules.txt / run.log / control_no_sampler.log
└── 8p/{raw,profile}/              # profile: hot_functions / stage_functions / stage_source_files /
                                   #          source_files / hot_lines / hot_callers / threads / stage_samples
scripts/analysis/e3_profile_report.py
experiments/transaction_e2e/harness/src/e3sampler.rs        # 采样器（env 门控，默认不参与构建行为）
experiments/transaction_e2e/vendor/halo2_proofs-0.4.0/     # E2 计时 + E3 阶段打标
```

`hot_functions.csv` 的列含义：`exclusive_samples` 全部样本 / `busy_exclusive_samples` 忙碌样本 /
`busy_self_pct` 自耗时占比 / `inclusive_samples` 含递归重复 / `coverage_samples`、`coverage_pct` 去重覆盖度。
