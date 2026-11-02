# 阶段 A 基线报告：Orchard v0.16.0 官方测试与单 Action 证明成本

- 采集时间：2026-11-02 21:33 – 21:49 (GMT+8)
- 源码：`zcash/orchard` @ `616a669df8c9a59c33d47064d3ce04b25d026b2e`（`0.16.0-1-g616a669`，工作树 clean）
- 工具链：`rustc 1.88.0` / `x86_64-pc-windows-msvc`
- 环境详情：`docs/environment/orchard_environment.md`
- 原始日志：`results/raw/baseline/`、`results/raw/affinity/`
- 机器可读汇总：`results/analysis/all_runs.csv`（由 `scripts/analysis/collect_runs.py` 生成）

---

## 1. 官方测试套件（A2 验收项）

命令：

```powershell
cd D:\Research\orchard-upstream
cargo +1.88 test --locked
```

退出码 **0**，全程无 `error`、无 `warning`。

| 测试目标 | 通过 | 失败 | 忽略 | 耗时 |
| --- | ---: | ---: | ---: | ---: |
| `unittests src\lib.rs` | 163 | 0 | 1 | 154.29 s |
| `tests\builder.rs` | 8 | 0 | 0 | 66.45 s |
| `tests\platform_smoke.rs` | 1 | 0 | 0 | 25.67 s |
| `Doc-tests orchard` | 1 | 0 | 0 | 1.00 s |
| **合计** | **173** | **0** | **1** | 247.41 s（进程墙钟 4 min 08 s） |

唯一被忽略的测试：

```
test circuit::benchmark::benchmark_batch_verifier ... ignored, manual single-thread performance benchmark
```

它是人工触发的单线程性能测试，不参与 CI 门禁，忽略属预期行为。

按计划未执行 `--all-features`。

---

## 2. 单 Action 证明基线（A3 / A4 验收项）

基准对象：`benches/orchard_k11_prover.rs`

已核对的基准语义（读源码确认，非推测）：

| 项目 | 实际值 |
| --- | --- |
| Action 数量 | 1（`Builder::new(Coinbase, orchard_v2, SPENDS_DISABLED, anchor=[0;32])` + 1 个 Output，value = 10） |
| 电路版本 | `OrchardCircuitVersion::FixedPostNu6_2` |
| K | 11（`src/circuit.rs:76`） |
| 公开实例布局 | `INSTANCE_COLUMNS = 1`、`INSTANCE_ROWS = 10`（`src/circuit.rs:79-80`） |
| `ProvingKey` / `VerifyingKey` | 在 `benchmark_group` 之前构建，**不计入计时区间** |
| 正确性校验 | 计时前先完整 `create_proof` + `verify` 一次，失败则整轮 panic |
| 输入确定性 | fixture 种子 `[0x42; 32]`、证明随机源 `[0x24; 32]` 固定 |
| Criterion 设置 | `sample_size(10)`、`SamplingMode::Flat`、`warm_up_time(2s)`、`measurement_time(15s)` |
| 线程约束 | 源码断言 `RAYON_NUM_THREADS == "1"`，否则直接 panic |

> **对计划中 A3 参数表的一处修正**：表中「测量时间 15 秒、样本数 10」在单线程下无法同时满足——一次迭代约 1.9–4.2 s，10 个样本至少要 19–42 s。Criterion 会打印
> `Warning: Unable to complete 10 samples in 15.0s` 并**自动延长采样窗口**到恰好容纳 10 次迭代。
> 因此真实的有效测量窗口是 19.4 s（P-core 绑定）或约 41 s（未绑定），而不是 15 s。

### 2.1 未绑定（默认口径，`RAYON_NUM_THREADS=1`）

| 轮次 | Mean | 95% CI | Median | σ | RSD |
| --- | ---: | --- | ---: | ---: | ---: |
| run_001 | 4.0605 s | 3.9577 – 4.1616 | 4.0571 s | 0.1730 s | 4.26% |
| run_002 | 4.1258 s | 4.0915 – 4.1595 | 4.1324 s | 0.0579 s | 1.40% |
| run_003 | 4.2365 s | 4.1710 – 4.2966 | 4.2541 s | 0.1072 s | 2.53% |
| run_004 | 4.2211 s | 4.1854 – 4.2551 | 4.2377 s | 0.0596 s | 1.41% |
| **均值** | **4.161 s** | — | — | 0.083 s | 2.0% |

四轮之间的极差 4.3%，且呈轻微单调上升（4.06 → 4.13 → 4.24 → 4.22），提示存在热漂移。

### 2.2 绑定 P-core（逻辑 CPU 0，掩码 `0x1`）

| 轮次 | Mean | 95% CI | σ | RSD |
| --- | ---: | --- | ---: | ---: |
| p0_run_001 | 1.9339 s | 1.9260 – 1.9423 | 0.0139 s | 0.72% |
| p0_run_002 | 1.9267 s | 1.9242 – 1.9295 | 0.0046 s | 0.24% |
| p0_run_003 | 1.9273 s | 1.9230 – 1.9314 | 0.0071 s | 0.37% |
| **均值** | **1.9293 s** | — | 0.0039 s | 0.44% |

三轮极差 **0.37%**，比未绑定口径小一个数量级。

### 2.3 结论

