#!/usr/bin/env python3
"""Parse the e2e harness v3-mode stdout into per-rep baseline CSVs.

Reads a raw harness log (results/transaction_e2e/baseline/raw/run_*.log),
extracts the Orchard V3 (PostNu6_3, 2 actions) per-rep timings plus the
once-measured keygen time, and writes a baseline CSV.

Usage:
    python scripts/analysis/e1_baseline_csv.py \
        --log results/transaction_e2e/baseline/raw/run_1p.log \
        --config 1p \
        --out results/transaction_e2e/baseline/baseline_1p.csv
"""
import argparse
import csv
import json
import re
import statistics


def parse_log(path):
    result = bundle = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("RESULT {"):
                result = json.loads(line[len("RESULT "):])
            elif line.startswith("BUNDLE {") and '"pool":"orchard"' in line:
                bundle = json.loads(line[len("BUNDLE "):])
    if result is None or bundle is None:
        raise SystemExit(f"RESULT/BUNDLE(orchard) not found in {path}")
    return result, bundle


def to_floats(s):
    if isinstance(s, str):
        return [float(x) for x in s.strip("[]").split(",") if x != ""]
    return [float(x) for x in s]  # already a JSON list


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--config", required=True, help="1p / 8p label")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    result, bundle = parse_log(args.log)

    keygen_s = float(result["keygen_s"])
    rayon = result.get("rayon_threads", "")
    affinity = result.get("affinity", "")
    actions = int(bundle["actions"])
    proof_bytes = int(bundle["proof_bytes"])
    ws_before = float(bundle["ws_before_mib"])
    ws_after = float(bundle["ws_after_mib"])

    prove = to_floats(bundle["prove_s"])
    build = to_floats(bundle["build_ms"])
    sign = to_floats(bundle["sign_ms"])
    verify = to_floats(bundle["verify_ms"])

    n = len(prove)
    rows = []
    for i in range(n):
        rows.append({
            "config": args.config,
            "rep": i + 1,
            "keygen_s": f"{keygen_s:.6f}",
            "build_ms": f"{build[i]:.4f}",
            "sign_ms": f"{sign[i]:.4f}",
            "verify_ms": f"{verify[i]:.4f}",
            "prove_s": f"{prove[i]:.6f}",
            "proof_bytes": proof_bytes,
            "ws_before_mib": f"{ws_before:.1f}",
            "ws_after_mib": f"{ws_after:.1f}",
            "rayon_threads": rayon,
            "affinity": affinity,
        })

    fields = ["config", "rep", "keygen_s", "build_ms", "sign_ms", "verify_ms",
              "prove_s", "proof_bytes", "ws_before_mib", "ws_after_mib",
              "rayon_threads", "affinity"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # Summary to stdout
    def stat(xs):
        return (statistics.mean(xs), min(xs), max(xs),
                statistics.pstdev(xs), (statistics.pstdev(xs) / statistics.mean(xs) * 100 if xs else 0))
    m, lo, hi, sd, rsd = stat(prove)
    print(f"[{args.config}] actions={actions} keygen_s={keygen_s:.4f} "
          f"prove_s mean={m:.6f} min={lo:.6f} max={hi:.6f} "
          f"pstdev={sd:.6f} RSD={rsd:.2f}%  -> {args.out}")


if __name__ == "__main__":
    main()
