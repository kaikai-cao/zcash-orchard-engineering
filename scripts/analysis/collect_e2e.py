#!/usr/bin/env python3
"""Collect RESULT lines from the Orchard end-to-end harness logs into one CSV.

Usage: python scripts/analysis/collect_e2e.py
"""

import json
import pathlib
import sys

RAW = pathlib.Path(__file__).resolve().parents[2] / "results" / "transaction_e2e" / "raw"
OUT = pathlib.Path(__file__).resolve().parents[2] / "results" / "transaction_e2e" / "analysis"

COLUMNS = [
    "tag", "version", "spends", "outputs", "actions", "reps", "seed",
    "fixture_s", "keygen_s", "build_ms_mean", "prove_s_mean", "prove_s_min",
    "sign_ms_mean", "verify_ms_mean", "proof_bytes", "encoded_bytes",
    "ws_before_prove_mib", "ws_after_prove_mib", "peak_ws_mib",
    "ws_after_keygen_mib", "rayon_threads", "affinity", "wall_s",
]


def main() -> int:
    rows = []
    for log in sorted(RAW.glob("*.log")):
        wall = None
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("RESULT "):
                rec = json.loads(line[len("RESULT "):])
                rec["wall_s"] = wall
                rows.append(rec)
            elif line.startswith("WALL_S="):
                wall = float(line.split("=", 1)[1])
        if rows and rows[-1]["tag"] == log.stem:
            rows[-1]["wall_s"] = wall

    if not rows:
        print("no RESULT lines found", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "e2e_runs.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(",".join(COLUMNS) + "\n")
        for r in rows:
            f.write(",".join(str(r.get(c, "")) for c in COLUMNS) + "\n")

    header = f"{'run':<22}{'n':>3}{'thr':>5}{'build ms':>10}{'prove s':>10}{'sign ms':>9}{'verify ms':>10}{'proof B':>9}{'bundle B':>9}{'peak MiB':>9}"
    print(header)
    for r in rows:
        print(
            f"{r['tag']:<22}{r['actions']:>3}{r['rayon_threads']:>5}"
            f"{r['build_ms_mean']:>10.2f}{r['prove_s_mean']:>10.3f}"
            f"{r['sign_ms_mean']:>9.2f}{r['verify_ms_mean']:>10.2f}"
            f"{r['proof_bytes']:>9}{r['encoded_bytes']:>9}{r['peak_ws_mib']:>9.1f}"
        )
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
