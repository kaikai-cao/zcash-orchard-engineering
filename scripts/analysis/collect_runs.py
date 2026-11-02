#!/usr/bin/env python3
"""Collect every archived Criterion estimate into one CSV + console table.

Walks results/raw/<suite>/<run>/criterion/orchard-k11/prove-one-action/new/
and reports mean / median / std_dev in seconds, together with the metadata
recorded for that run (affinity mask, or "unpinned").

Usage:
    python scripts/analysis/collect_runs.py
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ENGINEERING = Path(r"D:\Research\zcash-orchard-engineering")
RAW = ENGINEERING / "results" / "raw"
OUT = ENGINEERING / "results" / "analysis" / "all_runs.csv"

BENCH = Path("criterion") / "orchard-k11" / "prove-one-action"


def read_affinity(run_dir: Path) -> str:
    meta = run_dir / "metadata.md"
    if not meta.exists():
        return ""
    text = meta.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\| Processor affinity mask \| `([^`]+)` \|", text)
    if m:
        return m.group(1)
    if "RAYON_NUM_THREADS" in text:
        return "unpinned"
    return ""


def read_commit(run_dir: Path) -> str:
    meta = run_dir / "metadata.md"
    if not meta.exists():
        return ""
    text = meta.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\| Commit \| `([0-9a-f]+)` \|", text)
    return m.group(1) if m else ""


def main() -> None:
    rows = []
    for suite in sorted(p for p in RAW.iterdir() if p.is_dir()):
        for run in sorted(p for p in suite.iterdir() if p.is_dir()):
            est = run / BENCH / "new" / "estimates.json"
            if not est.exists():
                continue
            e = json.loads(est.read_text(encoding="utf-8"))
            mean = e["mean"]["point_estimate"] / 1e9
            rows.append(
                {
                    "Suite": suite.name,
                    "Run": run.name,
                    "Affinity": read_affinity(run),
                    "Commit": read_commit(run)[:12],
                    "Mean_s": round(mean, 4),
                    "MeanCI_low_s": round(e["mean"]["confidence_interval"]["lower_bound"] / 1e9, 4),
                    "MeanCI_high_s": round(e["mean"]["confidence_interval"]["upper_bound"] / 1e9, 4),
                    "Median_s": round(e["median"]["point_estimate"] / 1e9, 4),
                    "StdDev_s": round(e["std_dev"]["point_estimate"] / 1e9, 4),
                    "RelStdDev_pct": round(100 * e["std_dev"]["point_estimate"] / e["mean"]["point_estimate"], 2),
                }
            )

    if not rows:
        raise SystemExit(f"no archived estimates under {RAW}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    hdr = f"{'Suite':<10}{'Run':<14}{'Affinity':<10}{'Mean(s)':>9}{'SD(s)':>8}{'RSD%':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['Suite']:<10}{r['Run']:<14}{r['Affinity']:<10}"
            f"{r['Mean_s']:>9.4f}{r['StdDev_s']:>8.4f}{r['RelStdDev_pct']:>7.2f}"
        )
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
