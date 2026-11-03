# Stage E1 · Orchard V3（PostNu6_3）证明基线

> 阶段 E 的第一块：在**不改任何约束、不优化**的前提下，冻结一份可重复的真实
> `create_proof` 基线，供后续 Stage E2（分阶段计时）和 Stage F（优化对照）回溯。
>
> 数据日期：2026-11-03（GMT+8）。所有静态机器/工具链事实复用
> `docs/environment/orchard_environment.md`（阶段 A 已核对）。

---

## 1. 为什么是这份基线（与阶段 A 的区别）

| 维度 | 阶段 A 基线（已有） | 本基线（Stage E1） |
| --- | --- | --- |
| 电路版本 | `FixedPostNu6_2` | **`PostNu6_3`** |
| 形态 | 单 Action（Coinbase + 1 Output） | **Orchard V3 bundle = 2 Action**（1 spend + 1 change，cross-address 关闭） |
| 测什么 | 仅 `create_proof`（Criterion 基准） | **Keygen + `create_proof` + verify 分离计量** |
| 线程口径 | 单线程（断言 `RAYON_NUM_THREADS=1`） | **1P 与 8P 两种配置** |
| 与成本地图的关系 | 无（旧电路） | 直接对应 `08_action_cost_map.md` 的 PostNu6_3 布局 |

> 阶段 A 报告第 6 节已明确把「PostNu6_3」「多 Action」「验证/内存/证明大小」列为
> **未覆盖、留给阶段 D/E** 的项。本基线即补齐 PostNu6_3 + 多 Action + 验证/内存/大小，
> 并把 Keygen 与 Prove 分开，满足用户「不把一次性参数生成混进日常证明耗时」的要求。

---

## 2. 固定条件（复现时必须逐项满足）

| 项 | 值 |
| --- | --- |
| 上游 commit | `616a669df8c9a59c33d47064d3ce04b25d026b2e`（`0.16.0-1-g616a669`，工作树 clean） |
| 电路 | `OrchardCircuitVersion::PostNu6_3`，`K = 11`（`src/circuit.rs` 固有） |
| Bundle | Orchard V3：`Builder::new(UNPADDED, orchard_v3, 默认 flags, anchor)` + 1 `add_spend` + 1 `add_change_output` → **2 Action** |
| 跨地址检查 | 关闭（`BundleVersion::orchard_v3().default_flags()` 构造上禁止） |
| 证明随机源 / fixture 种子 | harness 默认 `seed=7`、确定性 RNG |
| 工具链 | `rustc 1.88.0` / `x86_64-pc-windows-msvc`（`rust-toolchain.toml` 锁 1.88） |
| 编译 | `--release --locked`（harness crate；依赖已缓存） |
| 锁定依赖 | `halo2_proofs 0.4.0`、`halo2_gadgets 0.6.0`、`pasta_curves 0.6.0`（以 harness `Cargo.lock` 为准） |
| 电源方案 | **平衡（Balanced）**（与阶段 A 一致；未切高性能，因基线即在此状态采集） |

### 两种线程配置（必须显式绑定 P-core）

阶段 A 已证实：**不绑定会被 Windows Thread Director 调度到 E-core，耗时翻倍**
（4.16 s 未绑定 vs 1.93 s P-core）。本基线用 harness 自带 `--mask` 亲和 + `RAYON_NUM_THREADS`
双重约束：

| 配置 | 掩码 | 生效逻辑 CPU | `RAYON_NUM_THREADS` | 含义 |
| --- | ---: | --- | ---: | --- |
| **1P** | `0x1` | 逻辑 0（第 1 个 P-core 首 SMT 线程） | `1` | 单 P-core、单 worker |
| **8P** | `0xFF` | 逻辑 0–7（8 个 P-core 首 SMT 线程） | `8` | 8 P-core、8 worker |

> 机器实测映射（见 `docs/environment/orchard_environment.md` §4.1）：逻辑 0–15 = 8 P-core 的双
> SMT 线程，逻辑 16–27 = 12 E-core。因此 `0xFF` 恰好覆盖 8 个 P-core，不含任何 E-core。

---

## 3. 结果

### 3.1 主指标（Orchard V3 · 2 Action · `create_proof`）

| 配置 | 均值 | 最小 | 最大 | 极差 RSD | 关键结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| **1P** | **3.3746 s** | 3.3325 s | 3.4828 s | 1.66% | 单线程基准 |
| **8P** | **0.9316 s** | 0.9218 s | 0.9464 s | 0.94% | 相对 1P **3.62×** 加速 |

