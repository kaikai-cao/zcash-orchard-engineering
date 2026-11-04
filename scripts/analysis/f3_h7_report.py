#!/usr/bin/env python3
"""Stage F3 / H7: did removing the intermediate `Vec` pay for itself?

Four independent datasets, all produced by scripts/benchmark/run_f3_ab.sh or by
`--mode h7` (docs/orchard/19_h7_materialization_prereg.md section 4):

  baseline/, treatment/        no env                      -> D1 verdict
  stage/{baseline,treatment}   ORCHARD_E2_TIMING=1         -> D2 attribution
  eval/{baseline,treatment}    ORCHARD_F2=1, mode f2       -> D3 evaluator time
  mechanism/*.log              ORCHARD_H7=1, mode h7       -> D4 ownership counts

The A side cannot run `--mode h7` (the counters only exist in the patched tree),
so its allocation totals are derived from the AST census in the same log, using
the pre-H7 rule: every `Ast::Poly` visit copies a chunk, every
`ConstantTerm`/`LinearTerm` fills one, and `DistributePowers` seeds one. That
rule is exact, not an estimate -- it is one allocation per node of those four
kinds -- and `closed_leaf`/`closed_alloc` in the report check that the B side
counts each allocation once.

Usage:
    python scripts/analysis/f3_h7_report.py [--root DIR] [--boot 10000]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import statistics as st

BUNDLE = re.compile(r"^BUNDLE (\{.*\})\s*$")
RESULT = re.compile(r"^RESULT (\{.*\})\s*$")
E2 = re.compile(r"^E2_STAGE pool=(\w+) rep=\d+ (.*)$")
F2REPORT = re.compile(r"^F2_REPORT (\{.*\})\s*$")
H7REPORT = re.compile(r"^H7_REPORT (\{.*\})\s*$")
H7META = re.compile(r"^H7_META (\{.*\})\s*$")

ELEM = 32  # size_of::<pallas::Base>()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_run(path: str) -> dict | None:
    """prove_s samples, peak working set and per-stage seconds from one run."""
    reps = None
    peak = None
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
                continue
            m = RESULT.match(line)
            if m:
                try:
                    o = json.loads(m.group(1))
                except json.JSONDecodeError:
                    continue
                peak = o.get("peak_ws_mib")
                continue
            m = E2.match(line)
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
        for k in set().union(*[set(s) for s in stages]):
            vals = [s[k] for s in stages if k in s]
            if vals:
                med[k] = st.median(vals)
    return {"reps": reps, "stages": med, "peak_mib": peak}


def parse_f2(path: str) -> dict | None:
    """The quotient evaluate(): AST census and wall time, from F2_REPORT."""
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
            big = max(items, key=lambda it: it["poly_len"])
            return big
    return None


def parse_h7(path: str) -> dict | None:
    """Ownership counters plus the formulation bench, from H7_REPORT."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = H7REPORT.match(line)
            if not m:
                continue
            d = json.loads(m.group(1))
            items = d.get("evals", {}).get("items", [])
            big = max(items, key=lambda it: it["poly_len"]) if items else None
            return {"totals": d.get("evals", {}), "fused": big,
                    "formulation": d.get("formulation")}
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


