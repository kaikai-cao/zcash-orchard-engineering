# Vendored `halo2_proofs` 0.4.0（Stage E2/E3 + F2 插桩）

本目录保存一份带测量插桩的 `halo2_proofs` 0.4.0 副本。放在仓库里是为了让实验可复现：
`harness/Cargo.toml` 通过 `[patch.crates-io]` 直接指向它，删掉这个目录 harness 就编译不过。

上游来源：crates.io `halo2_proofs 0.4.0`（`Cargo.lock` 锁定版本，未升级）。

## 与上游的差异

共 **6 个文件，+855 / −11** 行。完整差异见 `halo2_proofs-0.4.0-instrumentation.patch`
（相对仓库根可用 `git apply` 应用；`git apply --check` 已验证可干净套到原版 0.4.0 上）。

```
src/f2.rs               +616 /   -0   新增：F2 计数器 / 解析式遍历 / JSON 报告
src/lib.rs                +2 /   -0   导出 `pub mod f2;`
src/plonk.rs              +7 /   -0   `cs_f2()`：把 ConstraintSystem 借给 f2
src/plonk/keygen.rs       +4 /   -6   keygen_vk 末尾触发一次 F2 分析
src/plonk/prover.rs     +125 /   -5   E2 阶段计时 + E3 阶段标签 + F2 三段计数包装
src/poly/evaluator.rs   +101 /   -0   `evaluate` 变成门控包装 + `f2_ast_stats` 遍历
```

改动只有测量，**不改任何约束、算法、数据结构或求值结果**：所有开关默认关闭时
`evaluate` 直接转到 `evaluate_inner`（原函数体原样改名），prover 的九个阶段计时
只多几次 `Instant::now()`。

## 三类开关

| 开关 | 归属 | 作用 | 默认 |
| --- | --- | --- | --- |
| `ORCHARD_E2_TIMING=1` | E2 | 打开 9 个连续不重叠阶段的墙钟计时，退出时打印 `E2_REPORT` | 关 |
| `E3_STAGE`（无条件） | E3 | 把「当前处于哪个阶段」写进 `AtomicU8`，供采样线程给栈样本打阶段标签；单次 store，可忽略 | 常开 |
| `ORCHARD_F2=1` | F2 | 在 keygen 后遍历约束系统、在 `evaluate` 前后统计 AST 并重复计时；退出时打印 `F2_REPORT` / `F2_META` | 关 |

`ORCHARD_F2_BENCH=n` 配合 `ORCHARD_F2=1` 使用，控制 evaluator 单元重复计时次数
（E2 的 `ORCHARD_E2_TIMING` 与它独立）。

E2 与 E3 共用同一套阶段定义：

| code | 阶段 | code | 阶段 |
| --- | --- | --- | --- |
| 1 | `instance` | 6 | `permutation` |
| 2 | `synthesis` | 7 | `lookup_product` |
| 3 | `advice_commit` | 8 | `quotient` |
| 4 | `eval_setup` | 9 | `opening` |
| 5 | `lookup_permute` | 0 | 不在证明中（Keygen / 空闲） |

各阶段以 `Instant::now()` 起点 + `e2_add(stage, t.elapsed())` 收尾，区间首尾相接、互不嵌套，
因此**阶段耗时可以直接相加**，不会重复计账（实测：阶段和 3288 ms vs 外层 `prove_s` 3291 ms）。

## 四条设计约束

1. **计时路径里不放计数器。** `ORCHARD_E2_TIMING` 计时的那一段代码里没有任何原子操作、
   HashMap 或日志，否则测到的就不是生产路径。F2 的计数与计时分属两个开关，
   报告里给出的耗时来自 `ORCHARD_F2_BENCH` 的重复计时，不是带计数器的版本。
2. **计数必须是解析式的，不是热循环累加。** F2 的「域运算次数」不是把 `Fp::mul` 加上
   一个全局原子计数器（那会引入 1.076 亿次原子写，比被测对象还贵），而是
   **节点数 × 多项式长度**：先对 AST 做一趟遍历得到每类节点数，再乘上
   `poly_len`（16384）。域运算次数因此是**算出来的**，误差只来自「节点数」这一层，
   而节点数本身可以对账（见下）。
3. **全局量只在串行单证明时有意义。** `E3_STAGE` 是进程级 `AtomicU8`，
   `F2_REPORT` 的累加量同理——1P/8P 各自独立进程、不并发多证明，才有意义；
   多线程并行只影响 `evaluate` 内部的 chunk 并行，不影响计数总量。
4. **不改语义。** 插桩不得改变任何求值结果：`f2::enabled() == false` 时
   `evaluate` 与原版逐字节等价。这一点靠三重对账保证——门数、表达式节点数与
   整块乘法数都能从两个独立来源（表达式层统计 / prover 分段自数）互相验证，
   实测 gate 段 2848/2924 逐位相符（见 `docs/orchard/13_expression_evaluator_analysis.md`）。

## 为什么用插桩而不是 profiler 直接归因

Windows 上 ETW/WPR 需要管理员权限（本机被拒绝，`0xc5585011`），VTune / samply / perf 均不可用。
自研采样器只能拿到 PC 栈，无法回答「这个样本属于 Halo2 的哪个阶段」——
所以在 prover 里放一个阶段码，让每个栈样本自带标签。详见 `docs/orchard/10_cpu_sampling_profile.md`。
F2 更进一步：采样只能给出**占比**，回答不了「一共调用了多少次域乘法」，
所以需要在表达式层做解析式计数，两条证据再互相交叉验证（33.1% vs 33.08%）。

## 三个补丁文件（按应用顺序）

| 补丁 | 相对谁 | 用途 | 是否常驻 |
| --- | --- | --- | --- |
| `halo2_proofs-0.4.0-instrumentation.patch` | 原版 0.4.0 | E2 阶段计时 + E3 阶段标签 + F2 计数 | **是**（本目录就是它的产物） |
| `halo2_proofs-0.4.0-h6-neg.patch` | 本目录 | H6：`Ast::Neg` 取代 `Scale(-1)`（+255 / −13） | 否，只在测 B 侧时应用，测完 `git checkout` 还原 |
| `halo2_proofs-0.4.0-f3-lc-probe.patch` | **应用了 H6 之后** | LC 叶子普查探针（新增 `src/f3lc.rs` + `evaluate` 前的一次只读遍历） | 否，只在测 LC 时应用 |

后两个补丁都是"基线 + 补丁"的单因子形式：vendor 目录本身始终保持**未打补丁**的冻结状态，
`f0_verify_frozen.py` 因此永远能对上 `src/**/*.rs` 的 58 个文件指纹。
`f3-lc-probe` 依赖 `h6-neg`（普查要处理 `Ast::Neg` 分支），必须按上表顺序应用。

## 注意

- 这份副本仅服务测量，**不是**要提交给上游的补丁，也不应用于生产构建。- 冻结指纹（git commit、`Cargo.lock` sha256、依赖指纹、编译参数）见
  `results/transaction_e2e/f0_freeze/manifest.json` 与 `docs/orchard/11_stage_f_scope.md`。
  vendor 源码指纹覆盖 `src/**/*.rs` 全部 **58 个文件**（57 个上游文件 + `f2.rs`），
  用 `python scripts/analysis/f0_verify_frozen.py` 校验（exit 0 = 全部匹配）。
- 本目录下的 `.rs` 由 `.gitattributes` 统一钉成 LF；Windows 上 `core.autocrlf=true`
  时若不钉，clone 出来的字节与指纹不一致。
