#!/usr/bin/env python3
"""Stage E3 analysis: turn the sampler's raw/per-stage CSVs into the E3 evidence tables.

Inputs (produced by the in-process sampler in experiments/transaction_e2e/harness/src/e3sampler.rs):
  <root>/1p/profile/{hot_functions,stage_samples,stage_functions,stage_source_files,
                     source_files,hot_lines,hot_callers,threads}.csv
  <root>/8p/profile/...   (same set)

Outputs:
  <root>/comparison.csv         per-stage: CPU-sample share and implied parallel width, 1P vs 8P
  <root>/hot_functions_compare.csv   top functions: 1P/8P self share, stage, source file
  <root>/operation_categories.csv    coarse activity buckets (FFT / MSM / field / curve / ...)
  <root>/summary.txt                 human-readable roll-up used for the document

Methodology notes baked into the numbers:
  * "busy" samples exclude ticks whose leaf frame is a wait/sleep primitive, so
    percentages are CPU (thread) time shares, not wall-clock shares.
  * sum(busy) over threads = total CPU time; wall time = window length. The ratio
    is the achieved parallel width, reported per config.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict

STAGE_ORDER = [
    (0, "outside"),
    (1, "instance"),
    (2, "synthesis"),
    (3, "advice_commit"),
    (4, "eval_setup"),
    (5, "lookup_permute"),
    (6, "permutation"),
    (7, "lookup_product"),
    (8, "quotient"),
    (9, "opening"),
]

# Coarse activity buckets. Ordered: first match wins.
CATEGORY_RULES = [
    ("constant-time barrier", lambda f, s: "subtle::black_box" in f),
    ("idle/wait", lambda f, s: f.startswith("Nt") or f.startswith("Zw") or "WaitFor" in f),
    ("FFT", lambda f, s: "fft" in f.lower() or "butterfly" in f.lower()),
    ("MSM", lambda f, s: "Buckets" in f or "msm" in f.lower() or "multiexp" in f.lower()),
    ("IPA / polynomial opening", lambda f, s: "multiopen" in f.lower() or "commitment" in f.lower()),
    ("expression evaluation", lambda f, s: "evaluator" in f.lower()),
    ("field arithmetic", lambda f, s: "fields" in s or "Fp::" in f or "Fq::" in f or "montgomery" in f.lower()),
    ("curve arithmetic", lambda f, s: "curves" in s or "curves::impl" in f),
    ("memory / allocation", lambda f, s: any(k in s for k in ("alloc", "vec/", "hashbrown", "memchr"))
                                        or any(k in f for k in ("memcpy", "memset", "alloc::"))),
]


def classify(func: str, src: str) -> str:
    for name, pred in CATEGORY_RULES:
        try:
            if pred(func, src):
                return name
        except Exception:
            pass
    return "other"


def read_csv(path: str):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_profile(root: str):
    p = os.path.join(root, "profile")
    return {
        "stages": read_csv(os.path.join(p, "stage_samples.csv")),
        "stage_funcs": read_csv(os.path.join(p, "stage_functions.csv")),
        "stage_files": read_csv(os.path.join(p, "stage_source_files.csv")),
        "funcs": read_csv(os.path.join(p, "hot_functions.csv")),
        "files": read_csv(os.path.join(p, "source_files.csv")),
        "lines": read_csv(os.path.join(p, "hot_lines.csv")),
        "callers": read_csv(os.path.join(p, "hot_callers.csv")),
        "threads": read_csv(os.path.join(p, "threads.csv")),
    }


def short(fn: str, limit: int = 88) -> str:
    """Trim Rust generic soup to `path<...>` head so tables stay readable."""
    fn = re.sub(r"::impl\$\d+", "", fn)
    if len(fn) <= limit:
        return fn
    cut = fn.find("<")
    if 0 < cut <= limit:
        return fn[:cut] + "<...>"
    return fn[: limit - 4] + "..."


def window_stats(root: str):
    """Pull wall time and proof count from the sampler log (PROFILE_WINDOW_END)."""
    log = os.path.join(root, "raw", "run.log")
    out = {"wall_s": None, "reps": None, "per_proof_ms": None, "ticks": None, "busy": None,
           "threads": None, "mean_busy_threads": None}
    if not os.path.exists(log):
        return out
    for line in open(log, encoding="utf-8", errors="replace"):
        if line.startswith("PROFILE_WINDOW_END"):
            m = re.search(r"wall_s=([\d.]+) reps=(\d+) per_proof_ms=([\d.]+)", line)
            if m:
                out["wall_s"] = float(m.group(1))
                out["reps"] = int(m.group(2))
                out["per_proof_ms"] = float(m.group(3))
        elif "E3 sampler: summary" in line:
            m = re.search(
                r"ticks=(\d+) busy=(\d+)[^(]*\(([\d.]+)%\) threads=(\d+) mean_busy_threads=([\d.]+)",
                line,
            )
            if m:
                out["ticks"] = int(m.group(1))
                out["busy"] = int(m.group(2))
                out["threads"] = int(m.group(4))
                out["mean_busy_threads"] = float(m.group(5))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="results/transaction_e2e/e3_profile")
    args = ap.parse_args()
    root = args.root

    data = {"1p": load_profile(os.path.join(root, "1p")), "8p": load_profile(os.path.join(root, "8p"))}
    stats = {"1p": window_stats(os.path.join(root, "1p")), "8p": window_stats(os.path.join(root, "8p"))}

    lines_out: list[str] = []

    def emit(s: str = "") -> None:
        print(s)
        lines_out.append(s)

    emit("=" * 78)
    emit("Stage E3 — CPU sampling roll-up")
    emit("=" * 78)
    for cfg in ("1p", "8p"):
        st = stats[cfg]
        emit(
            f"[{cfg}] window={st['wall_s']}s proofs={st['reps']} per_proof={st['per_proof_ms']}ms "
            f"ticks={st['ticks']} busy={st['busy']} threads={st['threads']} "
            f"mean_busy_threads={st['mean_busy_threads']}"
        )
    emit()

    # ---- per-stage comparison -------------------------------------------
    by_stage = {}
    for cfg in ("1p", "8p"):
        d = {}
        for r in data[cfg]["stages"]:
            d[int(r["stage_code"])] = (int(r["busy_samples"]), float(r["busy_pct_of_all_busy"]))
        by_stage[cfg] = d

    cmp_path = os.path.join(root, "comparison.csv")
    with open(cmp_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "stage_code", "stage",
            "busy_1p", "cpu_share_1p_pct", "cpu_s_per_proof_1p",
            "busy_8p", "cpu_share_8p_pct", "cpu_s_per_proof_8p",
            "share_delta_pp", "cpu_time_ratio_1p_over_8p",
        ])
        # seconds represented by one sample = window / rounds, rounds = ticks / threads.
        secs = {}
        for cfg in ("1p", "8p"):
            st = stats[cfg]
            if st["ticks"] and st["threads"] and st["wall_s"] and st["reps"]:
                secs[cfg] = st["wall_s"] * st["threads"] / st["ticks"] / st["reps"]
            else:
                secs[cfg] = float("nan")

        emit("Per-stage CPU (thread) time share and absolute CPU seconds per proof")
        emit(f"{'stage':<16}{'1P%':>8}{'1P s/pf':>9}{'8P%':>8}{'8P s/pf':>9}{'CPU 1P/8P':>11}")
        for code, name in STAGE_ORDER:
            b1, p1 = by_stage["1p"].get(code, (0, 0.0))
            b8, p8 = by_stage["8p"].get(code, (0, 0.0))
            s1 = b1 * secs["1p"] if secs["1p"] == secs["1p"] else float("nan")
            s8 = b8 * secs["8p"] if secs["8p"] == secs["8p"] else float("nan")
            ratio = (s1 / s8) if s8 and s8 == s8 else float("nan")
            w.writerow([code, name, b1, f"{p1:.3f}", f"{s1:.4f}" if s1 == s1 else "",
                        b8, f"{p8:.3f}", f"{s8:.4f}" if s8 == s8 else "",
                        f"{p8 - p1:.3f}", f"{ratio:.3f}" if ratio == ratio else ""])
            emit(f"{name:<16}{p1:>8.2f}{s1:>9.3f}{p8:>8.2f}{s8:>9.3f}{ratio:>11.2f}")

        tot1 = sum(b for b, _ in by_stage["1p"].values()) * secs["1p"]
        tot8 = sum(b for b, _ in by_stage["8p"].values()) * secs["8p"]
        emit(f"{'TOTAL':<16}{100.0:>8.2f}{tot1:>9.3f}{100.0:>8.2f}{tot8:>9.3f}"
             f"{tot1 / tot8 if tot8 else float('nan'):>11.2f}")
        emit(f"  wall per proof: 1P {stats['1p']['per_proof_ms']}ms, 8P {stats['8p']['per_proof_ms']}ms"
             f"  -> wall speed-up {stats['1p']['per_proof_ms'] / stats['8p']['per_proof_ms']:.2f}x")
        emit(f"  CPU per proof:  1P {tot1:.3f}s, 8P {tot8:.3f}s"
             f"  -> extra CPU work in 8P {tot8 / tot1:.2f}x"
             f"  (achieved parallel width {stats['8p']['mean_busy_threads']} of 8 cores)")
    emit()

    # ---- function-level comparison --------------------------------------
    def func_index(cfg: str):
        idx = {}
        for r in data[cfg]["funcs"]:
            key = r["function"]
            if idx.get(key, {"busy": 0})["busy"] < int(r["busy_exclusive_samples"]):
                idx[key] = {"busy": int(r["busy_exclusive_samples"]), "pct": float(r["busy_self_pct"])}
        # attach dominant stage + source file from stage_functions.csv
        stage_of, file_of = {}, {}
        for r in data[cfg]["stage_funcs"]:
            fn = r["function"]
            n = int(r["busy_self_samples"])
            if n > stage_of.get(fn, (0, ""))[0]:
                stage_of[fn] = (n, r["stage"])
            if n > file_of.get(fn, (0, ""))[0]:
                file_of[fn] = (n, r["source_file"])
        for fn, v in idx.items():
            v["stage"] = stage_of.get(fn, (0, "?"))[1]
            v["file"] = file_of.get(fn, (0, ""))[1]
        return idx

    i1, i8 = func_index("1p"), func_index("8p")
    all_fns = sorted(set(i1) | set(i8), key=lambda f: -(i1.get(f, {}).get("busy", 0) + i8.get(f, {}).get("busy", 0)))
    fc_path = os.path.join(root, "hot_functions_compare.csv")
    with open(fc_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["function", "short_name", "stage", "source_file",
                    "self_pct_1p", "self_pct_8p", "delta_pp", "category"])
        emit("Top hot functions by CPU self time (1P ranked; 8P share for comparison)")
        emit(f"{'self% 1P':>9}{'self% 8P':>10}{'Δpp':>7}  {'stage':<14}{'category':<24}function")
        for fn in all_fns:
            a, b = i1.get(fn, {}), i8.get(fn, {})
            p1, p8 = a.get("pct", 0.0), b.get("pct", 0.0)
            if p1 < 0.5 and p8 < 0.5:
                continue
            stage = a.get("stage") or b.get("stage") or "?"
            src = a.get("file") or b.get("file") or ""
            cat = classify(fn, src)
            w.writerow([fn, short(fn), stage, src, f"{p1:.3f}", f"{p8:.3f}", f"{p8 - p1:.3f}", cat])
            if len(lines_out) < 400:
                emit(f"{p1:>9.2f}{p8:>10.2f}{p8 - p1:>+7.2f}  {stage:<14}{cat:<24}{short(fn, 70)}")
    emit()

    # ---- category roll-up ------------------------------------------------
    cat_path = os.path.join(root, "operation_categories.csv")
    with open(cat_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["category", "self_pct_1p", "self_pct_8p", "delta_pp"])
        agg = {"1p": defaultdict(float), "8p": defaultdict(float)}
        for cfg, idx in (("1p", i1), ("8p", i8)):
            for fn, v in idx.items():
                agg[cfg][classify(fn, v.get("file", ""))] += v.get("pct", 0.0)
        cats = sorted(set(agg["1p"]) | set(agg["8p"]), key=lambda c: -agg["1p"].get(c, 0.0))
        emit("Activity categories (sum of function self shares)")
        emit(f"{'category':<28}{'1P%':>8}{'8P%':>8}{'Δpp':>8}")
        for c in cats:
            a, b = agg["1p"].get(c, 0.0), agg["8p"].get(c, 0.0)
            w.writerow([c, f"{a:.3f}", f"{b:.3f}", f"{b - a:.3f}"])
            emit(f"{c:<28}{a:>8.2f}{b:>8.2f}{b - a:>+8.2f}")
    emit()

    # ---- per-stage category mix (answers "what is quotient actually doing") --
    mix_path = os.path.join(root, "stage_category_mix.csv")
    with open(mix_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["config", "stage", "category", "self_pct_within_stage"])
        emit("Within-stage category mix (busy self share inside each stage)")
        for cfg in ("1p", "8p"):
            stage_tot = defaultdict(float)
            stage_cat = defaultdict(float)
            for r in data[cfg]["stage_funcs"]:
                key = (r["stage"], classify(r["function"], r["source_file"]))
                stage_cat[key] += float(r["pct_within_stage"])
                stage_tot[r["stage"]] += float(r["pct_within_stage"])
            for code, name in STAGE_ORDER:
                items = [(c, v) for (s, c), v in stage_cat.items() if s == name and v >= 1.0]
                if not items:
                    continue
                items.sort(key=lambda kv: -kv[1])
                summary = ", ".join(f"{c} {v:.0f}%" for c, v in items[:4])
                w.writerow([cfg, name, "; ".join(f"{c}:{v:.1f}" for c, v in items)])
                emit(f"  [{cfg}] {name:<15} {summary}")
        emit()

    # ---- top source lines ------------------------------------------------
    emit("Top source lines (1P)")
    for r in data["1p"]["lines"][:10]:
        emit(f"  {float(r['pct_of_all_busy']):5.2f}%  {r['source_file']}:{r['line']}  {short(r['function'], 60)}")
    emit()

    emit("Caller attribution — who invokes the biggest leaf hotspots (1P)")
    for r in data["1p"]["callers"][:10]:
        emit(f"  {r['busy_samples']:>4}  {short(r['callee'], 42):<42} <- {short(r['caller'], 50)}")
    emit()

    with open(os.path.join(root, "summary.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines_out) + "\n")
    print(f"\nwrote: {cmp_path}\n       {fc_path}\n       {cat_path}\n       {mix_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
