# F4-A 探针汇总（自动生成）

日志：`probe_on.log`　probe=True　证明 7264 B / `0xa6bcd171d69e969a`

## 1. 结构总量

| 项 | 值 |
|---|---:|
| 链 | 50 |
| 其中 occ ≥ 2（可共享） | 39 |
| 其中 occ = 1（无收益） | 11 |
| 消费者总数 | 181 |
| Σ(k−1) | 242 |
| Σ occ·(k−1) | 869 |
| 可省整块乘 | **627** |
| 根位置可直接提取的消费者 | 179 / 181 （98.9%） |

k 分布：k=4 × 12 条，k=5 × 5 条，k=6 × 12 条，k=7 × 21 条

## 2. 前 12 条链（按可省乘法排序）

| # | 列 | k | occ | 可省 | 根位置 | 来源门 |
|---:|---:|---:|---:|---:|---:|---|
| 1 | 25 | 6 | 14 | 65 | 14 | CommitIvk canonicity check |
| 2 | 27 | 7 | 7 | 36 | 7 | y coordinate checks |
| 3 | 28 | 7 | 7 | 36 | 7 | y coordinate checks |
| 4 | 19 | 4 | 12 | 33 | 12 | complete addition |
| 5 | 18 | 6 | 6 | 25 | 6 | q_mul_2 == 1 checks |
| 6 | 26 | 7 | 5 | 24 | 5 | NoteCommit input g_d |
| 7 | 27 | 7 | 5 | 24 | 5 | NoteCommit input psi |
| 8 | 28 | 7 | 5 | 24 | 5 | NoteCommit input g_d |
| 9 | 28 | 7 | 5 | 24 | 5 | NoteCommit input psi |
| 10 | 21 | 4 | 8 | 21 | 8 | Canonicity checks |
| 11 | 20 | 5 | 6 | 20 | 6 | q_mul_2 == 1 checks |
| 12 | 26 | 7 | 4 | 18 | 4 | Decomposition check |

## 3. 复用代价（本轮实测）

| 内核 | n | ns/元素 | 中位 ns |
|---|---:|---:|---:|
| in_place | 4096 | 12.0605 | 49400 |
| in_place | 16384 | 12.1582 | 199200 |
| fused | 4096 | 12.3291 | 50500 |
| fused | 16384 | 12.3352 | 202100 |
| reused | 4096 | 12.1338 | 49700 |
| reused | 16384 | 12.2986 | 201500 |

Δ(fused − in_place)：n=4096 时 **+0.2686** ns/元素，n=16384 时 **+0.1770** ns/元素。

## 4. 成本模型

- 毛节省 = 627 × 0.1861 ms = **116.7 ms = 4.28%**（H6 冻结锚点，外推）
- 同口径用本轮内核价（199200 ns/整块乘）= 124.9 ms = 4.58%（上界，二者相差 +7.1%）
- 复用代价 = 170 个共享链消费者 × 16384 元素 × +0.2686 ns/chunk 几何 = **0.75 ms**（若改用 16384 几何的 Δ=+0.1770，则为 0.49 ms）
- 净收益 = **115.9 ms = 4.25%** of 2725.8 ms

## 5. 闭合检查

- PASS　occ_muls == F4 的 869（压缩替身总数）
- PASS　saved + Σ(k-1) == occ_muls
- PASS　Σ(k-1) == 242（F4 候选 A 的差额）
- PASS　consumers == 181
- PASS　identity_fail == 0
- PASS　domains == 1
- PASS　proof 7264 B
- PASS　proof 指纹 0xa6bcd171d69e969a

派生闭合：Σ occ·k = 869 + 181 = 1050；F4 的 fixed 增量是 1062，差 12 = 2 个 k=1 单例 selector 的出现次数（不可由结构直接计数）。

非根位置的消费者：列19 / witness point / poly 0，列19 / witness point / poly 1
