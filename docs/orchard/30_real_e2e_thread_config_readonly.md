# 30. 真实环境并行配置 · 只读确认（未跑实验）

**状态**：只读确认（2026-11-06 15:11–15:17）。**没有重跑任何交易或测量，没有改 CPU affinity，
没有设置任何环境变量，没有重启服务，没有改任何脚本，没有改 doc 26 / 27 / 28 / 29、
INDEX / README 与 `results/real_e2e_baseline/` 下的任何既有文件。**

**要回答的唯一问题**：在 `taskset -pc 0` 且**未设置** `RAYON_NUM_THREADS` 的正式 20 轮批次里，
真实 `zallet-zaino` 到底创建/使用了多少并行 worker？这个配置能不能叫「1-thread baseline」？

**证据等级沿用本阶段口径**：E1-S 源码/二进制静态证据、E1-M 现场 `/proc` 测量、
E2 派生、E3 机制推断、E4 无数据。

---

## 0. 结论先行：`taskset -pc 0` 不等于 1 线程，且连「全进程 CPU 0」都不是

| # | 结论 | 等级 |
| --- | --- | --- |
| C1 | `RAYON_NUM_THREADS`（以及 `RAYON_RS_NUM_CPUS` / TOKIO / OMP 等线程类变量）在真实 wallet 进程里**确实未设置** | E1-M |
| C2 | 二进制静态链接 `rayon 1.12.0` / `rayon-core 1.13.0` / `num_cpus 1.17.0`，含全局池符号与 `RAYON_NUM_THREADS` 字面量；未设置该变量时回退到 `std::thread::available_parallelism()` | E1-S |
| C3 | 进程启动后 0.24 s 内一次性创建 **28 个 worker 线程**（= `nproc` = 28 = 当时可见 CPU 数），全部存活到本次确认，affinity 均为 `0-27` | E1-M + E2 |
| C4 | `taskset -pc 0 <pid>` **不带 `-a`，只把主线程钉到 CPU 0**；其余 57 个线程仍是 `0-27` | E1-M |
| C5 | 因此正式 20 轮**不是** 1-thread baseline；「CPU-affinity=0」也只在主线程成立 | E1-M + E2 |

C3 对「池初始化时刻」不敏感：池创建于 11:39:49.78（G0 的 taskset 在 11:40:50 之前已执行完毕的那次门禁里才发生），
当时全部线程可见 28 个 CPU；即使假设池是在 `pczt_prove` 里、由一个未绑定的 tokio worker 线程首次触发，
`available_parallelism()` 读到的仍是该线程自己的 `0-27` → 仍是 28 个 worker。两种路径结论一致。

> **口径修正建议（不改写已审文本）**：冻结 20 轮应标为
> **「主线程 CPU-affinity=0（非全线程）、`RAYON_NUM_THREADS` 未设置、启动期 28-worker 全局池」的真实 PCZT 基线**。
> 禁止称「真实 1-thread baseline」；单独写「CPU-affinity=0 基线」时必须加主线程限定。

---

## 1. 现场证据（全部只读）

### 1.1 进程与二进制身份

```
Ubuntu（注意：wsl.exe 默认发行版是 docker-desktop，真实环境在 -d Ubuntu）
zebrad         pid 1940  STARTED 2026-11-06 11:39:43
zallet-zaino   pid 2255  STARTED 2026-11-06 11:39:49
zallet-zaino SHA256 = e602e4a3d4e919c155b8f05dae29f0b9311631f167eaf25455166edf268208a7  （与 meta.json 冻结值一致）
file: ELF 64-bit LSB pie, static-pie linked, not stripped
nproc = 28 ; os.sched_getaffinity(0) = [0..27]
```

### 1.2 环境变量（E1-M）

```
/proc/2255/environ → 变量名里含 RAYON|THREAD|TOKIO|OMP|NUMEXPR|MKL|BLAS 的项：(无)
```

G0-D 的冻结输出也印证了设置前后：`pid 2255's current affinity list: 0-27` → `new affinity list: 0`。

### 1.3 二进制与 rayon-core 源码（E1-S）

`strings -a zallet-zaino` 命中：

