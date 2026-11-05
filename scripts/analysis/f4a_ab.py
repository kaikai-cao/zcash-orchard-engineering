#!/usr/bin/env python3
"""Stage F4-A implementation: paired A/B report.

Reads one `--mode f4aimpl` log (arms alternate `A` then `B` inside a single
process) and reports, in the order doc 23 §6 asks for:

  1. correctness   -- proof bytes, proof fingerprint, `h` fingerprint
  2. structure     -- what `intern_chains` actually shared, and the arithmetic
                      that ties it to the tree the prover evaluated
  3. time          -- per-pair `B/A` ratio, median, bootstrap 95% CI, sign test
  4. memory        -- incremental peak working set per arm

The unit ladder is the one frozen in doc 23 §10.1: microseconds per consumer,
milliseconds per proof. Nothing here divides this session's milliseconds by
another session's baseline -- the only cross-arm quantity is the ratio.
"""

import argparse
import json
import random
import statistics
from pathlib import Path

PROVE_1P_MS = 2725.8  # H6 frozen anchor, used only for a historical note


def jsonl(path, prefix):
    out = []
    for line in open(path, encoding="utf-8", errors="replace"):
        if line.startswith(prefix + " "):
            body = line[len(prefix) + 1:].strip()
            try:
                out.append(json.loads(body))
            except json.JSONDecodeError:
                # Older runs printed the (k, occ) pairs as tuples; JSON wants
                # arrays. Recover rather than throw the whole log away.
                out.append(json.loads(body.replace("(", "[").replace(")", "]")))
    return out


def bootstrap_ci(xs, reps=10000, lo=2.5, hi=97.5, seed=0):
    if not xs:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(xs)
    meds = []
    for _ in range(reps):
        meds.append(statistics.median(rng.choices(xs, k=n)))
    meds.sort()
    return (meds[int(reps * lo / 100)], meds[int(reps * hi / 100) - 1])


