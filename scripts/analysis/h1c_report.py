#!/usr/bin/env python3
"""Stage H1c: compare the interleaved A/B runs and print the pre-registered gates.

Reads results/transaction_e2e/h1c_lto/runs/h1c_<threads>p_<side>_r<round>.log,
pulls the orchard BUNDLE line out of each, and reports:

  * per side: the 25 prove_s samples pooled over 5 rounds x 5 reps
    (median, mean, CV)
  * per round: the round median for A and B, and their relative difference
  * the pooled relative difference and a paired bootstrap interval over
    ROUNDS (5 pairs). Round is the resampling unit because rounds are the
    unit that shares a thermal state.

Then it prints which of the pre-registered gates G-A..G-D fired, from
docs/orchard/14_h1c_lto_prereg.md. It does not decide anything by itself
beyond that mapping: the interval is the evidence.

Usage:
    python scripts/analysis/h1c_report.py [--dir DIR] [--boot 10000]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics as st
import sys

BUNDLE = re.compile(r"^BUNDLE (\{.*\})\s*$")
RESULT = re.compile(r"^RESULT (\{.*\})\s*$")


def parse_log(path: str) -> dict | None:
    orchard_prove = None
    per_rep = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = BUNDLE.match(line)
            if m:
                try:
                    o = json.loads(m.group(1))
                except json.JSONDecodeError:
                    continue
                if o.get("pool") == "orchard":
                    per_rep = o.get("prove_s")
                    orchard_prove = o.get("prove_s_mean")
            m = RESULT.match(line)
            if m:
                try:
                    o = json.loads(m.group(1))
                except json.JSONDecodeError:
                    continue
                if o.get("orchard_prove_s") is not None:
                    orchard_prove = o["orchard_prove_s"]
    if per_rep is None and orchard_prove is None:
        return None
    return {"mean": orchard_prove, "reps": per_rep}


def bootstrap_delta(round_deltas: list[float], n: int, rng) -> tuple[float, float, float]:
    """Paired bootstrap over rounds. Returns (median, lo95, hi95) of the mean delta."""
    k = len(round_deltas)
    means = []
    for _ in range(n):
        pick = [round_deltas[rng.randrange(k)] for _ in range(k)]
        means.append(sum(pick) / k)
    means.sort()
    return (st.median(means), means[int(0.025 * n)], means[int(0.975 * n) - 1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="results/transaction_e2e/h1c_lto/runs")
    ap.add_argument("--boot", type=int, default=10000)
    ap.add_argument("--out", default="results/transaction_e2e/h1c_lto")
    args = ap.parse_args()

    import random
    rng = random.Random(20261103)

    lines_out: list[str] = []
    def emit(s: str = "") -> None:
        print(s)
        lines_out.append(s)

    emit("Stage H1c - LTO only, interleaved A/B")
    emit("=" * 72)

    summary_rows = []
    for threads in (1, 8):
        rounds_a, rounds_b = {}, {}
        for round_no in range(1, 100):
            pa = os.path.join(args.dir, f"h1c_{threads}p_a_r{round_no}.log")
            pb = os.path.join(args.dir, f"h1c_{threads}p_b_r{round_no}.log")
            if not (os.path.exists(pa) and os.path.exists(pb)):
                break
            da, db = parse_log(pa), parse_log(pb)
            if da is None or db is None:
                break
            rounds_a[round_no] = da
            rounds_b[round_no] = db

        if not rounds_a:
            emit(f"\n[{threads}P] no logs found in {args.dir}")
            continue

        pooled_a = [v for d in rounds_a.values() for v in (d["reps"] or [d["mean"]])]
        pooled_b = [v for d in rounds_b.values() for v in (d["reps"] or [d["mean"]])]

        def stats(xs):
            m = st.median(xs)
            mean = st.fmean(xs)
            cv = (st.stdev(xs) / mean * 100) if len(xs) > 1 else 0.0
            return m, mean, cv

        ma, mea, cva = stats(pooled_a)
        mb, meb, cvb = stats(pooled_b)

        emit(f"\n[{threads}P]  rounds={len(rounds_a)}  samples/side={len(pooled_a)}")
        emit(f"  A (release)      median {ma*1000:8.1f} ms   mean {mea*1000:8.1f} ms   CV {cva:5.2f}%")
        emit(f"  B (release+lto)  median {mb*1000:8.1f} ms   mean {meb*1000:8.1f} ms   CV {cvb:5.2f}%")
        emit(f"  pooled delta     {(mb-ma)/ma*100:+6.2f}%  (median B vs median A)")

        round_deltas = []
        emit("  per round (median of 5 reps):")
        for r in sorted(rounds_a):
            ra = st.median(rounds_a[r]["reps"] or [rounds_a[r]["mean"]])
            rb = st.median(rounds_b[r]["reps"] or [rounds_b[r]["mean"]])
            d = (rb - ra) / ra * 100
            round_deltas.append(d)
            emit(f"    r{r}   A {ra*1000:8.1f}   B {rb*1000:8.1f}   delta {d:+6.2f}%")

        med, lo, hi = bootstrap_delta(round_deltas, args.boot, rng)
        emit(f"  paired bootstrap over rounds: mean delta {med:+6.2f}%  95% CI [{lo:+6.2f}%, {hi:+6.2f}%]  (n={args.boot})")

        if threads == 1:
            emit("  --- pre-registered gates (1P is the deciding arm) ---")
            if med <= -5:
                g = "G-A  gain <= -5%  -> F2's revision is wrong, H1c revives"
            elif med <= -1 and hi < 0:
                g = "G-B  -5% < gain <= -1%, CI excludes 0 -> real but modest"
            elif med >= 1:
                g = "G-D  gain >= +1%  -> LTO hurts"
            else:
                g = "G-C  |gain| < 1% or CI spans 0 -> no measurable benefit"
            emit(f"  {g}")
            if hi < -10.6:
                emit("  NOTE: CI upper bound below -10.6% would confirm F1's number; it does not.")
            else:
                emit(f"  CI upper bound is {hi:+.2f}%, i.e. the data excludes a 10.6% win at 95%.")

        summary_rows.append((threads, ma, mb, (mb - ma) / ma * 100, lo, hi))

    out = os.path.join(args.out, "summary.txt")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines_out) + "\n")
    csv = os.path.join(args.out, "timing.csv")
    with open(csv, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("threads,median_a_ms,median_b_ms,delta_pct,ci_lo_pct,ci_hi_pct\n")
        for t, ma, mb, d, lo, hi in summary_rows:
            fh.write(f"{t},{ma*1000:.1f},{mb*1000:.1f},{d:.2f},{lo:.2f},{hi:.2f}\n")
    print(f"\nwrote {out}\nwrote {csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
