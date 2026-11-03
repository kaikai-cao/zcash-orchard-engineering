#!/usr/bin/env python3
"""Parse Stage E2 source-level phase timings from a harness run log.

The harness (with ORCHARD_E2_TIMING set) emits one line per proof:

    E2_STAGE pool=orchard rep=0 advice_commit=0.123 synthesis=0.456 ...

This script turns those into a tidy CSV (config, rep, pool, stage, seconds) and
prints a rolled-up 6-phase summary plus, when both 1P and 8P logs are supplied,
a side-by-side comparison with per-phase parallel speedups.

The 8 raw instrumented stages are:
  instance       - instance-column witness + commitments
  synthesis      - advice-column witness generation (FloorPlanner::synthesize +
                   batch_invert_assigned + blinding)
  advice_commit  - advice MSM (commit_lagrange) + batch_normalize + IFFT/FFT
  eval_setup     - polynomial evaluator handle registration
  lookup_permute - lookup commit_permuted
  permutation    - permutation commit
  lookup_product - lookup commit_product
  quotient       - vanishing commit + construct (the heavy FFT)
  opening        - challenge evals + multiopen::create_proof (the heavy MSM/IPA)

Roll-up to the 6 phases of interest (instance + eval_setup folded into synthesis
front-end, since they are cheap and conceptually pre-proof setup):
  synthesis       = instance + synthesis + eval_setup
  advice_commit   = advice_commit
  lookup          = lookup_permute + lookup_product
  permutation     = permutation
  quotient        = quotient
  opening (IPA)   = opening
"""

import argparse
import csv
import json
import re
import statistics
from pathlib import Path

STAGE_LINE = re.compile(r"^E2_STAGE\s+(.*)$")
KV = re.compile(r"(\w+)=([^\s]+)")

# Roll-up mapping: rolled phase -> list of raw stages
ROLLED = {
    "synthesis": ["instance", "synthesis", "eval_setup"],
    "advice_commit": ["advice_commit"],
    "lookup": ["lookup_permute", "lookup_product"],
    "permutation": ["permutation"],
    "quotient": ["quotient"],
    "opening": ["opening"],
}

ALL_RAW = [
    "instance", "synthesis", "advice_commit", "eval_setup",
    "lookup_permute", "permutation", "lookup_product", "quotient", "opening",
]


def parse_log(path: Path, pool: str = "orchard"):
    """Return {rep: {stage: sec}} for the given pool."""
    out = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = STAGE_LINE.match(line.strip())
        if not m:
            continue
        fields = dict(KV.findall(m.group(1)))
        if fields.get("pool") != pool:
            continue
        rep = int(fields["rep"])
        stage_map = {k: float(v) for k, v in fields.items() if k not in ("pool", "rep")}
        out[rep] = stage_map
    return out


def parse_prove_s(path: Path, pool: str = "orchard"):
    """Extract the per-rep prove_s array for `pool` from the BUNDLE line."""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("BUNDLE") and f'"pool":"{pool}"' in line:
            j = line[len("BUNDLE"):].strip()
            obj = json.loads(j)
            return obj.get("prove_s", [])
    return []


def rolled_means(rep_data):
    """Mean of each rolled phase across reps."""
    reps = sorted(rep_data)
    means = {}
    for phase, raws in ROLLED.items():
        vals = []
        for r in reps:
            s = sum(rep_data[r].get(st, 0.0) for st in raws)
            vals.append(s)
        means[phase] = statistics.fmean(vals) if vals else 0.0
    return means


def raw_means(rep_data):
    reps = sorted(rep_data)
    means = {}
    for st in ALL_RAW:
        vals = [rep_data[r].get(st, 0.0) for r in reps]
        means[st] = statistics.fmean(vals) if vals else 0.0
    return means