- **单 Action（K=11）单线程证明成本：1.93 s**（P-core 绑定，n=3，RSD 0.44%，可直接作为后续实验的冻结基线）。
- 未绑定口径的 4.16 s **不能**作为基线，原因见第 3 节。

---

## 3. 一个必须先解决的方法学问题：E-core 调度污染

第一轮跑完时 Criterion 打出：

```
change: [+111.15% +117.09% +122.82%] (p = 0.00 < 0.05)
Performance has regressed.
```

即相对 `target/criterion` 里 21:03 留下的历史基线（反解约 **1.87 s**）慢了 117%。一个「没改过一行代码」的基线不该出现 2 倍差异，因此做了对照实验。

### 3.1 对照实验结果

| 口径 | 掩码 | 生效逻辑 CPU | Mean | 相对 P-core |
| --- | --- | --- | ---: | ---: |
| 绑定单 P 核 | `0x1` | 0 | 1.9293 s | 1.00× |
| 绑定 8 个 P 核 | `0xFF` | 0–7 | 2.0724 s | 1.07× |
| 绑定单 E 核 | `0x10000` | 16 | 3.9948 s | 2.07× |
| 未绑定 | — | 0–27 | 4.161 s | **2.16×** |

### 3.2 判读

1. **E-core 本身比 P-core 慢 2.07 倍**。这符合 Gracemont 与 Raptor Cove 在标量大整数运算上的差距（IPC + 主频双重劣势），与代码无关。
2. **未绑定的 4.161 s 与纯 E-core 的 3.995 s 几乎相等（差 4%）**，而如果 P/E 各占一半，结果应落在 2.96 s 附近。也就是说，在 4 秒的测量窗口里，**线程的有效计算几乎全部发生在 E-core 上**。
3. 运行期按核采样（1 s × 6 次，阈值 >40%）观察到占用出现在 CPU 16、20（E）以及 CPU 3、4、6（P），说明线程并非死钉在某一核，而是在迁移；迁移本身有代价——只绑 8 个 P 核比钉死 1 个 P 核慢 7%（2.07 vs 1.93），但远不足以解释 2 倍。
4. **主要因素是「E-core 被纳入可调度集合」，不是「迁移」**。这也解释了 1.87 s 的历史值：那次大概是在前台/交互语境下跑的，被 Thread Director 判为前台任务而留在 P-core。

> 机制层面可以这样理解：Windows 的 Thread Director 会把这类由脚本以非交互方式拉起的后台计算归类为 background/batch，优先放置到 E-core。对 Orchard 这种单线程、长时、纯计算负载，这一决定直接让测得成本翻倍。

### 3.3 对实验协议的影响（已落进脚本）

- **所有**单线程绝对耗时实验必须显式绑定 P-core，并在 `metadata.md` 里记录掩码。
- 阶段 D 的线程扩展实验不能只改 `RAYON_NUM_THREADS`：参与并行的 8 个 worker 若被摊到 E-core 上，加速比会被系统性压低。正确做法是**随线程数一起放大掩码**（1→`0x1`、2→`0x3`、4→`0xF`、8→`0xFF`），或统一在「全部 28 逻辑核」口径下比较并单独说明。
- Criterion 打印的 `change` 百分比只在**同一掩码口径内**有意义；跨口径比较必须看绝对区间。第 4 节里 `run_001` 的 +117% 就是跨口径产物，不是性能回退。

---

## 4. 与历史基线的关系

`target/criterion` 里 21:03 那次运行留下的估计值反解为 **1.8705 s**（由 `new=4.0605`、`change=+117.09%` 反推）。该轮的原始 `sample.json` 已被 Criterion 在后续运行中覆盖，无法直接核对。

它与本次 P-core 绑定基线 1.9293 s 相差 3.1%，落在同一量级，因此判定：**1.87 s 是一次正常 P-core 运行，而非「更快的旧版本」。** 上游代码从未被修改。

---

## 5. A 阶段验收对照

| 验收标准 | 状态 | 证据 |
| --- | --- | --- |
| 官方测试成功记录 | ✅ | 173 passed / 0 failed / 1 ignored，exit 0 |
| 完整源码与环境版本记录 | ✅ | `docs/environment/orchard_environment.md`（含 Commit SHA、Cargo.lock SHA256） |
| 至少三轮单 Action 证明结果 | ✅ | 未绑定 4 轮 + P-core 3 轮 + 对照 2 轮，共 9 轮 |
| 每轮证明均通过验证 | ✅ | 基准在计时前强制 `create_proof` + `verify`，任一失败即 panic |
| 可重复执行的 Benchmark 命令 | ✅ | `scripts/benchmark/run_orchard_k11_baseline.ps1`、`run_orchard_k11_pinned.ps1` |

---

## 6. 仍未覆盖（留给阶段 D）

- **验证成本**：`orchard_k11_prover` 只测证明生成，`verify` 只在计时外跑一次做正确性校验。证明验证耗时需用 `benches/circuit.rs` 或自建 harness 单独测。
- **证明大小**：本次未采集。
- **内存**：Peak Working Set / Private Bytes 均未采集。
- **多 Action**：未涉及，`benches/circuit.rs` 的 1–4 Output 配置尚待跑通并按同一 P-core 口径冻结。
- 电路版本只覆盖 `FixedPostNu6_2`，`PostNu6_3` 与 `InsecurePreNu6_2` 未测。
