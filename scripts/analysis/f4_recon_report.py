#!/usr/bin/env python3
"""Stage F4: constraint-system reconnaissance from the keygen census.

Reads one `--mode recon` log (see experiments/transaction_e2e/harness) and
answers the five questions in docs/orchard/21_constraint_system_recon.md:

  1. gate structure           -- per-gate constraints, expression size, ops, degree
  2. selector use/compression  -- activation rows, pairwise relations, virtual ->
                                 physical, and what the substitution costs
  3. merge candidates          -- structural duplicates, and whether merging is
                                 where the cost actually is
  4. re-baseline vs H6/H7      -- what one more/less column or product is worth
  5. priority                  -- printed buckets

The census records the constraint system twice: before `compress_selectors`
(authored form, virtual selectors) and after (what the prover walks). F2 walks
the *post* form. Neither number is comparable to the other by eye, so this
script rebuilds the substitution explicitly:

    per selector occurrence, `q` -> `q * (r1 - q) * ... * (rk-1 - q)`

which adds exactly `k-1` products, `k-1` sums, `k-1` negations, `k-1` constants
and `k` fixed leaves per occurrence, and removes one selector leaf. Predicting
all five components and matching F2 digit for digit is what turns "the numbers
differ" into "the difference is compression, and here is its price".

Cost conversions use H6's own measurement (2638 block multiplications removed
took `quotient` from 1871.4 to 1347.4 ms at 1P, i.e. 0.1986 ms per block
multiply); they are labelled as extrapolations wherever they appear.

Usage:
    python scripts/analysis/f4_recon_report.py \
        results/transaction_e2e/f4_recon/recon_on.log \
        --out-dir results/transaction_e2e/f4_recon --md results/transaction_e2e/f4_recon/summary.md
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict

# H6 / D2, 1P: `quotient` 1767.0 -> 1276.2 ms (doc 16) for the 2638 block
# multiplies H6 removed. This is the only measured price of a block multiply in
# this repo; it is a `Scale` price, so `Product` may differ somewhat.
MS_PER_BLOCK_MUL = (1767.0 - 1276.2) / 2638.0
# H6 + H7, 1P: the frozen prove time everything is compared against.
PROVE_1P_MS = 2725.8

# Static attribution of every gate name to the registration site. The census
# gives names and order; this table says which `configure` call produced them,
# so the report can point at a file. Only hand-maintained part of this script.
MODULE = {
    "Orchard circuit checks": "orchard/src/circuit.rs:335",
    "Field element addition: c = a + b": "orchard/src/circuit/gadget.rs",
    "Short lookup bitshift": "halo2_gadgets/utilities/lookup",
    "witness point": "halo2_gadgets/ecc/chip",
    "witness non-identity point": "halo2_gadgets/ecc/chip",
    "incomplete addition": "halo2_gadgets/ecc/chip/add_incomplete",
    "complete addition": "halo2_gadgets/ecc/chip/add",
    "q_mul_1 == 1 checks": "halo2_gadgets/ecc/chip/mul",
    "q_mul_2 == 1 checks": "halo2_gadgets/ecc/chip/mul",
    "q_mul_3 == 1 checks": "halo2_gadgets/ecc/chip/mul",
    "Decompose scalar for complete bits of variable-base mul": "halo2_gadgets/ecc/chip/mul",
    "overflow checks": "halo2_gadgets/ecc/chip/mul",
    "LSB check": "halo2_gadgets/ecc/chip/mul",
    "range check": "halo2_gadgets/ecc/chip/mul_fixed",
    "Running sum coordinates check": "halo2_gadgets/ecc/chip/mul_fixed",
    "Full-width fixed-base scalar mul": "halo2_gadgets/ecc/chip/mul_fixed",
    "Short fixed-base mul gate": "halo2_gadgets/ecc/chip/mul_fixed/short",
    "Canonicity checks": "halo2_gadgets/sinsemilla",
    "full round": "halo2_gadgets/poseidon",
    "partial rounds": "halo2_gadgets/poseidon",
    "pad-and-add": "halo2_gadgets/sinsemilla",
    "Initial y_Q": "halo2_gadgets/sinsemilla",
    "Sinsemilla gate": "halo2_gadgets/sinsemilla",
    "a' = b ⋅ swap + a ⋅ (1-swap)": "halo2_gadgets/sinsemilla",
    "Decomposition check": "halo2_gadgets/sinsemilla",
    "CommitIvk canonicity check": "orchard/src/circuit/commit_ivk.rs",
    "NoteCommit MessagePiece b": "orchard/src/circuit/note_commit.rs",
    "NoteCommit MessagePiece d": "orchard/src/circuit/note_commit.rs",
    "NoteCommit MessagePiece e": "orchard/src/circuit/note_commit.rs",
    "NoteCommit MessagePiece g": "orchard/src/circuit/note_commit.rs",
    "NoteCommit MessagePiece h": "orchard/src/circuit/note_commit.rs",
    "NoteCommit input g_d": "orchard/src/circuit/note_commit.rs",
    "NoteCommit input pk_d": "orchard/src/circuit/note_commit.rs",
    "NoteCommit input value": "orchard/src/circuit/note_commit.rs",
    "NoteCommit input rho": "orchard/src/circuit/note_commit.rs",
    "NoteCommit input psi": "orchard/src/circuit/note_commit.rs",
    "y coordinate checks": "orchard/src/circuit/note_commit.rs",
}


def parse(path: str) -> dict:
    out: dict = {"cs": None, "compress": None, "eval": None, "f2": None, "meta": None}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("F4_CS "):
                out["cs"] = json.loads(line[6:])
            elif line.startswith("F4_COMPRESS "):
                out["compress"] = json.loads(line[12:])
            elif line.startswith("F4_EVAL "):
                out["eval"] = json.loads(line[8:])
            elif line.startswith("F2_REPORT "):
                out["f2"] = json.loads(line[10:])
            elif line.startswith("F4_META "):
                out["meta"] = json.loads(line[8:])
    missing = [k for k, v in out.items() if v is None]
    if missing:
        raise SystemExit(f"missing records in {path}: {missing}")
    return out


def rows_of(sel: dict, n: int) -> set[int]:
    raw = bytes.fromhex(sel["rows_hex"])
    return {i for i in range(n) if raw[i // 8] >> (i % 8) & 1}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--md", default=None)
    args = ap.parse_args()

    d = parse(args.log)
    cs, cmp_, ev, f2, meta = d["cs"], d["compress"], d["eval"], d["f2"], d["meta"]
    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.log))
    n = 1 << cs["k"]
    gates = cs["gates"]
    lines: list[str] = []

    def emit(s: str = "") -> None:
        lines.append(s)
        print(s)

    emit("Stage F4 -- constraint-system reconnaissance")
    emit("=" * 78)
    emit(f"source: {os.path.basename(args.log)}   proof {meta['proof_bytes']} B   "
         f"fnv {meta['proof_fnv']}   probe {meta['probe']}")
    emit()

    # ------------------------------------------------------------------ 0
    pre = defaultdict(int)
    for g in gates:
        for p in g["polys"]:
            for k, v in p["stats"].items():
                if isinstance(v, int):
                    pre[k] += v

    sel_rows = [rows_of(s, n) for s in cs["selector_rows"]]
    col_of = cmp_["selector_map"]
    group = defaultdict(list)
    for s, c in enumerate(col_of):
        group[c].append(s)
    k_of = {s: len(group[c]) for s, c in enumerate(col_of)}

    # selector leaves per constraint, and the degree of the constraint it sits in
    occ = defaultdict(int)          # selector -> number of leaves (occurrences)
    poly_use = []                   # (gate index, poly index, {sel: leaves})
    max_deg = [0] * cs["selectors"]
    for gi, g in enumerate(gates):
        for pi, p in enumerate(g["polys"]):
            for s in p["stats"]["selectors"]:
                max_deg[s] = max(max_deg[s], p["stats"]["degree"])
            use = {s: p["stats"]["selector"] for s in p["stats"]["selectors"]}
            for s, c in use.items():
                occ[s] += c
            poly_use.append((gi, pi, use))

    # Substitution model: predicted post-compression tree, component by component.
    model = {
        "constant": sum(c * (k_of[s] - 1) for s, c in occ.items()),
        "product": sum(c * (k_of[s] - 1) for s, c in occ.items()),
        "sum": sum(c * (k_of[s] - 1) for s, c in occ.items()),
        "negated": sum(c * (k_of[s] - 1) for s, c in occ.items()),
        "fixed": sum(c * k_of[s] for s, c in occ.items()),
        "selector": -sum(occ.values()),
        # per substitution: +product +sum +negated +constant +k fixed -1 selector
        "nodes": 5 * sum(c * (k_of[s] - 1) for s, c in occ.items()),
    }
    f2n = f2["cs"]["nodes"]
    emit("[0] the authored tree vs the tree the prover walks")
    emit(f"  gates {len(gates)}   constraints {sum(g['constraints'] for g in gates)}   "
         f"selectors {cs['selectors']}   advice {cs['advice_columns']}   "
         f"instance {cs['instance_columns']}   perm cols {cs['permutation_columns']}   "
         f"lookups {cs['lookups']}   constants {cs['constants']}   "
         f"degree {cs['minimum_degree']}..{cs['maximum_degree']}   n={n}")
    emit(f"  fixed columns {cs['fixed_columns']} at configure -> "
         f"{cmp_['fixed_columns']} after selector compression "
         f"(+{cmp_['fixed_columns'] - cs['fixed_columns']} columns)")
    emit()
    emit("  component   authored (F4)   compression   predicted   F2 (post)   match")
    model_ok = True
    for comp in ("constant", "product", "sum", "negated", "fixed", "selector", "nodes"):
        a = pre[comp] if comp != "nodes" else sum(
            pre[x] for x in ("constant", "fixed", "advice", "instance", "negated",
                             "sum", "product", "scaled", "selector"))
        added = model.get(comp, 0)
        pred = a + added
        obs = f2n["total"] if comp == "nodes" else f2n[comp]
        model_ok &= pred == obs
        emit(f"  {comp:10s} {a:13d} {added:+12d} {pred:11d} {obs:11d}   "
             f"{'SAME' if pred == obs else 'DIFF'}")
    emit(f"  model closes: {'PASS -- compression is the entire difference' if model_ok else 'FAIL'}")
    emit()

    # ------------------------------------------------------------------ 1
    emit("[1] gate structure (authored form, before selector substitution)")
    emit(f"  {'#':>2} {'gate':42s} {'con':>3} {'nod':>5} {'dep':>3} {'deg':>3} "
         f"{'sum':>4} {'mul':>4} {'neg':>4} {'scl':>4} {'cst':>4} {'fix':>4} {'adv':>4}  module")
    gate_rows = []
    for i, g in enumerate(gates):
        s = defaultdict(int)
        depth = deg = 0
        for p in g["polys"]:
            for k, v in p["stats"].items():
                if isinstance(v, int):
                    s[k] += v
            deg = max(deg, p["stats"]["degree"])
            depth = max(depth, p["stats"]["depth"])
        mod = MODULE.get(g["name"], "?")
        emit(f"  {i:2d} {g['name'][:42]:42s} {g['constraints']:3d} {s['nodes']:5d} "
             f"{depth:3d} {deg:3d} {s['sum']:4d} {s['product']:4d} {s['negated']:4d} "
             f"{s['scaled']:4d} {s['constant']:4d} {s['fixed']:4d} {s['advice']:4d}  {mod}")
        gate_rows.append({
            "gate_index": i, "gate": g["name"], "constraints": g["constraints"],
            "nodes": s["nodes"], "depth": depth, "degree": deg, "sum": s["sum"],
            "product": s["product"], "negated": s["negated"], "scaled": s["scaled"],
            "constant": s["constant"], "fixed": s["fixed"], "advice": s["advice"],
            "selectors": " ".join(str(x) for x in g["queried_selectors"]),
            "module": mod,
        })
    emit()

    # ------------------------------------------------------------------ 2
    emit("[2] selectors: activation rows, relations, compression")
    simple_flag = {s["selector"]: s["simple"] for s in cs["selector_rows"]}
    emit(f"  {'sel':>3} {'smp':>3} {'rows':>5} {'first':>5} {'last':>5} {'col':>4} "
         f"{'k':>2} {'deg':>3} {'occ':>4} {'+muls':>6}")
    sel_out = []
    for i in range(cs["selectors"]):
        r = sel_rows[i]
        added = occ[i] * (k_of[i] - 1)
        emit(f"  {i:3d} {'y' if simple_flag[i] else 'N':>3} {len(r):5d} "
             f"{min(r) if r else -1:5d} {max(r) if r else -1:5d} "
             f"{col_of[i]:4d} {k_of[i]:2d} {max_deg[i]:3d} {occ[i]:4d} {added:6d}")
        sel_out.append({
            "selector": i, "enabled_rows": len(r),
            "first_row": min(r) if r else -1, "last_row": max(r) if r else -1,
            "fixed_column": col_of[i], "group_size": k_of[i],
            "max_degree": max_deg[i], "simple": simple_flag[i], "occurrences": occ[i],
            "added_products": added,
        })
    max_row = max((max(r) for r in sel_rows if r), default=0)
    emit(f"  highest enabled row {max_row} of {n} (usable ~{n - 4}); "
         f"total selector leaves {sum(occ.values())} across "
         f"{sum(g['constraints'] for g in gates)} constraints")
    emit()

    rel = defaultdict(int)
    shared = []
    for i in range(len(sel_rows)):
        for j in range(i):
            a, b = sel_rows[i], sel_rows[j]
            if not a or not b:
                rel["one side empty"] += 1
            elif a == b:
                rel["identical"] += 1
                shared.append((i, j, len(a)))
            else:
                inter = len(a & b)
                if inter == 0:
                    rel["disjoint"] += 1
                else:
                    rel["partial overlap"] += 1
                    shared.append((i, j, inter))
    total_pairs = len(sel_rows) * (len(sel_rows) - 1) // 2
    emit(f"  pairwise row-set relations over {total_pairs} pairs:")
    for key in ("disjoint", "partial overlap", "identical", "one side empty"):
        if rel[key]:
            emit(f"    {key:16s} {rel[key]:5d}  ({100 * rel[key] / total_pairs:.1f}%)")
    if shared:
        emit("    pairs that share at least one row (compression forbids packing these):")
        for i, j, c in shared:
            emit(f"      sel {i:2d} & {j:2d}: {c} rows shared")
    emit()

    new_cols = cmp_["fixed_columns"] - cs["fixed_columns"]
    emit(f"  {cs['selectors']} virtual -> {new_cols} new fixed columns")
    emit(f"  {'col':>4} {'k':>2} {'selectors':46s} {'maxdeg':>6} {'rule':>5} {'disj':>5}")
    packed_ok = True
    for c in sorted(group):
        sels = group[c]
        deg = max(max_deg[s] for s in sels)
        feasible = (deg - 1) + len(sels) <= cs["maximum_degree"]
        disjoint = all(not (sel_rows[a] & sel_rows[b])
                       for i, a in enumerate(sels) for b in sels[:i])
        packed_ok &= disjoint and feasible
        emit(f"  {c:4d} {len(sels):2d} {str(sels)[:46]:46s} {deg:6d} "
             f"{'ok' if feasible else 'OVER':>5} {'yes' if disjoint else 'NO':>5}")
    emit(f"  all groups disjoint and within the degree rule: "
         f"{'YES' if packed_ok else 'NO'}")
    saturated = all(max(max_deg[s] for s in group[c]) - 1 + len(group[c])
                    == cs["maximum_degree"] for c in group if len(group[c]) > 1)
    emit(f"  every multi-selector group is degree-saturated (d + k = "
         f"{cs['maximum_degree']}): {'YES' if saturated else 'NO'} "
         f"-> no further packing is possible at this max degree")
    complex_ = [s for s in range(cs["selectors"]) if not simple_flag[s]]
    single_deg = [s for s in range(cs["selectors"])
                  if simple_flag[s] and k_of[s] == 1]
    emit(f"  complex selectors (used in a lookup -> boolean mask, own column by "
         f"rule): {len(complex_)} -> {complex_}")
    emit(f"  simple but alone because the degree budget is spent (deg "
         f"{cs['maximum_degree']}): {single_deg}")
    emit()

    # repacking experiment: same rule, best-fit decreasing
    simple = [s for s in range(cs["selectors"]) if simple_flag[s]]
    order = sorted(simple, key=lambda s: (-max_deg[s], -len(sel_rows[s])))
    bins: list[dict] = []
    for s in order:
        for b in bins:
            nd = max(b["d"], max_deg[s] - 1)
            if nd + len(b["m"]) + 1 <= cs["maximum_degree"] and not (b["used"] & sel_rows[s]):
                b["m"].append(s)
                b["used"] |= sel_rows[s]
                b["d"] = nd
                break
        else:
            bins.append({"d": max_deg[s] - 1, "m": [s], "used": set(sel_rows[s])})
    emit(f"  {len(simple)} simple selectors repacked best-fit-decreasing: {len(bins)} "
         f"columns vs {new_cols - len(complex_)} used by keygen "
         f"(the greedy in `compress_selectors` walks indices in order, not by degree)")
    emit()

    # ------------------------------------------------------------------ 3
    emit("[3] where the cost actually is: the substitution, not the gate count")
    by_gate = defaultdict(int)
    for gi, pi, use in poly_use:
        by_gate[gi] += sum(c * (k_of[s] - 1) for s, c in use.items())
    total_added = sum(by_gate.values())
    emit(f"  compression adds {total_added} products to the quotient tree "
         f"(authored {pre['product']}, prover sees {f2n['product']})")
    emit(f"  that is {100 * total_added / f2n['product']:.1f}% of all products and "
         f"{100 * total_added / pre['product']:.0f}% of the authored multiply count")
    emit(f"  at H6's measured price ({MS_PER_BLOCK_MUL:.4f} ms per block multiply at 1P): "
         f"{total_added * MS_PER_BLOCK_MUL:.1f} ms "
         f"= {100 * total_added * MS_PER_BLOCK_MUL / PROVE_1P_MS:.2f}% of the proof")
    emit()
    emit(f"  {'#':>2} {'gate':42s} {'con':>3} {'sel':>4} {'k':>2} {'+muls':>6} {'ms':>6}")
    for gi, c in sorted(by_gate.items(), key=lambda kv: -kv[1]):
        g = gates[gi]
        sels = set()
        for pi, p in enumerate(g["polys"]):
            sels |= set(p["stats"]["selectors"])
        if not sels:
            continue
        emit(f"  {gi:2d} {g['name'][:42]:42s} {g['constraints']:3d} "
             f"{','.join(str(s) for s in sorted(sels)):>4} "
             f"{max(k_of[s] for s in sels):2d} {c:6d} {c * MS_PER_BLOCK_MUL:6.1f}")
    emit()

    # ------------------------------------------------------------------ 4
    emit("[4] what one column is worth (measured), against H6+H7")
    median_ns = ev["median_ns"]
    per_col_ms = median_ns / 1e6
    emit(f"  eval_polynomial at n={ev['n']}: {per_col_ms * 1e3:.1f} us "
         f"({ev['ns_per_coeff']:.2f} ns/coeff, median of {ev['reps']}, "
         f"min {ev['min_ns'] / 1e3:.1f} us, max {ev['max_ns'] / 1e3:.1f} us)")
    emit(f"  one more fixed column = 1 eval + 1 MSM point = {per_col_ms:.4f} ms "
         f"= {100 * per_col_ms / PROVE_1P_MS:.5f}% of a 1P proof")
    emit(f"  all {cmp_['fixed_columns']} fixed queries: "
         f"{per_col_ms * cmp_['fixed_columns']:.3f} ms "
         f"({100 * per_col_ms * cmp_['fixed_columns'] / PROVE_1P_MS:.3f}%)")
    emit(f"  the {new_cols} selector columns cost "
         f"{per_col_ms * new_cols:.3f} ms "
         f"({100 * per_col_ms * new_cols / PROVE_1P_MS:.5f}%) of prove")
    emit(f"  and save {total_added} products = "
         f"{total_added * MS_PER_BLOCK_MUL:.1f} ms "
         f"({100 * total_added * MS_PER_BLOCK_MUL / PROVE_1P_MS:.2f}%) of quotient")
    emit()
    emit("  the frontier if the packing level were a dial (uniform k):")
    emit(f"    {'k':>2} {'columns':>8} {'+products':>10} {'quotient ms':>12} {'opening ms':>11}")
    for k in (1, 2, 3, 4, 5, 6, 7, 8):
        cols = -(-len(simple) // k)
        add = sum(occ[s] * (k - 1) for s in simple)
        emit(f"    {k:2d} {cols:8d} {add:10d} {add * MS_PER_BLOCK_MUL:12.1f} "
             f"{cols * per_col_ms:11.3f}")
    emit()

    fused = f2.get("evals") or {}
    emit("[5] the prover's own totals (F2, same run, same tree)")
    items = fused.get("items", [])
    if items:
        # The quotient evaluation is the call with the largest tree; the others
        # are the per-argument polynomial evaluations (permutation, lookup).
        big = max(items, key=lambda it: it["ast"]["vector_muls"])
        ast = big["ast"]
        vm = ast["vector_muls"]
        emit(f"  quotient call: poly_len {big['poly_len']} chunk {big['chunk_size']} x "
             f"{big['num_chunks']}   tree vector_muls {vm}  nodes {ast['nodes']}  "
             f"max_depth {ast['max_depth']}")
        emit(f"  of those vector muls: gate products {f2n['product']} "
             f"({100 * f2n['product'] / vm:.1f}%), of which selector recovery "
             f"{total_added} ({100 * total_added / vm:.1f}%)")
        emit(f"  all {fused['calls']} evaluations this run: vector_muls "
             f"{fused['vector_muls_total']}, field muls {fused['field_muls_total']}, "
             f"leaf elements copied {fused['leaf_elements_copied']}")
        emit(f"  segments (terms per source): {f2['segments']}")
    emit()

    if args.out_dir:
        for name, rows in (("gates.csv", gate_rows), ("selectors.csv", sel_out)):
            with open(os.path.join(args.out_dir, name), "w", newline="",
                      encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"\nwrote {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
