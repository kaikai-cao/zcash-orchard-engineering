#!/usr/bin/env python3
"""Stage F4-A.2: resource and stage audit report.

Reads four logs from `results/transaction_e2e/f4a2_audit/` and writes
`summary.md`, in the order doc 25 §2 asks for:

  1. block one   -- memory (sampled peak, memo residency, per-chunk peak)
  2. block two   -- allocations (calls / bytes / per-chunk)
  3. block three -- the nine E2 stages, paired

Every table is paired A/B inside one process. No absolute millisecond is
compared with another session (doc 25 §5).

Usage:
    python scripts/analysis/f4a2_report.py [--dir results/transaction_e2e/f4a2_audit]
"""

import argparse
import json
import random
import statistics
from pathlib import Path

STAGES = [
    "instance",
    "synthesis",
    "advice_commit",
    "eval_setup",
    "lookup_permute",
    "permutation",
    "lookup_product",
    "quotient",
    "opening",
]


def jsonl(path, prefix):
    out = []
    if not Path(path).exists():
        return out
    for line in open(path, encoding="utf-8", errors="replace"):
        if line.startswith(prefix + " "):
            out.append(json.loads(line[len(prefix) + 1 :]))
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
    from math import comb

    n = len(xs)
    if n == 0:
        return 0, 0, float("nan")
    k = sum(1 for x in xs if x < 1.0)
    p = 2 * sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2**n
    return k, n, min(1.0, p)


def pairs(rows):
    """[(A_row, B_row)] keyed by `pair`."""
    by = {}
    for r in rows:
        by.setdefault(r["pair"], {})[r["arm"]] = r
    return [(by[k]["A"], by[k]["B"]) for k in sorted(by) if "A" in by[k] and "B" in by[k]]


def f(x, nd=2):
    return f"{x:,.{nd}f}"


def mem_block(rows, geo):
    prs = pairs(rows)
    if not prs:
        return ["_（无数据）_", ""]
    lines = [
        f"| 指标 | A（基线） | B（F4-A） | Δ（B−A） |",
        "|---|---:|---:|---:|",
    ]
    def med(fn):
        a = statistics.median([fn(x) for x, _ in prs])
        b = statistics.median([fn(y) for _, y in prs])
        return a, b, b - a

    rows_spec = [
        ("`ws_start` 证明起点工作集 MiB", lambda r: r["ws_start_mib"], 2),
        ("`ws_sampled_peak` 1 ms 采样峰值 MiB", lambda r: r["ws_peak_mib"], 2),
        ("`ws_delta` 采样峰值 − 起点 MiB", lambda r: r["ws_delta_mib"], 2),
        ("`private_sampled_peak` MiB", lambda r: r["priv_peak_mib"], 2),
        ("`private_delta` MiB", lambda r: r["priv_delta_mib"], 2),
        ("`ws_peak_kernel`（单调，仅作上界）MiB", lambda r: r["peak_ws_kernel_mib"], 2),
        ("证明窗口 ms（诊断构建，不作性能结论）", lambda r: r["window_ms"], 1),
    ]
    for label, fn, nd in rows_spec:
        a, b, d = med(fn)
        lines.append(f"| {label} | {f(a, nd)} | {f(b, nd)} | {d:+,.{nd}f} |")
    return lines, prs