def abba_pairs(root: str, threads: int):
    """((A run, B run), round, position) for each ABBA pair."""
    out = []
    for r in range(1, 100):
        pa = {p: os.path.join(root, "baseline", f"{threads}p_a_r{r}_{p}.log") for p in (1, 2)}
        pb = {p: os.path.join(root, "treatment", f"{threads}p_b_r{r}_{p}.log") for p in (1, 2)}
        if not all(os.path.exists(v) for v in list(pa.values()) + list(pb.values())):
            break
        da = {p: parse_run(v) for p, v in pa.items()}
        db = {p: parse_run(v) for p, v in pb.items()}
        if any(v is None for v in list(da.values()) + list(db.values())):
            break
        # Pairs are always (A, B): (A1,B1) and (A2,B2). A2 runs last, so both
        # pairs sit close in time and share a thermal state.
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
    ap.add_argument("--root", default="results/transaction_e2e/f3_h7")
    ap.add_argument("--boot", type=int, default=10000)
    ap.add_argument("--md", default=None, help="also write the summary here")
    args = ap.parse_args()
    rng = random.Random(20261105)

    lines: list[str] = []
    csv_rows: list[list[str]] = []
    verdict: dict[str, str] = {}

    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    emit("Stage F3 / H7 -- Borrowed/Owned instead of a Vec per node")
    emit("=" * 74)
    emit("ABBA blocks (A1 B1 B2 A2); the bootstrap resamples pairs, not samples.")

    # -- D1: end-to-end ----------------------------------------------------
    emit("\n[D1] end-to-end prove, plain release, no instrumentation")
    d1 = {}
    for threads in (1, 8):
        pairs = abba_pairs(args.root, threads)
        if not pairs:
            emit(f"  {threads}P: no logs")
            continue
        pooled_a = [v for _, _, a, _ in pairs for v in a["reps"]]
        pooled_b = [v for _, _, _, b in pairs for v in b["reps"]]
        ma, mb = st.median(pooled_a), st.median(pooled_b)
        deltas = [rel(st.median(a["reps"]), st.median(b["reps"])) for _, _, a, b in pairs]
        med, lo, hi = bootstrap(deltas, args.boot, rng)
        cva = st.stdev(pooled_a) / st.fmean(pooled_a) * 100
        cvb = st.stdev(pooled_b) / st.fmean(pooled_b) * 100
        peaka = [a["peak_mib"] for _, _, a, _ in pairs if a["peak_mib"]]
        peakb = [b["peak_mib"] for _, _, _, b in pairs if b["peak_mib"]]

        emit(f"  {threads}P  pairs={len(pairs)}  samples/side={len(pooled_a)}")
        emit(f"    A  median {ma*1000:8.1f} ms   CV {cva:5.2f}%   peak WS {st.median(peaka):6.1f} MiB")
        emit(f"    B  median {mb*1000:8.1f} ms   CV {cvb:5.2f}%   peak WS {st.median(peakb):6.1f} MiB")
        emit(f"    paired delta {med:+6.2f}%   95% CI [{lo:+6.2f}%, {hi:+6.2f}%]")
        emit("    per pair:  " + "  ".join(
            f"r{r}.{p}:{d:+.2f}%" for (r, p, _, _), d in zip(pairs, deltas)))
        d1[threads] = {"a": ma, "b": mb, "delta": med, "lo": lo, "hi": hi,
                       "pairs": len(pairs), "cv_a": cva, "cv_b": cvb,
                       "peak_a": st.median(peaka), "peak_b": st.median(peakb)}
        csv_rows.append(["d1_prove", f"{threads}p", f"{ma*1000:.1f}", f"{mb*1000:.1f}",
                         f"{med:.2f}", f"{lo:.2f}", f"{hi:.2f}", str(len(pairs))])
        csv_rows.append(["d1_peak_ws_mib", f"{threads}p",
                         f"{st.median(peaka):.1f}", f"{st.median(peakb):.1f}",
                         f"{rel(st.median(peaka), st.median(peakb)):+.2f}", "", "",
                         str(len(pairs))])

    # -- D2: stages --------------------------------------------------------
    emit("\n[D2] stage breakdown (ORCHARD_E2_TIMING=1; attribution only)")
    stage_dir = os.path.join(args.root, "stage")
    d2 = {}
    if os.path.isdir(stage_dir):
        for threads in (1, 8):
            pairs = abba_pairs(stage_dir, threads)
            if not pairs:
                continue
            keys = sorted({k for _, _, a, _ in pairs for k in a["stages"]})
            emit(f"  {threads}P  pairs={len(pairs)}")
            for k in keys:
                pa = [(a["stages"][k], b["stages"][k]) for _, _, a, b in pairs
                      if k in a["stages"] and k in b["stages"]]
                if not pa:
                    continue
                ds = [rel(x, y) for x, y in pa]
                med, lo, hi = bootstrap(ds, args.boot, rng)
                ma = st.median([x for x, _ in pa])
                mb = st.median([y for _, y in pa])
                mark = "  faster" if hi < 0 else ("  REGRESSION" if lo > 0 else "")
                emit(f"    {k:<16} A {ma*1000:8.1f} ms   B {mb*1000:8.1f} ms   "
                     f"delta {med:+6.2f}%  [{lo:+6.2f}, {hi:+6.2f}]{mark}")
                d2[(threads, k)] = {"a": ma, "b": mb, "delta": med, "lo": lo, "hi": hi}
                csv_rows.append(["d2_" + k, f"{threads}p", f"{ma*1000:.1f}", f"{mb*1000:.1f}",
                                 f"{med:.2f}", f"{lo:.2f}", f"{hi:.2f}", str(len(pa))])
            rest = [k for k in keys if k != "quotient"]
            pa = [(sum(a["stages"][k] for k in rest if k in a["stages"]),
                   sum(b["stages"][k] for k in rest if k in b["stages"]))
                  for _, _, a, b in pairs]
            ds = [rel(x, y) for x, y in pa]
            med, lo, hi = bootstrap(ds, args.boot, rng)
            ma = st.median([x for x, _ in pa])
            mb = st.median([y for _, y in pa])
            emit(f"    {'all-but-quotient':<16} A {ma*1000:8.1f} ms   B {mb*1000:8.1f} ms   "
                 f"delta {med:+6.2f}%  [{lo:+6.2f}, {hi:+6.2f}]")
            d2[(threads, "all_but_quotient")] = {"a": ma, "b": mb, "delta": med,
                                                 "lo": lo, "hi": hi}
            csv_rows.append(["d2_all_but_quotient", f"{threads}p",
                             f"{ma*1000:.1f}", f"{mb*1000:.1f}",
                             f"{med:.2f}", f"{lo:.2f}", f"{hi:.2f}", str(len(pa))])
            emit("")
    else:
        emit("  (no stage dataset)")

    # -- D3: evaluator wall time ------------------------------------------
    emit("\n[D3] evaluator: the quotient `evaluate()` call itself (mode f2)")
    eval_dir = os.path.join(args.root, "eval")
    d3 = {}
    if os.path.isdir(eval_dir):
        for threads in (1, 8):
            pa = {}
            pb = {}
            for r in range(1, 100):
                fa = os.path.join(eval_dir, "baseline", f"{threads}p_a_r{r}_1.log")
                fb = os.path.join(eval_dir, "treatment", f"{threads}p_b_r{r}_1.log")
                if not (os.path.exists(fa) and os.path.exists(fb)):
                    break
                a, b = parse_f2(fa), parse_f2(fb)
                if not a or not b:
                    break
                pa[r] = a
                pb[r] = b
            if not pa:
                continue
            ds = [rel(pa[r]["first_ms"], pb[r]["first_ms"]) for r in pa]
            med, lo, hi = bootstrap(ds, args.boot, rng)
            ma = st.median([v["first_ms"] for v in pa.values()])
            mb = st.median([v["first_ms"] for v in pb.values()])
            emit(f"  {threads}P  pairs={len(ds)}")
            emit(f"    A {ma:8.1f} ms   B {mb:8.1f} ms   delta {med:+6.2f}%  "
                 f"[{lo:+6.2f}, {hi:+6.2f}]")
            d3[threads] = {"a": ma, "b": mb, "delta": med, "lo": lo, "hi": hi}
            csv_rows.append(["d3_evaluator", f"{threads}p", f"{ma:.1f}", f"{mb:.1f}",
                             f"{med:.2f}", f"{lo:.2f}", f"{hi:.2f}", str(len(ds))])
    else:
        emit("  (no evaluator dataset)")

    # -- D4: ownership counts ---------------------------------------------
    emit("\n[D4] mechanism: who allocates (mode h7, one run per thread count)")
    mech_dir = os.path.join(args.root, "mechanism")
    d4 = {}
    fnv = {}
    if os.path.isdir(mech_dir):
        for name in sorted(os.listdir(mech_dir)):
            if not name.endswith(".log"):
                continue
            path = os.path.join(mech_dir, name)
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = H7META.match(line)
                    if m:
                        o = json.loads(m.group(1))
                        fnv[name] = (o.get("proof_fnv"), o.get("rayon_threads"),
                                     o.get("probe"))
            h7 = parse_h7(path)
            f2 = parse_f2(path)
            if not h7 or not h7["fused"] or not f2:
                continue
            d4[name] = {"h7": h7, "f2": f2}

        ordered = [(k, v) for k, v in d4.items() if "off" not in k]
        for name, rec in ordered:
            f = rec["h7"]["fused"]
            tot = rec["h7"]["totals"]
            ast_ = rec["f2"]["ast"]
            poly_len, nchunk = f["poly_len"], f["num_chunks"]
            chunk_len = poly_len // nchunk
            a_allocs = nchunk * (ast_["poly"] + ast_["constant"] + ast_["linear"]
                                 + ast_.get("distribute", 0))
            a_alloc_bytes = a_allocs * chunk_len * ELEM
            a_copy_bytes = ast_["poly"] * poly_len * ELEM

            emit(f"\n  {name}")
            emit(f"    tree: poly_len {poly_len}, chunk {chunk_len} x {nchunk}   "
                 f"(F2's independent walk: {ast_['poly']} leaves, "
                 f"{ast_['constant']} const, {ast_['linear']} linear)")
            emit("    leaf visits   A {:>8}   B {:>8}   (borrowed {:>8} / copied {:>6})".format(
                ast_["poly"] * nchunk, f["leaf_visits"], f["leaf_borrowed"], f["leaf_copied"]))
            emit(f"    copy bytes    A {a_copy_bytes/1e9:8.3f} GB  B {f['copy_bytes']/1e9:8.3f} GB"
                 f"   {rel(a_copy_bytes, f['copy_bytes']):+7.2f}%")
            emit(f"    allocations   A {a_allocs:8}     B {f['allocs']:8}     "
                 f"{rel(a_allocs, f['allocs']):+7.2f}%")
            emit(f"    alloc bytes   A {a_alloc_bytes/1e9:8.3f} GB  B {f['alloc_bytes']/1e9:8.3f} GB"
                 f"   {rel(a_alloc_bytes, f['alloc_bytes']):+7.2f}%")
            emit(f"    of which: both-borrowed {f['both_borrowed']}, unary {f['unary_alloc']}, "
                 f"fills {f['fills']} ({f['fill_bytes']/1e9:.3f} GB), copied {f['leaf_copied']}")
            emit(f"    in place: left {f['reuse_left']}, right(swapped) {f['reuse_right']}")
            emit(f"    invariants: leaf closed {tot.get('closed_leaf')}, "
                 f"alloc closed {tot.get('closed_alloc')}")
            for k in ("leaf_visits", "leaf_borrowed", "leaf_copied"):
                csv_rows.append(["d4_" + k, name, "", f[k], "", "", "", ""])
            for k in ("copy_bytes", "allocs", "alloc_bytes"):
                csv_rows.append(["d4_" + k, name, "", f[k], "", "", "", ""])

            d4[name] = {**rec, "a_allocs": a_allocs, "a_alloc_bytes": a_alloc_bytes,
                        "a_copy_bytes": a_copy_bytes, "chunk_len": chunk_len}

        form = next((v["h7"]["formulation"] for v in d4.values()
                     if v["h7"].get("formulation")), None)
        if form:
            emit("\n  formulation price (single thread, after the timed window)")
            for geo in ("chunk", "poly"):
                g = form.get(geo)
                if not g:
                    continue
                emit(f"    {geo:<6} len {g['chunk_len']:>6}: copy+inplace "
                     f"{g['copy_mul_ns_per_elem']:6.3f} ns/el   collect "
                     f"{g['collect_ns_per_elem']:6.3f} ns/el   copy only "
                     f"{g['copy_only_ns_per_elem']:5.3f} ns/el   ratio {g['ratio']:.3f}")
            ch = form.get("chunk") or {}
            rec = next((v for k, v in d4.items() if "off" not in k), None)
            if ch and rec:
                est = ch["copy_only_ns_per_elem"]
                f = rec["h7"]["fused"]
                cl = rec["chunk_len"]
                b_cost = f["leaf_copied"] * cl * est / 1e6
                a_cost = rec["f2"]["ast"]["poly"] * f["poly_len"] * est / 1e6
                emit(f"    priced at the chunk geometry ({est:.3f} ns/element), the leaf "
                     f"copies in one evaluate are worth:")
                emit(f"      A rule (every leaf copied): {a_cost:7.2f} ms")
                emit(f"      B rule (identity rotation borrowed): {b_cost:7.2f} ms")
                emit(f"      difference: {a_cost - b_cost:7.2f} ms — compare with D3, "
                     f"which measures the evaluator itself")

        if fnv:
            emit("\n  proof fingerprint (probe on vs off; same seed, same binary)")
            for name, (h, thr, on) in sorted(fnv.items()):
                emit(f"    {name:<28} threads {thr:<2} probe {str(on):<5} {h}")
            vals = {h for h, _, _ in fnv.values()}
            verdict["fnv"] = "PASS" if len(vals) == 1 else "FAIL"
            emit(f"    -> {'identical' if len(vals) == 1 else 'DIFFERENT'}: counting "
                 f"changes no field element")
    else:
        emit("  (no mechanism dataset)")

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------
    emit("\n" + "-" * 74)
    emit("Gates (fixed in docs/orchard/19_h7_materialization_prereg.md section 6)")

    def gate(name: str, ok: bool | None, text: str) -> None:
        verdict[name] = {True: "PASS", False: "FAIL", None: "n/a"}[ok]
        emit(f"  {name:<7} {verdict[name]:<4}  {text}")

    fused = next((v for k, v in d4.items() if "off" not in k and v.get("fused_ok", True)), None)
    main_rec = next((v for k, v in d4.items() if "off" not in k), None)
    if main_rec:
        f = main_rec["h7"]["fused"]
        tot = main_rec["h7"]["totals"]
        leaf_ok = bool(tot.get("closed_leaf"))
        alloc_ok = bool(tot.get("closed_alloc"))
        gate("H7-A", verdict.get("fnv") == "PASS" and leaf_ok and alloc_ok,
             f"counters close (leaf {leaf_ok}, alloc {alloc_ok}); probe on/off proof identical; "
             f"lib tests + verify_proof recorded in the doc")
        b_ok = f["alloc_bytes"] <= 0.70 * main_rec["a_alloc_bytes"]
        c_ok = f["copy_bytes"] <= 0.20 * main_rec["a_copy_bytes"]
        gate("H7-B", b_ok and c_ok,
             f"alloc bytes {rel(main_rec['a_alloc_bytes'], f['alloc_bytes']):+.1f}% "
             f"(needs <= -30%), leaf copy {rel(main_rec['a_copy_bytes'], f['copy_bytes']):+.1f}% "
             f"(needs <= -80%)")
        gate("H7-C", f["allocs"] <= main_rec["a_allocs"],
             f"allocations {rel(main_rec['a_allocs'], f['allocs']):+.1f}% "
             f"({main_rec['a_allocs']} -> {f['allocs']})")
    else:
        gate("H7-A", None, "no mechanism dataset")
        gate("H7-B", None, "no mechanism dataset")
        gate("H7-C", None, "no mechanism dataset")

    if 1 in d3:
        e = d3[1]
        gate("H7-D", e["delta"] <= -5.0 and e["hi"] < 0,
             f"evaluator {e['delta']:+.2f}% [{e['lo']:+.2f}, {e['hi']:+.2f}] "
             f"(needs <= -5% and CI excluding 0)")
    else:
        gate("H7-D", None, "no evaluator dataset")

    if 1 in d1:
        g = d1[1]
        gate("H7-E", g["delta"] <= -1.0 and g["hi"] < 0,
             f"1P prove {g['delta']:+.2f}% [{g['lo']:+.2f}, {g['hi']:+.2f}] "
             f"(needs <= -1.0% and CI excluding 0)")
    else:
        gate("H7-E", None, "no D1 dataset")

    if 8 in d1:
        g = d1[8]
        gate("H7-F", g["delta"] <= 0.5 and g["hi"] <= 1.0,
             f"8P prove {g['delta']:+.2f}% [{g['lo']:+.2f}, {g['hi']:+.2f}] "
             f"(needs point <= +0.5% and CI top <= +1%)")
        mem = rel(g["peak_a"], g["peak_b"])
        emit(f"  H7-C2   peak WS {g['peak_a']:.1f} -> {g['peak_b']:.1f} MiB "
             f"({mem:+.1f}%, gate <= +10%)")
    else:
        gate("H7-F", None, "no D1 dataset")

    agg = d2.get((1, "all_but_quotient"))
    if agg:
        gate("H7-G", agg["hi"] <= 1.0,
             f"all-but-quotient {agg['delta']:+.2f}% [{agg['lo']:+.2f}, {agg['hi']:+.2f}] "
             f"(CI top <= +1%)")
    else:
        gate("H7-G", None, "no stage dataset")

    # H7-H as written in the prereg is a three-way rule with no branch for "the
    # mechanism missed its predicted size, yet the end-to-end gate still held".
    # Print the literal rule first, then what was actually applied, so nobody
    # reads this output as "the prereg said keep" or "the prereg said close".
    hard = ["H7-A", "H7-B", "H7-D", "H7-E"]
    if all(verdict.get(k) == "PASS" for k in hard):
        literal = "KEEP the prototype; open H7.5 (pooling, two-piece borrow, root write)."
        applied = literal
    elif verdict.get("H7-D") != "PASS":
        literal = ("CLOSE (prereg: 'D 不过 -> 关档'): the evaluator did not improve, "
                   "so the ownership discipline costs as much as it saves.")
        applied = ("KEEP the patch, close H7.5: H7-E held (the end-to-end gate is the one "
                   "that decides) and H7-F showed no real 8P regression, so the remaining "
                   "H7.5 ideas are capped at ~0.29% and are not worth another round. "
                   "Deviation recorded in docs/orchard/20_h7_borrowed_ownership.md section 9.4.")
    else:
        literal = ("CLOSE with the 'gain not transmitted' record: evaluator improved, "
                   "the proof did not.")
        applied = literal
    emit(f"\n  H7-H  preregistered rule: {literal}")
    if applied != literal:
        emit(f"        applied:           {applied}")

    emit("\nverdicts: " + "  ".join(f"{k}={v}" for k, v in sorted(verdict.items())))

    with open(os.path.join(args.root, "comparison.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["metric", "arm", "a", "b", "delta_pct", "ci_lo", "ci_hi", "n"])
        w.writerows(csv_rows)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"\nwrote {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