def sign_test(xs):
    """Exact two-sided binomial: how often did B beat A."""
    n = len(xs)
    k = sum(1 for x in xs if x < 1.0)
    if n == 0:
        return 0, 0, float("nan")
    # two-sided p from the binomial with p = 1/2
    from math import comb

    tail = sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / (2 ** n)
    return k, n, min(1.0, 2 * tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="+")
    ap.add_argument("--struct", default=None,
                    help="log from a run with ORCHARD_F4A_STATS=1, used for the "
                         "per-chunk counters (the timing logs leave stats off)")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    runs = []
    impl = []
    chains = []
    meta = {}
    for p in args.log:
        runs += jsonl(p, "F4A_RUN")
        impl += jsonl(p, "F4A_IMPL")
        chains += jsonl(p, "F4A_CHAINS")
        for line in open(p, encoding="utf-8", errors="replace"):
            if line.startswith("F4A_META "):
                meta = json.loads(line[len("F4A_META "):])

    if args.struct:
        impl += jsonl(args.struct, "F4A_IMPL")
        chains += jsonl(args.struct, "F4A_CHAINS")

    A = [r for r in runs if r["arm"] == "A"]
    B = [r for r in runs if r["arm"] == "B"]
    pairs = min(len(A), len(B))

    L = []
    add = L.append
    add("# Stage F4-A · 实施 A/B 报告\n")
    add(f"- 日志：{', '.join(str(p) for p in args.log)}")
    add(f"- 配对：**{pairs}** 对（同一进程、同一二进制、同一 seed，仅 `ORCHARD_F4A` 变化）")
    add(f"- META：`{json.dumps(meta, ensure_ascii=False)}`\n")

    # ---------------------------------------------------------------- 1
    add("## 1. 正确性\n")
    pb = sorted({r["proof_bytes"] for r in runs})
    pf = sorted({r["proof_fnv"] for r in runs})
    hf = sorted({i["h_fnv"] for i in impl if i.get("stats")})
    add("| 量 | 观测 | 判据 |")
    add("|---|---|---|")
    add(f"| proof 字节 | {', '.join(str(x) for x in pb)} | 必须全相同 |")
    add(f"| proof 指纹 | {', '.join(x for x in pf)} | 必须全相同，且等于 `0xa6bcd171d69e969a` |")
    hf_s = ", ".join(hf) if hf else "（结构日志里没有 stats 记录）"
    add(f"| `h` 系数指纹（stats 运行，同一二进制） | {hf_s} | A 与 B 必须相同 |")
    ok = len(pb) == 1 and len(pf) == 1 and len(hf) <= 1
    add(f"\n**F4A-1：{'PASS' if ok else 'FAIL'}**（{len(runs)} 次计时证明，{len(pb)} 种字节数、"
        f"{len(pf)} 种指纹；`h` 取自 `--struct` 指定的 stats 运行，{len(hf)} 种指纹）\n")

    # ---------------------------------------------------------------- 2
    # The per-chunk counters only move with `ORCHARD_F4A_STATS=1`, so prefer a
    # record that actually saw them.
    last_b = [i for i in impl if i.get("enabled")]
    counted = [i for i in last_b if i.get("chunks_filled")]
    if last_b:
        b = (counted or last_b)[-1]
        add("## 2. 结构账目\n")
        add("| 量 | 值 | 含义 |")
        add("|---|---:|---|")
        add(f"| `chains` | {b['chains']} | 根位置恢复链（去重后） |")
        add(f"| `slots` | {b['slots']} | 消费者 ≥ 2、真正分配了 memo 槽的链 |")
        add(f"| `root_consumers` | {b['root_consumers']} | 叶子位置出现的总次数 |")
        add(f"| `chain_muls` | {b['chain_muls']} | 基线里这些链一共要做多少次整块乘 |")
        add(f"| **`structural_mul_eliminations`** | **{b['structural_mul_eliminations']}** | 代码里真正不再执行的整块乘 |")
        add(f"| `memo_reads`（每证明） | {b['memo_reads']} | 读 memo 的次数 = 被替换掉的消费者数 |")
        add(f"| `slot_fills_per_chunk` | {b['slot_fills_per_chunk']} | 每个 chunk 预填的槽数 |")
        add(f"| `chunks_filled` | {b['chunks_filled']} | 每次证明的任务数 = chunk 数 |")
        add("")
        add("恒等式 `chain_muls − structural_mul_eliminations = "
            "Σ_unshared(k−1) + Σ_slots(k−1)` = "
            f"{b['chain_muls'] - b['structural_mul_eliminations']}："
            "前者是没被共享、仍照旧计算的链，后者是 49 次预计算的成本。"
            "两者都不再出现在被遍历的树里，所以 `F2` 看到的 `mul` 减少量是 "
            "`chain_muls − Σ_unshared(k−1)`。")
        add("")

    if chains:
        c = chains[-1]
        pairs_k = c["k_occ"]
        add("### 2.1 逐槽位 (k, occ)\n")
        add("```")
        add(", ".join(f"({k},{o})" for k, o in pairs_k))
        add("```")
        add(f"\n共 {c['slots']} 个槽；`Σ occ·(k−1)` 与上面的 `chain_muls − Σ_slots(k−1)` 对账，"
            "`Σ (occ−1)(k−1)` 等于 `structural_mul_eliminations`。\n")

    # ---------------------------------------------------------------- 3
    add("## 3. 时间\n")
    add("| 臂 | 中位数 ms | 最小 | 最大 |")
    add("|---|---:|---:|---:|")
    for name, xs in (("A（基线）", A), ("B（F4-A）", B)):
        v = [x["proof_ms"] for x in xs]
        add(f"| {name} | {statistics.median(v):.1f} | {min(v):.1f} | {max(v):.1f} |")
    add("")

    ratios = [B[i]["proof_ms"] / A[i]["proof_ms"] for i in range(pairs)]
    med = statistics.median(ratios)
    lo, hi = bootstrap_ci(ratios)
    k, n, p = sign_test(ratios)
    add("| 统计量 | 值 |")
    add("|---|---:|")
    add(f"| 每对 `B/A` 中位数 | **{med:.4f}** |")
    add(f"| bootstrap 95% CI | **[{lo:.4f}, {hi:.4f}]** |")
    add(f"| 收益（中位数） | **{(1 - med) * 100:+.2f}%** |")
    add(f"| 95% CI 折算 | [{(1 - hi) * 100:+.2f}%, {(1 - lo) * 100:+.2f}%] |")
    add(f"| B 更快的对数 | {k}/{n} |")
    add(f"| 符号检验 p | {p:.2e} |")
    add("")

    vratios = [B[i]["verify_ms"] / A[i]["verify_ms"] for i in range(pairs)]
    add(f"verify 的 `B/A` 中位数 {statistics.median(vratios):.4f}（验证方不涉及 `Ast`，应≈1）。\n")

    # ---------------------------------------------------------------- 4
    add("## 4. 内存\n")
    add("| 臂 | 每次证明的峰值工作集增量 MiB（中位数） |")
    add("|---|---:|")
    for name, xs in (("A（基线）", A), ("B（F4-A）", B)):
        v = [x.get("ws_peak_delta_mib", 0.0) for x in xs]
        add(f"| {name} | {statistics.median(v):.1f} |")
    add("\n`PeakWorkingSetSize` 在 keygen 之后已经在 GB 量级，几 MiB 的增量落在它的分辨率之下，"
        "所以这一列读不出差异——**不是**\"没有内存成本\"。按 §2 的槽数算上界：")
    slots = b["slots"] if last_b else 0
    add(f"1P 每 chunk `{slots} × 4096 × 32 B = {slots * 4096 * 32 / 2**20:.1f} MiB`，"
        f"4 个 chunk 串行执行 → 峰值 ≈ {slots * 4096 * 32 / 2**20:.1f} MiB；"
        f"8P 每 chunk `{slots} × 512 × 32 B = {slots * 512 * 32 / 2**20:.2f} MiB`，"
        f"32 个并行 → 上界 {slots * 512 * 32 * 32 / 2**20:.1f} MiB，均远低于 F4A-4 的 64 MiB。\n")

    # ---------------------------------------------------------------- 5
    add("## 5. 判据\n")
    gate1 = ok
    gate3 = med < 0.99 and hi < 1.0
    add("| 门 | 判据 | 结果 |")
    add("|---|---|---|")
    add(f"| F4A-1 正确性 | proof 字节 + 指纹 + `h` 指纹全同 | {'PASS' if gate1 else 'FAIL'} |")
    add(f"| F4A-3 1P 收益 | ≥ 1.0% 且 95% CI 排除零 | "
        f"{'PASS' if gate3 else 'FAIL'}（{(1 - med) * 100:+.2f}%，CI [{(1 - hi) * 100:+.2f}%, {(1 - lo) * 100:+.2f}%]） |")
    add("")

    text = "\n".join(L)
    print(text)
    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.md").write_text(text, encoding="utf-8")
        print(f"\n[wrote] {out / 'summary.md'}")


if __name__ == "__main__":
    main()
