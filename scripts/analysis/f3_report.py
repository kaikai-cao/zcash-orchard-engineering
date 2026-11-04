#!/usr/bin/env python3
"""Stage F3 / H6: compare the ABBA runs and answer the three-layer question.

Reads three independent datasets, each produced by
scripts/benchmark/run_f3_ab.sh with a different switch set
(docs/orchard/15_h6_neg_prereg.md section 4):

  D1  <root>/baseline, <root>/treatment           no env at all          -> verdict
  D2  <root>/stage/{baseline,treatment}           ORCHARD_E2_TIMING=1    -> attribution
  D3  <root>/mechanism/{baseline,treatment}       ORCHARD_F2=1, mode f2  -> mechanism

D1/D2 use ABBA blocks (A1 B1 B2 A2). Each block yields two pairs --
(A1,B1) and (B2,A2) -- and the paired bootstrap resamples over pairs, not
over samples, because a pair shares a thermal state.

The point of the report is the three-layer answer:
  1. how many of F2's 6570 whole-chunk multiplications really disappear
  2. how much faster the evaluator gets
  3. how much of that survives to the end-to-end proof
Layer 3 minus layers 1-2 is "absorbed", and the report says so out loud
instead of rounding it away.

Usage:
    python scripts/analysis/f3_report.py [--root DIR] [--boot 10000]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics as st
import sys

BUNDLE = re.compile(r"^BUNDLE (\{.*\})\s*$")
RESULT = re.compile(r"^RESULT (\{.*\})\s*$")
E2 = re.compile(r"^E2_STAGE pool=(\w+) rep=\d+ (.*)$")
F2REPORT = re.compile(r"^F2_REPORT (\{.*\})\s*$")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_timing(path: str) -> dict | None:
    """prove_s samples + per-stage seconds, from one harness run."""
    reps = None
    stages: list[dict[str, float]] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = BUNDLE.match(line)
            if m:
                try:
                    o = json.loads(m.group(1))
                except json.JSONDecodeError:
                    continue
                if o.get("pool") == "orchard":
                    reps = o.get("prove_s")
            m = E2.match(line)
            # Each rep emits one line per pool; only the Orchard bundle is the
            # subject of this experiment, and mixing the two would average a
            # 16384-element quotient with a smaller one.
            if m and m.group(1) == "orchard":
                kv = {}
                for part in m.group(2).split():
                    k, _, v = part.partition("=")
                    try:
                        kv[k] = float(v)
                    except ValueError:
                        pass
                if kv:
                    stages.append(kv)
    if reps is None:
        return None
    med = {}
    if stages:
        keys = set().union(*[set(s) for s in stages])
        for k in keys:
            vals = [s[k] for s in stages if k in s]
            if vals:
                med[k] = st.median(vals)
    return {"reps": reps, "stages": med}


def parse_mechanism(path: str) -> dict | None:
    """The quotient evaluation's AST census and wall time, from F2_REPORT."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = F2REPORT.match(line)
            if not m:
                continue
            try:
                d = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            items = d.get("evals", {}).get("items", [])
            if not items:
                return None
            # The quotient call is the one over the extended domain; it is two
            # orders of magnitude bigger than every other evaluate() call.
            big = max(items, key=lambda it: it["poly_len"])
            return {"ast": big["ast"], "first_ms": big["first_ms"],
                    "poly_len": big["poly_len"], "chunk_size": big["chunk_size"],
                    "num_chunks": big["num_chunks"], "terms": big["ast"].get("terms", [])}
    return None


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def bootstrap(deltas: list[float], n: int, rng) -> tuple[float, float, float]:
    k = len(deltas)
    means = []
    for _ in range(n):
        pick = [deltas[rng.randrange(k)] for _ in range(k)]
        means.append(sum(pick) / k)
    means.sort()
    return st.median(means), means[int(0.025 * n)], means[int(0.975 * n) - 1]


