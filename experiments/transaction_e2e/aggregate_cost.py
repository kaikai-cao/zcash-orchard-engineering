#!/usr/bin/env python3
"""Aggregate layout_v3.txt region rows + selector activations per Action module.

The Orchard Action circuit is a single `synthesize_base` call; this script
reconstructs a module-level cost map from the measured layout recorder output.
"""
import re
from collections import defaultdict

LAYOUT = r"D:\Research\zcash-orchard-engineering\results\halo2\synthesize\layout_v3.txt"

# Module assignment rules: (substring, module_label). First match wins.
RULES = [
    ("generator_table", "GEN  Generator table (fixed)"),
    ("[ivk] g_d_old", "ADDR Address derivation (variable-base mul)"),
    ("pk_d_old equality", "BIND pk_d_old equality (copy)"),
    ("cm_old equality", "BIND cm_old equality (copy)"),
    ("nf_old = DeriveNullifier", "NF   Nullifier (nf_old)"),
    ("Merkle path", "MERKLE Merkle auth path"),
    ("CommitIvk", "CIVK CommitIvk (ivk)"),
    ("cv_net = ValueCommit", "CV   Value commit (cv_net)"),
    ("g★_d || pk★_d", "NC   NoteCommit (old+new, combined)"),
    ("post-NU 6.3 cross-address checks", "XADDR Cross-address checks (NU6.3)"),
    ("[alpha] SpendAuthG", "SA   Spend authorization (SpendAuthG)"),
    ("rk", "RK   RK (binding sig)"),
    ("Orchard circuit checks", "CHK  Orchard circuit checks"),
]

WITNESS_RULES = ("witness ", "/witness ", "cm_old/witness", "gd_old/witness")


def module_of(name):
    for sub, label in RULES:
        if sub in name:
            return label
    # witness loading
    if name.startswith("witness") or "/witness" in name or name.endswith("/load private"):
        return "WIT  Witness loading / private load"
    return "OTHER " + name.split("/")[0][:30]


def parse_selectors(s):
    """S18x170|S7x83 -> {S18:170, S7:83}"""
    out = {}
    if not s:
        return out
    for tok in s.split("|"):
        if "x" in tok:
            sel, cnt = tok.split("x")
            out[sel] = out.get(sel, 0) + int(cnt)
    return out


def main():
    rows_by_mod = defaultdict(int)
    sels_by_mod = defaultdict(lambda: defaultdict(int))
    regions_by_mod = defaultdict(int)
    active_regions = 0
    with open(LAYOUT, encoding="utf-8") as f:
        lines = f.read().splitlines()

    in_region = False
    for ln in lines:
        if ln.startswith("# region"):
            in_region = True
            continue
        if ln.startswith("#") or not ln.strip():
            # leaving region block if we were in it
            if ln.startswith("# advice"):
                in_region = False
            continue
        if not in_region:
            continue
        # region line: id,name,first,last,rows_used,extent,cols,sels
        # NAME may itself contain commas (e.g. MerkleCRH(14, left, right)),
        # so split the leading id off, then rsplit the tail into 7 fields.
        if "," not in ln:
            continue
        first_comma = ln.index(",")
        rid = ln[:first_comma]
        rest = ln[first_comma + 1:]
        fields = rest.rsplit(",", 6)  # [NAME, FIRST, LAST, ROWS, EXTENT, COLS, SELS]
        if len(fields) != 7:
            continue
        name, first, last, rused, extent, cols, sels = fields
        try:
            ru = int(rused)
        except ValueError:
            ru = 0
        mod = module_of(name)
        if ru > 0:
            rows_by_mod[mod] += ru
            regions_by_mod[mod] += 1
            active_regions += 1
        for s, c in parse_selectors(sels).items():
            sels_by_mod[mod][s] += c

    # Header totals
    print("=== ROWS PER MODULE (rows_used sum) ===")
    total = 0
    for mod in sorted(rows_by_mod, key=lambda m: -rows_by_mod[m]):
        print(f"{rows_by_mod[mod]:6d}  {regions_by_mod[mod]:4d} regions   {mod}")
        total += rows_by_mod[mod]
    print(f"{total:6d}  TOTAL rows (excludes 0-row copy/instance bindings)")

    print("\n=== KEY SELECTOR TOTALS PER MODULE ===")
    # show selectors with their global total for context
    global_sels = defaultdict(int)
    for mod in sels_by_mod:
        for s, c in sels_by_mod[mod].items():
            global_sels[s] += c
    for mod in sorted(sels_by_mod):
        ss = sels_by_mod[mod]
        top = sorted(ss.items(), key=lambda kv: -kv[1])
        print(f"\n{mod}  (Σsel={sum(ss.values())})")
        print("  " + ", ".join(f"{s}:{c}" for s, c in top))


if __name__ == "__main__":
    main()