（每轮实测见 `baseline_1p.csv` / `baseline_8p.csv`，各 5 次重复。）

### 3.2 Keygen（一次性，单独计量，不计入日常证明耗时）

| 配置 | `keygen_s` | 相对 1P |
| --- | ---: | ---: |
| 1P | 2.7373 s | 1.00× |
| 8P | 1.4644 s | 1.87× |

`ProvingKey::build(PostNu6_3)` 也并行化，但收益低于 Prove 本身（1.87× vs 3.62×）。

### 3.3 验证（`verify_proof`，Orchard V3 · 2 Action）

| 配置 | `verify_ms` 均值 | 相对 1P |
| --- | ---: | ---: |
| 1P | 30.80 ms | 1.00× |
| 8P | 8.11 ms | 3.80× |

### 3.4 内存峰值 / 证明大小

| 项 | 1P | 8P |
| --- | ---: | ---: |
| Peak working set | 115.8 MiB | 117.6 MiB |
| 证明字节数（`proof_bytes`） | 7264 | 7264（与线程无关） |
| `ws_before` → `ws_after`（Orchard，rep 0） | 44.5 → 48.2 MiB | 43.4 → 47.7 MiB |

---

## 4. 与阶段 A 基线的关系（上下文，非直接可比）

- 阶段 A：`FixedPostNu6_2` **单 Action** 单线程 = **1.93 s**（P-core 绑定）。
- 本基线：`PostNu6_3` **2 Action** 单 P-core = 3.37 s → 约 **1.69 s/Action**。
- 二者电路版本、Action 数、绑定口径都不同，**不能直接相减**。差异来源包括：
  PostNu6_3 比 FixedPostNu6_2 多了跨地址相关结构、布局重排（V1 全局 1772→1779 行）、
  以及 2 Action 间的共享证明项不随 Action 数线性增长。仅作量级参照。

---

## 5. 复现命令

```bash
cd D:\Research\zcash-orchard-engineering\experiments\transaction_e2e\harness

# 1P（单 P-core）
RAYON_NUM_THREADS=1 ./target/release/orchard-e2e --mode v3 --reps 5 --mask 0x1 --tag e1_1p \
  > ../../../results/transaction_e2e/baseline/raw/run_1p.log 2>&1

# 8P（八 P-core）
RAYON_NUM_THREADS=8 ./target/release/orchard-e2e --mode v3 --reps 5 --mask 0xFF --tag e1_8p \
  > ../../../results/transaction_e2e/baseline/raw/run_8p.log 2>&1

# 解析为 CSV
cd D:\Research\zcash-orchard-engineering
python scripts/analysis/e1_baseline_csv.py --log results/transaction_e2e/baseline/raw/run_1p.log --config 1p --out results/transaction_e2e/baseline/baseline_1p.csv
python scripts/analysis/e1_baseline_csv.py --log results/transaction_e2e/baseline/raw/run_8p.log --config 8p --out results/transaction_e2e/baseline/baseline_8p.csv
```

> 构建：`cargo +1.88 build --release --locked`（在 harness 目录内；依赖已缓存，约 13 s）。
> 若上游工作树被改动，须先还原到 `616a669d` 再跑，否则 Keygen/Prove 数值不可与本文对照。

---

## 6. 给 Stage E2 的衔接

本基线把 `create_proof` 当作**一个黑盒**计时，已经暴露一个值得追的问题：
**8P 相对 1P 只有 3.62× 加速，而非 8×**——说明 `create_proof` 内部存在无法并行或并行度低
的阶段。E2 的目标就是把 `create_proof` 拆成 synthesis / advice commitments /
permutation / lookup / quotient / IPA，定位是哪个阶段拖低了并行度与绝对占比，
再与 `08_action_cost_map.md` 的「行数占比」（Merkle 58% / NoteCommit 22%）对照，
回答「占行最多的部分是否也是最耗时的部分」。

---

## 7. 产物清单

| 文件 | 内容 |
| --- | --- |
| `baseline/environment.md`（本文件） | 固定条件 + 结果 + 复现 |
| `baseline/baseline_1p.csv` | 1P 每轮：keygen_s、build_ms、sign_ms、verify_ms、prove_s、内存、线程/亲和 |
| `baseline/baseline_8p.csv` | 8P 同上 |
| `baseline/raw/run_1p.log` | 1P 原始 stdout（含 SHAPE/BUNDLE/RESULT 机读行） |
| `baseline/raw/run_8p.log` | 8P 原始 stdout |
| `scripts/analysis/e1_baseline_csv.py` | 日志 → CSV 解析器（可随重新采集重跑） |