```
/usr/local/cargo/registry/src/.../rayon-1.12.0/src/iter/...
/usr/local/cargo/registry/src/.../rayon-core-1.13.0/src/registry.rs
/usr/local/cargo/registry/src/.../num_cpus-1.17.0/src/linux.rs
RAYON_NUM_THREADS   RAYON_RS_NUM_CPUS   GlobalPoolAlreadyInitialized
_ZN10rayon_core8registry12THE_REGISTRY... ; _ZN10rayon_core19current_num_threads...
```

本机 `rayon-core-1.13.0/src/lib.rs` 第 458–486 行：

```rust
fn get_num_threads(&self) -> usize {
    if self.num_threads > 0 { self.num_threads } else {
        let default = || thread::available_parallelism().map(|n| n.get()).unwrap_or(1);
        match env::var("RAYON_NUM_THREADS") ... { Some(x @ 1..) => return x, Some(0) => return default(), _ => {} }
        match env::var("RAYON_RS_NUM_CPUS") ... { Some(x @ 1..) => x, _ => default() }
    }
}
```

第 489–492 行 `get_thread_name()` 默认返回 `None` → rayon worker 不设置线程名，
`/proc/<tid>/comm` **继承创建者线程名**。这解释了下面 28 个同名线程。

### 1.4 线程现场（E1-M，以 `/proc/2255/task/<tid>/status|stat|comm` 只读采集）

| 组 | 数量 | `comm` | `starttime`（自开机 tick） | 换算墙钟 | `Cpus_allowed_list` |
| --- | ---: | --- | ---: | --- | --- |
| 主线程 | 1 | `zallet-zaino` | 118154 | 11:39:49.540 | **`0`** |
| tokio worker | 24 | `tokio-worker-1..23, 27` | 118155 | 11:39:49.550 | `0-27` |
| **一次性 burst** | **28** | `tokio-worker-24`（同名 ×28，继承） | 118178–118179 | **11:39:49.780–.790** | `0-27` |
| 后续零散线程 | 5 | `tokio-worker-39/41/47` | > 1 195 157 | ≥ 14:39（批次之后） | `0-27` |

- 15:16 快照共 **58 线程**：`Cpus_allowed_list=0` 的只有 **1 个**（主线程），其余 **57 个**都是 `0-27`。
- 按 `starttime` 重建，**正式 20 轮（11:43–11:47）期间至少 53 个线程**：1 主线程 + 24 tokio + 28 burst。
  批次之后新增的 5 个线程不参与本结论。
- G0-D 的 PASS 判据读的是 `/proc/PID/status`（即主线程），**判定本身为真，但覆盖面只有主线程**。

### 1.5 `taskset -p` 作用范围（E1-M 机制复核）

用一个与真实环境完全无关的、一次性 4 线程 Python 进程验证（退出即消失）：

```
BEFORE  {'9759': '0-27', '9760': '0-27', '9761': '0-27', '9762': '0-27'}
taskset -pc 0 9759   →  rc=0, "new affinity list: 0"
AFTER   {'9759': '0',   '9760': '0-27', '9761': '0-27', '9762': '0-27'}
```

util-linux `taskset` 的 `-a/--all-tasks` 才覆盖全部线程；`g0_gate_errata1.py:118` 调用的是
`["taskset", "-pc", CPU_MASK, str(pid)]`，**没有 `-a`**。

### 1.6 28 个 burst 线程的身份（E3，附稳健性说明）

最合理解释是 rayon 全局池的 28 个 worker：数量恰好等于 `available_parallelism()`、
启动期一次性成批创建、默认不设线程名因而继承创建者 `tokio-worker-24` 的 `comm`、
且二进制内含 `THE_REGISTRY` / `WorkerThread` 等符号。未使用调试器 attach 读取
`rayon_core::current_num_threads()`（那属于侵入式操作，本次不做），故身份标 **E3**。

**但结论不依赖这个身份判定**：无论这 28 个是 rayon worker 还是另一个并行池，
「存在 28 个可在 28 个 CPU 上运行的后台 worker、且只有主线程被钉住」这一 E1-M 事实不变，
「不是 1 线程、不是全进程单核」的结论不变。

---

## 2. 对既有材料的影响（记录层，不是数据层）