def alloc_block(rows):
    prs = pairs(rows)
    if not prs:
        return ["_（无数据）_"], prs
    lines = [
        "| 指标 | A（基线） | B（F4-A） | Δ（B−A） |",
        "|---|---:|---:|---:|",
    ]
    specs = [
        ("`alloc_calls` / 证明", "alloc_calls", 0),
        ("`alloc_bytes` / 证明（GiB）", "alloc_bytes", 3),
        ("`dealloc_calls` / 证明", "dealloc_calls", 0),
        ("`memo_bytes` / 证明（MiB）", "memo_bytes", 2),
        ("`memo_bytes_max_chunk`（MiB）", "memo_bytes_max_chunk", 2),
        ("`memo_slots` / 证明", "memo_slots", 0),
        ("`chunk_peak_max`（MiB）", "chunk_peak_max_bytes", 2),
        ("`chunk_peak_mean`（MiB）", "chunk_peak_mean_bytes", 2),
        ("`chunk_allocs_mean`（次/chunk）", "chunk_allocs_mean", 0),
        ("`chunk_allocs_max`（次/chunk）", "chunk_allocs_max", 0),
        ("`chunk_count` / 证明", "chunk_count", 0),
        ("`chunk_anomalies`", "chunk_anomalies", 0),
    ]
    for label, key, nd in specs:
        a = statistics.median([x[key] for x, _ in prs])
        b = statistics.median([y[key] for _, y in prs])
        if key.endswith("_bytes") and key != "memo_bytes_max_chunk":
            a /= 1024**3 if "alloc" in key else 1024**2
            b /= 1024**3 if "alloc" in key else 1024**2
        elif key.startswith("memo_bytes") or key.startswith("chunk_peak"):
            a /= 1024**2
            b /= 1024**2
        lines.append(f"| {label} | {f(a, nd)} | {f(b, nd)} | {b - a:+,.{nd}f} |")
    return lines, prs


