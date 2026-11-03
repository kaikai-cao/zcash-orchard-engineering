# 端到端实验协议（冻结于 2026-11-03）

任何 Orchard 交易级数字必须按本协议产出，否则不与已冻结的结果比较。

## 1. 硬性前置

| 项 | 要求 |
|---|---|
| 上游 commit | `616a669d`，工作树 clean，`--locked` |
| 工具链 | rustc 1.88.0 **msvc**（机器默认是 gnu，会链接失败） |
| 构建 | `--release`，`features = ["circuit","multicore"]` |
| 依赖版本 | harness 的 `Cargo.lock` 由上游 `Cargo.lock` 复制而来；不得单独 `cargo update` |
| 亲和性 | 必须在进程内 `SetProcessAffinityMask`，**且** `RAYON_NUM_THREADS` 同步设置 |

## 2. 为什么必须绑核，以及掩码怎么给

不绑定时本机的证明耗时是绑 P 核的 2.15×——后台进程被 Windows 调度到 E-core。任何只改 `RAYON_NUM_THREADS` 的扩展实验，测的都是调度策略。

掩码必须按**物理核**进位，不能连续取位：

| 线程 | 掩码 | 说明 |
|---:|---|---|
| 1 | `0x1` | CPU 0 |
| 2 | `0x5` | CPU 0,2 |
| 4 | `0x55` | CPU 0,2,4,6 |
| 8 | `0x5555` | 8 个 P 核各取一个硬件线程 |
| 16 | `0xFFFF` | 8 核 × SMT2 |
| 20 | `0xFFFFF` | 16 个 P 逻辑核 + 4 个 E 核，**会产生长尾离群值** |

线程数上限建议 8。超过 8 只测到 SMT 争用和 E 核 straggler。

## 3. 阶段划分（不得合并）

```
fixture      note / MerklePath / anchor 构造       —— 单独计时，不计入交易成本
keygen       ProvingKey::build(K=11)              —— 冷启动一次性，单独报告
build        Builder::new + add_spend/add_output + build()
prove        Bundle::create_proof(&pk, rng)       —— 核心指标
sign         apply_signatures(sighash, &[ask])
verify       verify_proof(&vk)
size         proof 字节数 + 协议布局算出的 bundle 字节数
mem          PeakWorkingSetSize / 证明前后 WorkingSet
```

`Builder::build()` 不生成证明；`create_proof` 是独立调用。合并计时会隐藏归因。

## 4. 有效性判据

一次测量只有同时满足下列条件才算数：

1. `verify_proof` 返回 `Ok`；
2. `proof.len() == Proof::expected_proof_size(actions)`；
3. `affinity` 字段回读值与请求值一致（harness 会校验 `GetProcessAffinityMask`）；
4. 同一配置至少 3 次重复；单线程配置的 RSD < 2%；
5. 运行期间无编译任务或其他重负载进程。

## 5. 记录位置

```
results/transaction_e2e/raw/<run_id>.log        原始输出（含每条 STAGE 行）
results/transaction_e2e/analysis/e2e_runs.csv   汇总（collect_e2e.py 生成）
results/transaction_e2e/analysis/report_*.md    分析报告
```

`run_id` 命名：`e2e_<version>_<spends>s<outputs>o_t<threads>`。

## 6. 已知的计时常量

- 证明耗时抖动：单线程 5 次重复 RSD 0.7%（3.189–3.242 s），可以直接用均值。
- 多线程（≥8）需更多重复：20 线程观察到 0.671 / 0.696 / 1.717 s 的离群，均值无意义，应报中位数或分布。