def abba_pairs(root: str, threads: int, key: str):
    """Yield ((a_value, b_value), round_no) for each ABBA pair."""
    out = []
    for r in range(1, 100):
        pa = {p: os.path.join(root, "baseline", f"{threads}p_a_r{r}_{p}.log") for p in (1, 2)}
        pb = {p: os.path.join(root, "treatment", f"{threads}p_b_r{r}_{p}.log") for p in (1, 2)}
        if not all(os.path.exists(v) for v in list(pa.values()) + list(pb.values())):
            break
        da = {p: parse_timing(v) for p, v in pa.items()}
        db = {p: parse_timing(v) for p, v in pb.items()}
        if any(v is None for v in list(da.values()) + list(db.values())):
            break
        # Pairs are always (A, B): (A1,B1) and (A2,B2). In the ABBA schedule
        # A2 runs last, so both pairs are adjacent in time -- that is what
        # cancels drift, and swapping the sides for pair 2 would instead make
        # the two pair-deltas cancel against each other.
        for pos, (a, b) in enumerate(((da[1], db[1]), (da[2], db[2])), start=1):
            out.append((r, pos, a, b))
    return out


def rel(a: float, b: float) -> float:
    return (b - a) / a * 100.0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="results/transaction_e2e/f3_h6")
    ap.add_argument("--boot", type=int, default=10000)
    args = ap.parse_args()
    rng = random.Random(20261104)

    lines: list[str] = []
    csv_rows: list[list[str]] = []

    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    emit("Stage F3 / H6 -- Ast::Neg instead of Scale(-1)")
    emit("=" * 74)
    emit("ABBA blocks (A1 B1 B2 A2); pairs resampled, not individual samples.")

    # -- D1: end-to-end ----------------------------------------------------
    emit("\n[D1] end-to-end prove, plain release, no instrumentation")
    d1 = {}
    for threads in (1, 8):
        pairs = abba_pairs(args.root, threads, "reps")
        if not pairs:
            emit(f"  {threads}P: no logs")
            continue
        pooled_a = [v for _, _, a, _ in pairs for v in a["reps"]]
        pooled_b = [v for _, _, _, b in pairs for v in b["reps"]]
        ma, mb = st.median(pooled_a), st.median(pooled_b)
        deltas = []
        for r, pos, a, b in pairs:
            ra, rb = st.median(a["reps"]), st.median(b["reps"])
            deltas.append(rel(ra, rb))
        med, lo, hi = bootstrap(deltas, args.boot, rng)
        cva = st.stdev(pooled_a) / st.fmean(pooled_a) * 100
        cvb = st.stdev(pooled_b) / st.fmean(pooled_b) * 100

        emit(f"  {threads}P  pairs={len(pairs)}  samples/side={len(pooled_a)}")
        emit(f"    A  median {ma*1000:8.1f} ms   CV {cva:5.2f}%")
        emit(f"    B  median {mb*1000:8.1f} ms   CV {cvb:5.2f}%")
        emit(f"    paired delta {med:+6.2f}%   95% CI [{lo:+6.2f}%, {hi:+6.2f}%]")
        emit("    per pair (run medians):  " + "  ".join(
            f"r{r}.{p}:{d:+.2f}%" for (r, p, _, _), d in zip(pairs, deltas)))
        d1[threads] = {"a": ma, "b": mb, "delta": med, "lo": lo, "hi": hi,
                       "pairs": len(pairs), "cv_a": cva, "cv_b": cvb}
        csv_rows.append(["d1_prove", f"{threads}p", f"{ma*1000:.1f}", f"{mb*1000:.1f}",
                         f"{med:.2f}", f"{lo:.2f}", f"{hi:.2f}", str(len(pairs))])

    # -- H6-D gate ---------------------------------------------------------
    emit("\n  --- H6-D (1P is the deciding arm) ---")
    if 1 in d1:
        g = d1[1]
        if g["hi"] < 0:
            emit(f"  PASS  upper bound {g['hi']:+.2f}% < 0: the proof got faster at 95%.")
        elif g["lo"] > 0:
            emit(f"  FAIL  lower bound {g['lo']:+.2f}% > 0: H6 made the proof slower.")
        else:
            emit(f"  FAIL  CI [{g['lo']:+.2f}%, {g['hi']:+.2f}%] spans 0.")
            emit("        If D2 shows quotient/evaluate falling, the gain is being")
            emit("        absorbed elsewhere -- see H6-F, name the stage, do not")
            emit("        round this to 'no measurable benefit'.")

    # -- D2: stages --------------------------------------------------------
    emit("\n[D2] stage breakdown (ORCHARD_E2_TIMING=1; attribution only)")
    stage_dir = os.path.join(args.root, "stage")
    d2 = {}
    if os.path.isdir(stage_dir):
        for threads in (1, 8):
            pairs = abba_pairs(stage_dir, threads, "stages")
            if not pairs:
                continue
            keys = sorted({k for _, _, a, _ in pairs for k in a["stages"]})
            emit(f"  {threads}P  pairs={len(pairs)}")
            for k in keys:
                da = [a["stages"][k] for _, _, a, _ in pairs if k in a["stages"]]
                db = [b["stages"][k] for _, _, _, b in pairs if k in b["stages"]]
                if not da or not db:
                    continue
                pa = [(a["stages"][k], b["stages"][k]) for _, _, a, b in pairs
                      if k in a["stages"] and k in b["stages"]]
                ds = [rel(x, y) for x, y in pa]
                med, lo, hi = bootstrap(ds, args.boot, rng)
                ma, mb = st.median(da), st.median(db)
                mark = ""
                if hi < 0:
                    mark = "  faster"
                elif lo > 0:
                    mark = "  REGRESSION"
                emit(f"    {k:<16} A {ma*1000:8.1f} ms   B {mb*1000:8.1f} ms   "
                     f"delta {med:+6.2f}%  [{lo:+6.2f}, {hi:+6.2f}]{mark}")
                d2[(threads, k)] = {"a": ma, "b": mb, "delta": med,
                                    "lo": lo, "hi": hi}
                csv_rows.append(["d2_" + k, f"{threads}p", f"{ma*1000:.1f}",
                                 f"{mb*1000:.1f}", f"{med:.2f}", f"{lo:.2f}",
                                 f"{hi:.2f}", str(len(pa))])
            # Every stage except quotient in one number: the per-stage CIs are
            # tight enough on cheap stages (6 pairs) that a 1% wobble can come
            # out significant, so the aggregate is the honest summary of
            # "did anything else pay for this".
            rest = [k for k in keys if k != "quotient"]
            sa = [sum(a["stages"][k] for k in rest if k in a["stages"]) for _, _, a, _ in pairs]
            sb = [sum(b["stages"][k] for k in rest if k in b["stages"]) for _, _, _, b in pairs]
            ds = [rel(x, y) for x, y in zip(sa, sb)]
            med, lo, hi = bootstrap(ds, args.boot, rng)
            emit(f"    {'all-but-quotient':<16} A {st.median(sa)*1000:8.1f} ms   "
                 f"B {st.median(sb)*1000:8.1f} ms   delta {med:+6.2f}%  "
                 f"[{lo:+6.2f}, {hi:+6.2f}]")
            d2[(threads, "all_but_quotient")] = {"a": st.median(sa), "b": st.median(sb),
                                                 "delta": med, "lo": lo, "hi": hi}
            csv_rows.append(["d2_all_but_quotient", f"{threads}p",
                             f"{st.median(sa)*1000:.1f}", f"{st.median(sb)*1000:.1f}",
                             f"{med:.2f}", f"{lo:.2f}", f"{hi:.2f}", str(len(pairs))])
            emit("")

        regressions = [k for (t, k), v in d2.items()
                       if v["lo"] > 0 and k != "all_but_quotient"]
        emit("  --- H6-E (negative gate) ---")
        if regressions:
            worst = max((v["delta"] for (t, k), v in d2.items() if k in regressions))
            emit(f"  Letter of the gate: FAIL -- {len(regressions)} stage(s) slower at 95%:")
            for k in sorted(set(regressions)):
                v = next(v for (t, kk), v in d2.items() if kk == k)
                emit(f"    {k:<16} {v['delta']:+.2f}%  A {v['a']*1000:.1f} ms")
            emit(f"  Largest of them is {worst:+.2f}%. Check the sign at the other")
            emit("  thread count before calling it a cost: an effect that flips")
            emit("  sign is layout/noise, not a trade-off.")
            agg = d2.get((1, "all_but_quotient"))
            if agg:
                emit(f"  Aggregate of every non-quotient stage: {agg['delta']:+.2f}% "
                     f"({(agg['b'] - agg['a'])*1000:+.1f} ms)")
        else:
            emit("  PASS  no stage other than quotient regressed at 95%.")
    else:
        emit("  (no stage dataset)")

    # -- D3: mechanism -----------------------------------------------------
    emit("\n[D3] mechanism: node census + evaluator wall time (mode f2)")
    mech_dir = os.path.join(args.root, "mechanism")
    d3 = {}
    if os.path.isdir(mech_dir):
        for threads in (1, 8):
            for side in ("baseline", "treatment"):
                d = os.path.join(mech_dir, side)
                if not os.path.isdir(d):
                    continue
                recs = []
                for name in sorted(os.listdir(d)):
                    if not (name.endswith(".log") and name.startswith(f"{threads}p_")):
                        continue
                    r = parse_mechanism(os.path.join(d, name))
                    if r:
                        recs.append(r)
                if not recs:
                    continue
                asts = [r["ast"] for r in recs]
                # Counts are deterministic; if they are not, that itself is a finding.
                for key in ("poly", "add", "mul", "scale", "neg", "nodes", "vector_muls"):
                    vals = {a.get(key, 0) for a in asts}
                    if len(vals) > 1:
                        emit(f"  WARNING {threads}P {side}.{key} not deterministic: "
                             f"{sorted(vals)}")
                a0 = asts[0]
                t = st.median([r["first_ms"] for r in recs])
                d3[(threads, side)] = {"ast": a0, "ms": t, "n": len(recs),
                                       "terms": recs[0].get("terms", [])}
                emit(f"  {threads}P {side:<10} n={len(recs)}  poly {a0['poly']}  "
                     f"add {a0['add']}  mul {a0['mul']}  scale {a0['scale']}  "
                     f"neg {a0.get('neg', 0)}  nodes {a0['nodes']}  "
                     f"vector_muls {a0['vector_muls']}  evaluate {t:8.1f} ms")

        if (1, "baseline") in d3 and (1, "treatment") in d3:
            a = d3[(1, "baseline")]["ast"]
            b = d3[(1, "treatment")]["ast"]
            emit("\n  --- H6-B (did the multiplications actually go away?) ---")
            # The pre-registration predicted absolute values derived from F2's
            # *expression* census. It missed the `Sub` operator, which lowers to
            # `Neg` too, so the absolute numbers are off by however many Subtrees
            # the permutation and lookup arguments contain. The invariants below
            # do not depend on that derivation.
            moved = a["scale"] - b["scale"]
            predicted = [
                ("scale", a["scale"], b["scale"], 528),
                ("neg", a.get("neg", 0), b.get("neg", 0), 2526),
                ("vector_muls", a["vector_muls"], b["vector_muls"], 4044),
            ]
            for name, va, vb, expect in predicted:
                emit(f"    {name:<12} {va:>5} -> {vb:<5}   (pre-registered {expect}, "
                     f"{'hit' if vb == expect else f'miss by {vb - expect:+d}'})")

            inv = [
                ("every dropped Scale became a Neg",
                 b["scale"] + b.get("neg", 0) == a["scale"]),
                ("vector_muls dropped by exactly that many",
                 a["vector_muls"] - b["vector_muls"] == moved),
                ("node total unchanged", a["nodes"] == b["nodes"]),
                ("mul unchanged", a["mul"] == b["mul"]),
                ("poly/add unchanged",
                 a["poly"] == b["poly"] and a["add"] == b["add"]),
            ]
            ok = all(v for _, v in inv)
            for name, v in inv:
                emit(f"    {'OK  ' if v else 'FAIL'} {name}")
            removed = a["vector_muls"] - b["vector_muls"]
            emit(f"    moved {moved} Scale nodes, {removed} of {a['vector_muls']} "
                 f"whole-chunk muls gone = {removed / a['vector_muls'] * 100:.1f}%")
            emit(f"  => H6-B {'PASS' if ok else 'FAIL'} (invariants; the absolute")
            emit("     prediction is reported above as a miss, not silently fixed)")

             # Per-group split of the dropped Scale nodes. The terms arrive in
            # blocks of (gate polys, permutation args, lookup args) per instance,
            # which is what makes the three groups separable at all.
            recs_b = d3[(1, "baseline")]
            recs_t = d3[(1, "treatment")]
            terms_a = recs_b.get("terms") or []
            terms_b = recs_t.get("terms") or []
            if terms_a and terms_b and len(terms_a) == len(terms_b):
                per_block = 193 + 7 + 15
                groups = ("gate", "permutation", "lookup")
                ga = {g: 0 for g in groups}
                gb = {g: 0 for g in groups}
                for i, (ta, tb) in enumerate(zip(terms_a, terms_b)):
                    pos = i % per_block
                    g = "gate" if pos < 193 else ("permutation" if pos < 200 else "lookup")
                    ga[g] += ta[1]
                    gb[g] += tb[1]
                emit("\n  --- where the Scale nodes went, by group ---")
                for g in groups:
                    emit(f"    {g:<12} {ga[g]:>5} -> {gb[g]:<4}  ({gb[g] - ga[g]:+d})")
                    csv_rows.append([f"d3_scale_{g}", "1p", str(ga[g]), str(gb[g]),
                                     "", "", "", ""])
                emit(f"    gate delta {gb['gate'] - ga['gate']:+d} should equal "
                     f"F2's Negated census 2 x 1263 = -2526: "
                     f"{'OK' if gb['gate'] - ga['gate'] == -2526 else 'MISMATCH'}")
                emit(f"    the rest ({sum(gb[g] - ga[g] for g in groups) + 2526:+d}) comes "
                     f"from `Sub`, which F2 did not count")

            tc = d3[(1, "treatment")]["ms"]
            ta = d3[(1, "baseline")]["ms"]
            emit("\n  --- H6-C (evaluator got faster) ---")
            emit(f"    1P  evaluate {ta:.1f} -> {tc:.1f} ms   {rel(ta, tc):+.2f}%")
            if (8, "baseline") in d3 and (8, "treatment") in d3:
                t8a = d3[(8, "baseline")]["ms"]
                t8b = d3[(8, "treatment")]["ms"]
                emit(f"    8P  evaluate {t8a:.1f} -> {t8b:.1f} ms   {rel(t8a, t8b):+.2f}%")

            csv_rows.append(["d3_evaluate", "1p", f"{ta:.1f}", f"{tc:.1f}",
                             f"{rel(ta, tc):.2f}", "", "", str(d3[(1, "baseline")]["n"])])
            for name in ("scale", "neg", "vector_muls", "nodes"):
                csv_rows.append([f"d3_ast_{name}", "1p", str(a.get(name, 0)),
                                 str(b.get(name, 0)), "", "", "", ""])

            # -- the three layers, and what gets absorbed ------------------
            emit("\n[ANSWER] how much of F2's hot multiplication share goes away")
            removed = a["vector_muls"] - b["vector_muls"]
            emit(f"  layer 1 (count)      {removed} of {a['vector_muls']} whole-chunk muls "
                 f"gone = {removed / a['vector_muls'] * 100:.1f}%")
            ev_delta = rel(ta, tc)
            emit(f"  layer 2 (evaluator)  {ev_delta:+.2f}% on the quotient evaluate call")
            if 1 in d1:
                e2e = d1[1]["delta"]
                emit(f"  layer 3 (end-to-end) {e2e:+.2f}% on 1P prove")
                # What layers 1-2 would predict end-to-end, given how much of the
                # proof is quotient and how much of quotient is evaluate.
                if (1, "quotient") in d2:
                    q = d2[(1, "quotient")]
                    share_eval = ta / (q["a"] * 1000) if q["a"] else 0
                    share_q = q["a"] / d1[1]["a"] if d1[1]["a"] else 0
                    expected = ev_delta * share_eval * share_q
                    emit(f"  expected from 2 x share   {expected:+.2f}%  "
                         f"(evaluate/quotient {share_eval*100:.1f}%, "
                         f"quotient/prove {share_q*100:.1f}%)")
                    emit(f"  absorbed (expected - actual) {expected - e2e:+.2f} pp")
    else:
        emit("  (no mechanism dataset)")

    # -- write -------------------------------------------------------------
    out_csv = os.path.join(args.root, "comparison.csv")
    with open(out_csv, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("dataset,threads,a,b,delta_pct,ci_lo_pct,ci_hi_pct,pairs\n")
        for row in csv_rows:
            fh.write(",".join(row) + "\n")
    out_txt = os.path.join(args.root, "summary.txt")
    with open(out_txt, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nwrote {out_csv}\nwrote {out_txt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