def write_tidy_csv(out_path: Path, cfg: str, rep_data):
    reps = sorted(rep_data)
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["config", "rep", "pool", "stage", "seconds"])
        for r in reps:
            for st in ALL_RAW:
                if st in rep_data[r]:
                    w.writerow([cfg, r, "orchard", st, f"{rep_data[r][st]:.6f}"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log1p", help="1P run log")
    ap.add_argument("--log8p", help="8P run log")
    ap.add_argument("--config", help="single-config mode: which log this is (1p/8p)")
    ap.add_argument("--log", help="single-config mode: log path")
    ap.add_argument("--out", help="output tidy CSV (single-config mode)")
    ap.add_argument(
        "--compare-out",
        help="output comparison CSV (two-log mode)",
        default="e2_compare.csv",
    )
    args = ap.parse_args()

    if args.log and args.config:
        rep_data = parse_log(Path(args.log))
        write_tidy_csv(Path(args.out), args.config, rep_data)
        rm = rolled_means(rep_data)
        print(f"\n=== E2 rolled-up phases ({args.config}) ===")
        total = 0.0
        for ph in ROLLED:
            print(f"  {ph:14s} {rm[ph]*1000:8.2f} ms")
            total += rm[ph]
        print(f"  {'SUM':14s} {total*1000:8.2f} ms")
        prove = parse_prove_s(Path(args.log))
        if prove:
            pm = statistics.fmean(prove)
            print(f"  outer prove_s  {pm*1000:8.2f} ms  (unaccounted { (pm-total)*1000:7.2f} ms)")
        return

    if args.log1p and args.log8p:
        d1 = parse_log(Path(args.log1p))
        d8 = parse_log(Path(args.log8p))
        rm1 = rolled_means(d1)
        rm8 = rolled_means(d8)
        raw1 = raw_means(d1)
        raw8 = raw_means(d8)

        # tidy CSVs
        write_tidy_csv(Path(args.log1p).with_suffix("").parent / "e2_1p.csv", "1p", d1)
        write_tidy_csv(Path(args.log8p).with_suffix("").parent / "e2_8p.csv", "8p", d8)

        with open(args.compare_out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["phase", "1p_ms", "8p_ms", "speedup", "1p_share_%"])
            s1_total = sum(rm1.values())
            for ph in ROLLED:
                a = rm1[ph] * 1000
                b = rm8[ph] * 1000
                sp = (rm1[ph] / rm8[ph]) if rm8[ph] > 0 else float("nan")
                share = (rm1[ph] / s1_total * 100) if s1_total > 0 else 0.0
                w.writerow([ph, f"{a:.2f}", f"{b:.2f}", f"{sp:.2f}", f"{share:.1f}"])
                print(f"{ph:14s} 1P {a:8.2f} ms | 8P {b:8.2f} ms | speedup {sp:5.2f}x | 1P share {share:5.1f}%")

        # also dump raw stages to a second file
        with open("e2_raw_compare.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["stage", "1p_ms", "8p_ms", "speedup"])
            for st in ALL_RAW:
                a = raw1[st] * 1000
                b = raw8[st] * 1000
                sp = (raw1[st] / raw8[st]) if raw8[st] > 0 else float("nan")
                w.writerow([st, f"{a:.2f}", f"{b:.2f}", f"{sp:.2f}"])

        # outer prove comparison
        p1 = parse_prove_s(Path(args.log1p))
        p8 = parse_prove_s(Path(args.log8p))
        if p1 and p8:
            pm1 = statistics.fmean(p1) * 1000
            pm8 = statistics.fmean(p8) * 1000
            print(f"\nouter prove_s: 1P {pm1:.2f} ms | 8P {pm8:.2f} ms | speedup {pm1/pm8:.2f}x")
            s1 = sum(rm1.values()) * 1000
            s8 = sum(rm8.values()) * 1000
            print(f"staged sum   : 1P {s1:.2f} ms | 8P {s8:.2f} ms | unaccounted 1P {pm1-s1:.2f} ms / 8P {pm8-s8:.2f} ms")
        return

    print("need either (--log --config --out) or (--log1p --log8p)")


if __name__ == "__main__":
    main()
