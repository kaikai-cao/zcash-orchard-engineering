#!/usr/bin/env python3
"""Stage F3 · LC report: turn `--mode lc` logs into one table.

Inputs are the raw harness logs under results/transaction_e2e/f3_lc/. Each run
prints three lines we care about:

  F2_REPORT  the fused-tree Ast counts (independent second walk, and the
             `first_ms` of the quotient evaluation for that same run)
  LC_REPORT  the leaf census, one entry per `Evaluator::evaluate` call
  LC_META    proof fingerprint, verify time, thread count, probe on/off

The census has 13 calls for an Orchard V3 bundle: twelve small lookup/permutation
trees on the base domain (poly_len 2048) and one fused quotient tree on the
extended domain (poly_len 16384). Only the last one is large enough to matter,
and it is selected by `poly_len`, not by position.

Usage:
  python scripts/analysis/f3_lc_report.py [--dir DIR] [--csv OUT] [--md OUT]
"""

from __future__ import annotations

import argparse
import csv
import json
import os

F3_H6_1P_MS = 2781.4  # frozen H6 reference (docs/orchard/17 §0)
F3_H6_8P_MS = 784.8


def load(path):
    text = open(path, encoding="utf-8").read()
    out = {}
    if "F2_REPORT " in text:
        out["f2"] = json.loads(text.split("F2_REPORT ", 1)[1].split("\n", 1)[0])
    if "LC_REPORT " in text:
        out["lc"] = json.loads(text.split("LC_REPORT ", 1)[1].split("\n", 1)[0])
    if "LC_META " in text:
        out["meta"] = json.loads(text.split("LC_META ", 1)[1].split("\n", 1)[0])
    return out


def biggest(lc):
    """The fused quotient tree: the call with the longest polynomial."""
    return max(lc["calls"], key=lambda c: c["poly_len"])


def row(name, d):
    meta = d.get("meta") or {}
    lc = d.get("lc") or {"calls": []}
    if not lc["calls"]:
        return None
    c = biggest(lc)
    f2 = d.get("f2")
    ev = None
    if f2:
        calls = f2["evals"]["items"]
        ev = max(calls, key=lambda e: e["poly_len"])

    nse = (c.get("bench") or {}).get("ns_per_elem") or 0.0
    elems = c["visits"] * c["poly_len"]
    mat_ms = elems * nse / 1e6
    saved_ms = c["saved_chunks"] * c["poly_len"] * nse / 1e6
    saved_upper_ms = c["borrow"] * c["poly_len"] * nse / 1e6
    eval_ms = ev["first_ms"] if ev else 0.0
    threads = int(meta.get("rayon_threads") or 0) or None
    prove_ms = meta.get("proof_ms") or 0.0
    ref = F3_H6_1P_MS if threads == 1 else F3_H6_8P_MS

    return {
        "run": name,
        "threads": threads,
        "probe": meta.get("probe"),
        "proof_ms": round(prove_ms, 1),
        "verify_ms": round(meta.get("verify_ms") or 0.0, 1),
        "proof_fnv": meta.get("proof_fnv"),
        "eval_ms": round(eval_ms, 2),
        "poly_len": c["poly_len"],
        "chunk_size": c["chunk_size"],
        "num_chunks": c["num_chunks"],
        "registered": c["registered"],
        "visits": c["visits"],
        "distinct": c["distinct"],
        "reuse": round(c["visits"] / c["distinct"], 2),
        "own": c["own"],
        "borrow": c["borrow"],
        "borrow_pct": round(100.0 * c["borrow"] / c["visits"], 2),
        "distinct_with_borrow": c["distinct_with_borrow"],
        "saved_chunks": c["saved_chunks"],
        "leaf_gb": round(c["leaf_bytes"] / 1e9, 4),
        "saved_gb": round(c["saved_bytes"] / 1e9, 4),
        "cache_mib": round(c["cache_bytes"] / 2**20, 2),
        "per_thread_mib": round(c["per_thread_bytes"] / 2**20, 2),
        "ns_per_elem": round(nse, 4),
        "gbps": round((c.get("bench") or {}).get("gbps") or 0.0, 2),
        "materialise_ms": round(mat_ms, 2),
        "saved_ms": round(saved_ms, 2),
        "saved_upper_ms": round(saved_upper_ms, 2),
        "mat_pct_eval": round(100.0 * mat_ms / eval_ms, 2) if eval_ms else 0.0,
        "saved_pct_eval": round(100.0 * saved_ms / eval_ms, 2) if eval_ms else 0.0,
        "saved_pct_prove": round(100.0 * saved_ms / ref, 3) if ref else 0.0,
    }


def main():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(here, "results/transaction_e2e/f3_lc"))
    ap.add_argument("--csv", default=None)
    ap.add_argument("--md", default=None)
    args = ap.parse_args()

    rows = []
    for f in sorted(os.listdir(args.dir)):
        if not f.endswith(".log"):
            continue
        d = load(os.path.join(args.dir, f))
        if "lc" not in d:
            continue
        r = row(f[:-4], d)
        if r:
            rows.append(r)

    if not rows:
        raise SystemExit(f"no --mode lc logs under {args.dir}")

    keys = list(rows[0].keys())
    csv_path = args.csv or os.path.join(args.dir, "summary.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {csv_path} ({len(rows)} runs)")

    head = (
        f"{'run':22} {'thr':>3} {'probe':>5} {'visits':>7} {'distinct':>8} "
        f"{'reuse':>6} {'borrow%':>8} {'saved_ms':>9} {'%eval':>7} {'%prove':>7} "
        f"{'cacheMiB':>9}"
    )
    print(head)
    print("-" * len(head))
    for r in rows:
        print(
            f"{r['run']:22} {r['threads']:>3} {str(r['probe']):>5} {r['visits']:>7} "
            f"{r['distinct']:>8} {r['reuse']:>6} {r['borrow_pct']:>8} "
            f"{r['saved_ms']:>9} {r['saved_pct_eval']:>7} {r['saved_pct_prove']:>7} "
            f"{r['cache_mib']:>9}"
        )

    if args.md:
        lines = [
            "| run | thr | visits | distinct | reuse | borrow% | saved ms | % eval | % prove | cache MiB |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in rows:
            lines.append(
                f"| {r['run']} | {r['threads']} | {r['visits']} | {r['distinct']} | "
                f"{r['reuse']} | {r['borrow_pct']} | {r['saved_ms']} | "
                f"{r['saved_pct_eval']} | {r['saved_pct_prove']} | {r['cache_mib']} |"
            )
        open(args.md, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        print(f"wrote {args.md}")


if __name__ == "__main__":
    main()