| 对象 | 影响 |
| --- | --- |
| 20 轮时间 / RSS / 门禁数值、`prove/build` 94.94% / 95.35%、R3 判定 | **不变**。本次只读确认没有重新测量任何性能量 |
| doc 29 §11.2「两个 PID 共用同一固定核」、§15.3(4)、G0-D 的标签 | **记录层口径偏差**：只对主线程成立。按「不悄悄改写已审文本」原则，建议以后以**追加勘误（§21）**方式补注，本文不代改 |
| doc 26 / 27、INDEX、README、tag `f4a-closed-2026-11-05` | **未动** |
| 四条既有边界（同会话配对、跨会话绝对毫秒不比较、95% CI 配对口径、1487/621/806 不混用） | 继续遵守；本文不含任何跨会话毫秒比较 |

---

## 3. 给两个待决问题的输入（**只给依据，不做决定、不执行**）

### 3.1 真实环境是否补一批明确 `RAYON_NUM_THREADS=1` 的基线

- **本地不需要补点**：F4-A 的 **1P 配置本来就是** `--mask 0x1`（进程内
  `SetProcessAffinityMask`，启动即生效、早于任何 rayon 线程创建）**+ `RAYON_NUM_THREADS=1`**
  （`results/transaction_e2e/baseline/environment.md` §2、`docs/experiments/e2e_protocol.md` §1），
  doc 25 / doc 26 §11.1 已有 **20 对同进程交错 A/B** 的 1P 结果，且本地 `rayon` 同为 1.12.0。
  再跑一个「CPU0 + Rayon1 本地对照点」是重复劳动。
- **真实环境如果将来要跑 1-worker 基线**，必须是**独立新批次 + 独立预注册 + 独立目录**，
  且至少满足三点：
  1. `RAYON_NUM_THREADS=1` 必须在**进程启动前**注入到启动环境（池在启动 0.24 s 内已建好，
     事后 `export` / RPC 内改 env 无效）；
  2. 绑核必须覆盖全线程：`taskset -a -pc 0 <pid>`，或直接在启动命令外层用 `taskset -c 0` 包裹
     （这样所有新生线程继承单核掩码）；只有全线程 pin 时 `available_parallelism()` 才会返回 1，
     tokio 运行时规模也随之收缩；
  3. G0 必须**逐线程回读** `/proc/<pid>/task/<tid>/status` 的 `Cpus_allowed_list` 并报告线程数，
     不能只读 `/proc/PID/status`。
- **代价与边界**：改启动脚本等于改「现有官方预编译环境」，与 doc 29 §11.2「不主动改环境」的
  冻结边界冲突，需要用户显式批准；而路线 A 的三类允许结论（真实交易时间组成、prove 占比、
  RSS 可观测性 / R3）都是**单臂描述性**结果，不依赖 1-worker 假设。是否值得新开一批，
  取决于是否要为将来与本地对齐预置环境，而不是为了修正已冻结的 20 轮。

### 3.2 统一线程数能带来什么、不能带来什么

统一到 `CPU 0 + Rayon 1` 的价值是**减少一个重要混杂变量**；即使做到，仍存在：
`halo2_proofs 0.4.0` vs `0.3.2`、Orchard 版本不同、Windows 全进程 pin vs Linux 仅主线程 pin、
CPU 拓扑不同、harness `create_proof` vs RPC `pczt_prove`、真实交易额外 RPC / 序列化 / 钱包逻辑。
因此**仍然不能**说「本地快 22%，真实交易一定快 22%」，**也不能**把 doc 26 的 −22% 乘到 RPC 总时间，
更不能跨会话比较绝对毫秒。

---

## 4. 本次未做（边界清单）

- 未重跑 G0 / warm-up / 正式 20 轮，未发任何交易，未挖块，未改链状态；
- 未对 1940 / 2255 执行任何写操作的 `taskset`（`taskset -pc <pid>` 不带掩码为只读回读）；
- 未设置 / 注入任何环境变量，未重启服务，未改配置或脚本；
- 未修改 doc 26 / 27 / 28 / 29、INDEX、README、`results/real_e2e_baseline/**` 的既有字节；
- 未把本文挂进 INDEX / README（方向未定，沿用 doc 28 的做法）；
- 唯一新起进程是 §1.5 那个一次性 4 线程 Python 机制验证进程，不接触真实环境、退出即消失；
- 取证命令均为只读：`/proc` 读取、`ps`、`strings` / `file` / `sha256sum`、
  `taskset -pc <pid>` 回读、本机 rayon-core 源码读取。
