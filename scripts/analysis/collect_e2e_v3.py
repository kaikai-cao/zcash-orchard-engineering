#!/usr/bin/env python3
"""Collect BUNDLE lines from the NU6.3 (V3) harness logs.

Usage: python scripts/analysis/collect_e2e_v3.py
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW = ROOT / "results" / "transaction_e2e" / "raw_v3"
OUT = ROOT / "results" / "transaction_e2e" / "analysis"

COLUMNS = [
    "tag", "pool", "bundle_version", "circuit", "actions", "reps",
    "build_ms_mean", "prove_s_mean", "prove_s_min", "sign_ms_mean",
    "verify_ms_mean", "proof_bytes", "encoded_bytes",
    "ws_before_mib", "ws_after_mib", "rayon_threads", "affinity", "keygen_s",
]


def main() -> int:
    runs = {}
    bundles = []
    for log in sorted(RAW.glob("*.log")):
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("RESULT "):
                rec = json.loads(line[len("RESULT "):])
                runs[rec["tag"]] = rec
            elif line.startswith("BUNDLE "):
                bundles.append(json.loads(line[len("BUNDLE "):]))

    if not bundles:
        print("no BUNDLE lines found", file=sys.stderr)
        return 1

    for b in bundles:
        r = runs.get(b["tag"], {})
        b["rayon_threads"] = r.get("rayon_threads", "")
        b["affinity"] = r.get("affinity", "")
        b["keygen_s"] = round(r.get("keygen_s", 0.0), 3)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "e2e_v3_runs.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(",".join(COLUMNS) + "\n")
        for b in bundles:
            f.write(",".join(str(b.get(c, "")) for c in COLUMNS) + "\n")

    hdr = (
        f"{'run':<16}{'pool':<10}{'acts':>4}{'thr':>4}{'build ms':>10}"
        f"{'prove s':>9}{'sign ms':>9}{'verify ms':>10}{'proof B':>9}{'bundle B':>9}"
    )
    print(hdr)
    for b in bundles:
        enc = b.get("encoded_bytes", "-")
        print(
            f"{b['tag']:<16}{b['pool']:<10}{b['actions']:>4}{b['rayon_threads']:>4}"
            f"{b['build_ms_mean']:>10.2f}{b['prove_s_mean']:>9.3f}"
            f"{b['sign_ms_mean']:>9.2f}{b['verify_ms_mean']:>10.2f}"
            f"{b['proof_bytes']:>9}{str(enc):>9}"
        )

    print()
    for tag, r in sorted(runs.items()):
        print(
            f"{tag}: keygen {r['keygen_s']:.3f}s  orchard {r['orchard_prove_s']:.3f}s"
            f" + ironwood {r['ironwood_prove_s']:.3f}s = {r['prove_sum_s']:.3f}s"
            f"  peak {r['peak_ws_mib']:.1f} MiB  threads {r['rayon_threads']}"
        )
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