def stage_block(rows):
    prs = pairs(rows)
    if not prs:
        return ["_（无数据）_"], prs, None
    d_prove = [y["prove_ms"] - x["prove_ms"] for x, y in prs]
    lines = [
        "| 阶段 | A 中位 ms | B 中位 ms | Δ 中位 ms | 95% CI（Δ） | B/A 中位 | 占 Δprove |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    total = statistics.median(d_prove)
    for s in STAGES:
        a = [x["stages_ms"].get(s, 0.0) for x, _ in prs]
        b = [y["stages_ms"].get(s, 0.0) for _, y in prs]
        d = [bb - aa for aa, bb in zip(a, b)]
        ratio = [bb / aa for aa, bb in zip(a, b) if aa > 0]
        lo, hi = bootstrap_ci(d)
        ma, mb, md = statistics.median(a), statistics.median(b), statistics.median(d)
        share = md / total if total else float("nan")
        lines.append(
            f"| `{s}` | {f(ma,1)} | {f(mb,1)} | {md:+,.1f} | "
            f"[{lo:+,.1f}, {hi:+,.1f}] | {statistics.median(ratio):.4f} | {share:+.1%} |"
        )
    sums = [(x["stages_ms"], y["stages_ms"]) for x, y in prs]
    sum_a = statistics.median([sum(v.values()) for v, _ in sums])
    sum_b = statistics.median([sum(v.values()) for _, v in sums])
    lines += [
        f"| **九阶段合计** | **{f(sum_a,1)}** | **{f(sum_b,1)}** | **{sum_b - sum_a:+,.1f}** | | | |",
        f"| `prove_ms` | {f(statistics.median([x['prove_ms'] for x, _ in prs]),1)} | "
        f"{f(statistics.median([y['prove_ms'] for _, y in prs]),1)} | {total:+,.1f} | "
        f"[{bootstrap_ci(d_prove)[0]:+,.1f}, {bootstrap_ci(d_prove)[1]:+,.1f}] | | 100% |",
        f"| 未归因残差 | {sum_a and (statistics.median([x['prove_ms'] for x,_ in prs]) - sum_a):+,.1f} | "
        f"{(statistics.median([y['prove_ms'] for _,y in prs]) - sum_b):+,.1f} | | | | |",
    ]
    return lines, prs, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="results/transaction_e2e/f4a2_audit")
    args = ap.parse_args()
    d = Path(args.dir)

    out = ["# Stage F4-A.2 · 资源与阶段审计报告", ""]
    out.append(f"- 目录：`{d}`")
    out.append("- 协议：同一进程内 A/B 交错（`ORCHARD_F4A=0/1` 每次证明重读），与 doc 24 §5 同构。")
    out.append("- 单位：内存 MiB、分配字节 GiB、阶段毫秒。**任何一格都不跨块换算。**")
    out.append("")

    # ---------------- correctness ----------------
    out.append("## 1. 正确性（G1）")
    out.append("")
    out.append("| 几何 | 证明数 | proof 字节 | proof 指纹 | 判据 |")
    out.append("|---|---:|---|---|---|")
    verdicts = {}
    for geo, fn in [("1P", "stage_1p.log"), ("8P", "stage_8p.log")]:
        rows = jsonl(d / fn, "E2_PAIR")
        bytes_set = sorted({r["proof_bytes"] for r in rows})
        fnv_set = sorted({r["proof_fnv"] for r in rows})
        ok = bytes_set == [7264] and fnv_set == ["0xa6bcd171d69e969a"]
        verdicts[f"G1_{geo}"] = ok
        out.append(
            f"| {geo} | {len(rows)} | {', '.join(map(str, bytes_set))} | "
            f"{', '.join(fnv_set)} | {'PASS' if ok else 'FAIL'} |"
        )
    out.append("")

    for geo, mem_fn, stage_fn in [
        ("1P", "mem_1p.log", "stage_1p.log"),
        ("8P", "mem_8p.log", "stage_8p.log"),
    ]:
        out.append(f"## 2.{'1' if geo == '1P' else '2'} {geo} · 内存（块一）")
        out.append("")
        mrows = jsonl(d / mem_fn, "F4A2_MEM")
        arows = jsonl(d / mem_fn, "F4A2_ALLOC")
        lines, prs = mem_block(mrows, geo)
        out += lines
        out.append("")
        out.append(f"（{len(prs)} 对；窗口内采样 1 ms/次，样本数中位数 "
                   f"{statistics.median([r['samples'] for r in mrows]):.0f}）")
        out.append("")

        out.append(f"### {geo} · 分配（块二）")
        out.append("")
        lines, aprs = alloc_block(arows)
        out += lines
        out.append("")

        out.append(f"### {geo} · 阶段（块三）")
        out.append("")
        srows = jsonl(d / stage_fn, "E2_PAIR")
        lines, spr, total = stage_block(srows)
        out += lines
        out.append("")

        # gate numbers
        if aprs:
            da = statistics.median([y["alloc_calls"] - x["alloc_calls"] for x, y in aprs])
            db = statistics.median([y["alloc_bytes"] - x["alloc_bytes"] for x, y in aprs])
            out.append(f"**{geo} 对账**：`Δalloc_calls` = {da:+,.0f}；"
                       f"`Δalloc_bytes` = {db / 1024**3:+.3f} GiB")
            out.append("")

    # ---------------- gates ----------------
    out.append("## 3. 预注册判据（doc 25 §3）")
    out.append("")
    out.append("| # | 判据 | 观测 | 结论 |")
    out.append("|---|---|---|---|")
    out.append("| A1 | `Δquotient / Δprove ≥ 0.90` | 见 §2.1 / §2.2 阶段表 | 见正文 |")
    out.append("| A2 | 非 quotient 阶段 |Δ| ≤ 1% 或 CI 含 0 | 见阶段表 | 见正文 |")
    out.append("| A3 | `opening` |B/A−1| ≤ 2% | 见阶段表 | 见正文 |")
    out.append("| A4 | 1P ≈ −30 500 / 8P ≈ −244 000 次（±15%） | §2.1 / §2.2 对账行 | 见正文 |")
    out.append("| A5 | Δbytes ≈ −4.0 GiB（±20%），两几何比 ∈ [0.8, 1.25] | 同上 | 见正文 |")
    out.append("| A6 | memo 驻留 = 49 × chunk × 32 B（偏差 ≤ 2%） | 分配表 | 见正文 |")
    out.append("| A7 | 峰值增量 +6 ~ +7 MiB 且 < 64 MiB | 内存表 | 见正文 |")
    out.append("")

    text = "\n".join(out) + "\n"
    (d / "summary.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
