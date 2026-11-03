#!/usr/bin/env python3
"""Turn Stage F2 `--mode f2` logs into tables.

Input : results/transaction_e2e/f2_expression/raw_*.log  (one F2_REPORT per run)
Output: cs_nodes.csv, eval_calls.csv, terms.csv, summary.txt

The log carries a single JSON object per run, emitted by
`halo2_proofs::f2::take_report()`. Two things in there are worth care:

* `evals.items[]` has one entry per `Evaluator::evaluate` call in the proof.
  The last one is the quotient evaluation (poly_len = extended domain length);
  the others are the small Lagrange-basis evaluations for advice/fixed/instance
  values. They are counted separately on purpose - mixing them would hide that
  the quotient call is ~99% of the work.
* `terms[]` inside the big call is one row per top-level `distribute_powers`
  term, in construction order: every gate polynomial first, then the permutation
  arguments, then the lookup arguments. Splitting at `gate_polys` is therefore
  meaningful, and the script checks it: the gate segment must reproduce the
  expression-level product/scale counts from `cs`, otherwise the split is wrong
  and the script says so instead of quietly reporting nonsense.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys

GATE_GROUP = "gate"
PERM_GROUP = "permutation"
LOOKUP_GROUP = "lookup"


def load_run(path: str) -> dict | None:
    report = meta = None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("F2_REPORT "):
                report = json.loads(line[len("F2_REPORT ") :])
            elif line.startswith("F2_META "):
                meta = json.loads(line[len("F2_META ") :])
    if report is None:
        return None
    return {"report": report, "meta": meta or {}, "path": os.path.basename(path)}


def classify_terms(
    terms: list[list[int]], segments: dict, gate_polys: int
) -> tuple[list[str], int]:
    """Label each top-level term by which argument produced it.

    The fused tree is **not** "all gates, then all permutation, then all lookup".
    `prover.rs` flat_maps over circuit instances, and each instance contributes a
    block of its own: its gate polynomials, then its permutation terms, then its
    lookup terms. For a 2-action bundle the order is therefore

        [193 gate][7 perm][15 lookup] [193 gate][7 perm][15 lookup]

    so the boundaries have to be computed per block. Block sizes come from the
    prover's own counting adapters, not from a guess about how many `Ast`s each
    argument emits.
    """
    total = segments["gates"] + segments["permutation"] + segments["lookup"]
    instances = max(1, segments["gates"] // max(1, gate_polys))
    per_block = total // instances if instances else len(terms)
    g_per = segments["gates"] // instances
    p_per = segments["permutation"] // instances

    labels = []
    for i in range(len(terms)):
        k = i % per_block
        if k < g_per:
            labels.append(GATE_GROUP)
        elif k < g_per + p_per:
            labels.append(PERM_GROUP)
        else:
            labels.append(LOOKUP_GROUP)
    return labels, instances


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="results/transaction_e2e/f2_expression")
    args = ap.parse_args()
    out = args.dir

    runs = []
    for name in sorted(os.listdir(out)):
        if name.startswith("raw_") and name.endswith(".log"):
            r = load_run(os.path.join(out, name))
            if r:
                runs.append(r)
    if not runs:
        print("no F2_REPORT found", file=sys.stderr)
        return 1

    # ---- cs_nodes.csv ---------------------------------------------------
    with open(os.path.join(out, "cs_nodes.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["run", "variant", "count", "share_pct"])
        for r in runs:
            nodes = r["report"]["cs"]["nodes"]
            total = nodes["total"]
            for k, v in nodes.items():
                if k == "total":
                    continue
                w.writerow([r["path"], k, v, f"{100.0 * v / total:.3f}"])

    # ---- eval_calls.csv -------------------------------------------------
    with open(os.path.join(out, "eval_calls.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "run",
                "call_index",
                "poly_len",
                "chunk_size",
                "num_chunks",
                "nodes",
                "mul_nodes",
                "scale_nodes",
                "add_nodes",
                "poly_nodes",
                "distribute_terms",
                "vector_muls",
                "field_muls",
                "field_adds",
                "first_ms",
                "bench_median_ms",
                "bench_min_ms",
                "bench_max_ms",
            ]
        )
        for r in runs:
            for i, it in enumerate(r["report"]["evals"]["items"]):
                a = it["ast"]
                pl = it["poly_len"]
                w.writerow(
                    [
                        r["path"],
                        i,
                        pl,
                        it["chunk_size"],
                        it["num_chunks"],
                        a["nodes"],
                        a["mul"],
                        a["scale"],
                        a["add"],
                        a["poly"],
                        a["distribute_terms"],
                        a["vector_muls"],
                        a["vector_muls"] * pl,
                        a["add"] * pl,
                        f"{it['first_ms']:.3f}",
                        f"{it['bench_median_ms']:.3f}",
                        f"{it['bench_min_ms']:.3f}",
                        f"{it['bench_max_ms']:.3f}",
                    ]
                )

    # ---- terms.csv (quotient call only) ---------------------------------
    rows = []
    for r in runs:
        cs = r["report"]["cs"]
        items = r["report"]["evals"]["items"]
        big = max(items, key=lambda it: it["poly_len"])
        segments = r["report"].get("segments")
        if not segments:
            print(f"{r['path']}: no segments reported, skipping term split", file=sys.stderr)
            continue
        labels, instances = classify_terms(big["ast"]["terms"], segments, cs["gate_polys"])
        for i, (t, label) in enumerate(zip(big["ast"]["terms"], labels)):
            rows.append(
                {
                    "run": r["path"],
                    "index": i,
                    "group": label,
                    "mul": t[0],
                    "scale": t[1],
                    "poly": t[2],
                    "add": t[3],
                    "nodes": t[4],
                }
            )
    with open(os.path.join(out, "terms.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- summary --------------------------------------------------------
    lines = []
    add = lines.append
    add("Stage F2 - constraint expression accounting")
    add("=" * 72)

    first = runs[0]
    cs = first["report"]["cs"]
    add("")
    add(f"[constraint system]  ({first['path']})")
    add(f"  gates                     {cs['gates']}")
    add(f"  gate polynomials          {cs['gate_polys']}")
    add(f"  expression nodes          {cs['nodes']['total']}")
    add(
        "    product {}  sum {}  scaled {}  negated {}".format(
            cs["nodes"]["product"], cs["nodes"]["sum"], cs["nodes"]["scaled"], cs["nodes"]["negated"]
        )
    )
    add(
        "    advice {}  fixed {}  constant {}  instance {}  selector {}".format(
            cs["nodes"]["advice"],
            cs["nodes"]["fixed"],
            cs["nodes"]["constant"],
            cs["nodes"]["instance"],
            cs["nodes"]["selector"],
        )
    )
    add(f"  max depth                 {cs['max_depth']}")
    add(
        "  distinct subtrees         {}  (of {} nodes; {} repeated, {:.1f}%)".format(
            cs["distinct_subtrees"],
            cs["nodes"]["total"],
            cs["repeat_nodes"],
            100.0 * cs["repeat_nodes"] / cs["nodes"]["total"],
        )
    )
    add(
        "  leaf occurrences          {}  over {} distinct queries ({:.1f}x reuse)".format(
            cs["leaf_occ"],
            cs["leaf_distinct"],
            cs["leaf_occ"] / max(1, cs["leaf_distinct"]),
        )
    )
    add(
        "  CSE headroom (gates)      {} vector-muls saved".format(cs["cse_saving_vector_muls"])
    )
    add(
        "  degree min/max            {}/{}".format(cs["min_degree"], cs["max_degree"])
    )
    add(
        "  queries advice/fixed/inst {}/{}/{}".format(
            cs["advice_queries"], cs["fixed_queries"], cs["instance_queries"]
        )
    )

    add("")
    add("[evaluator calls]")
    for r in runs:
        ev = r["report"]["evals"]
        big = max(ev["items"], key=lambda it: it["poly_len"])
        a = big["ast"]
        add(
            f"  {r['path']}: {ev['calls']} calls, largest poly_len={big['poly_len']} "
            f"chunk={big['chunk_size']} x{big['num_chunks']}"
        )
        add(
            "    quotient ast: nodes {} (mul {} scale {} add {} poly {}), "
            "distribute_terms {}".format(
                a["nodes"], a["mul"], a["scale"], a["add"], a["poly"], a["distribute_terms"]
            )
        )
        add(
            "    vector-muls {}  -> field muls {}  |  field adds {}  |  leaf elements {}".format(
                a["vector_muls"],
                a["vector_muls"] * big["poly_len"],
                a["add"] * big["poly_len"],
                a["poly"] * big["poly_len"],
            )
        )
        if big["bench_reps"] == 0:
            # `--reps 0` runs carry no bench re-runs; quoting a 0.0 ms median
            # would look like a measurement rather than an absence of one.
            add(
                "    isolated evaluate: first {:.1f} ms (single run, no bench re-runs)".format(
                    big["first_ms"]
                )
            )
        else:
            add(
                "    isolated evaluate: first {:.1f} ms, bench median {:.1f} ms "
                "(min {:.1f}, max {:.1f}, n={})".format(
                    big["first_ms"],
                    big["bench_median_ms"],
                    big["bench_min_ms"],
                    big["bench_max_ms"],
                    big["bench_reps"],
                )
            )

    add("")
    add("[term attribution of the quotient tree]")
    # The four runs produce identical trees; summarise one of them so the counts
    # are per-proof rather than per-experiment.
    first_run = rows[0]["run"]
    by_group: dict[str, dict[str, int]] = {}
    for row in rows:
        if row["run"] != first_run:
            continue
        g = by_group.setdefault(row["group"], {"terms": 0, "mul": 0, "scale": 0, "poly": 0, "add": 0})
        g["terms"] += 1
        for k in ("mul", "scale", "poly", "add"):
            g[k] += row[k]
    for g, v in by_group.items():
        vm = v["mul"] + v["scale"]
        add(
            "  {:<12} terms {:>4}  mul {:>5}  scale {:>5}  add {:>5}  poly {:>5}  "
            "vector-muls {:>5} ({:.1f}%)".format(
                g,
                v["terms"],
                v["mul"],
                v["scale"],
                v["add"],
                v["poly"],
                vm,
                100.0 * vm / max(1, sum(x["mul"] + x["scale"] for x in by_group.values())),
            )
        )

    # Attribution check: the gate segment must reproduce the expression-level
    # product/scale counts. If it does not, the term ordering assumption is wrong
    # and every number in the table above is suspect - say so loudly.
    g_gate = by_group.get(GATE_GROUP)
    if g_gate:
        # The gate block is emitted once per circuit instance (a 2-action bundle
        # has 2), so the CS-level counts have to be scaled by that factor.
        seg = first["report"]["segments"]
        instances = seg["gates"] // max(1, cs["gate_polys"])
        exp_mul = cs["nodes"]["product"] * instances
        # `Negated` has no Ast variant: `Expression::evaluate` maps it through
        # `Neg for Ast`, which is `Scale(-1)`. So the gate segment should see
        # `scaled + negated` Scale nodes, not `scaled`.
        exp_scale = (cs["nodes"]["scaled"] + cs["nodes"]["negated"]) * instances
        ok = g_gate["mul"] == exp_mul and g_gate["scale"] == exp_scale
        add("")
        add(
            "  attribution check: gate segment mul/scale = {}/{} vs expected {}/{} "
            "(instances={}, product / scaled+negated)  -> {}".format(
                g_gate["mul"],
                g_gate["scale"],
                exp_mul,
                exp_scale,
                instances,
                "OK" if ok else "MISMATCH",
            )
        )
        if not ok:
            add("  !! term ordering assumption is wrong; treat the split as unverified")

    add("")
    add("[cross-check against the Stage E3 sampler]")
    for r in runs:
        ev = r["report"]["evals"]
        total_muls = ev["field_muls_total"]
        add(
            "  {:<14} field muls {:>11,}  at 10.14 ns inline throughput = {:.3f} s".format(
                r["path"], total_muls, total_muls * 10.14e-9
            )
        )
    add("")
    add("note: field_muls_total counts Vector<F> operations x poly_len for every")
    add("      evaluate call, including the 12 small Lagrange-basis calls.")

    text = "\n".join(lines)
    with open(os.path.join(out, "summary.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
