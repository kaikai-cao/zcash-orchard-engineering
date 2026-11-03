#!/usr/bin/env python3
"""Mechanism check for Stage H1c: did LTO actually inline `Fp::mul` away?

Counting call sites needs symbol addresses, and the MSVC release binaries carry
no COFF symbols (names live in an external PDB that this box has no tool to
read). So both builds are linked with `/MAP:<file>`, and this script stitches
the two together:

    map file  ->  address range of a function of interest
    objdump   ->  the instructions inside that range
    map file  ->  name of every `call` target

Reported for each binary:

  * `calls_in_body`      number of `call` instructions inside the named function
  * `calls_to_mul`       of those, how many land on a symbol matching the mul
                         pattern (i.e. "the multiplication was not inlined here")
  * `mul_call_sites_total`  call sites to mul symbols across the whole .text

Usage:
    python scripts/analysis/h1c_inlining_check.py \
        --bin target/h1c-a/release/orchard-e2e.exe --map .../a_baseline.map \
        --label A [--function evaluate7recurse] [--mul-pattern 'mul']

Exit code 0 always: this is a measurement tool, not a gate. Compare the two
JSON blobs yourself.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

MAP_LINE = re.compile(
    r"^\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{8})\s+(\S+)\s+([0-9a-fA-F]{16})\s+([a-zA-Z])\s+(\S+)\s*$"
)
OBJDUMP_CALL = re.compile(r"^\s*([0-9a-f]+):\s+(?:[0-9a-f]{2}\s+)*\s*callq?\s+(?:0x)?([0-9a-f]+)\b")
OBJDUMP_LABEL = re.compile(r"^\s*([0-9a-f]+)\s+<([^>]+)>:")


def parse_map(path: str) -> list[dict]:
    syms = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = MAP_LINE.match(line)
            if not m:
                continue
            sec, off, name, rva, flags, obj = m.groups()
            if flags not in ("f", "F"):
                continue  # keep functions only; data/absolute symbols are noise
            syms.append({"sec": sec, "off": int(off, 16), "name": name,
                         "addr": int(rva, 16), "obj": obj})
    syms.sort(key=lambda s: (s["sec"], s["addr"]))
    for i, s in enumerate(syms):
        nxt = [t["addr"] for t in syms[i + 1:] if t["sec"] == s["sec"]]
        s["end"] = nxt[0] if nxt else s["addr"]
    return syms


def find(syms: list[dict], needle: str) -> list[dict]:
    return [s for s in syms if needle in s["name"]]


def nearest(syms: list[dict], addr: int, sec_hint: str | None = None) -> str:
    best, best_d = None, None
    for s in syms:
        if sec_hint is not None and s["sec"] != sec_hint:
            continue
        if s["addr"] <= addr < max(s["end"], s["addr"] + 1):
            return s["name"]
        d = addr - s["addr"]
        if d >= 0 and (best_d is None or d < best_d):
            best, best_d = s["name"], d
    return best or "?"


def disassemble(objdump: str, exe: str, start: int, stop: int) -> list[tuple[int, int]]:
    """Return [(call_site_addr, target_addr)] for every call in [start, stop)."""
    out = subprocess.run(
        [objdump, "-d", "--no-show-raw-insn",
         f"--start-address=0x{start:x}", f"--stop-address=0x{stop:x}", exe],
        capture_output=True, text=True, errors="replace",
    ).stdout
    calls = []
    for line in out.splitlines():
        m = OBJDUMP_CALL.match(line)
        if m:
            calls.append((int(m.group(1), 16), int(m.group(2), 16)))
    return calls


def disassemble_all_calls(objdump: str, exe: str) -> list[tuple[int, int]]:
    out = subprocess.run([objdump, "-d", "--no-show-raw-insn", exe],
                         capture_output=True, text=True, errors="replace").stdout
    calls = []
    for line in out.splitlines():
        m = OBJDUMP_CALL.match(line)
        if m:
            calls.append((int(m.group(1), 16), int(m.group(2), 16)))
    return calls


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--objdump", default=r"D:\mingw64\bin\objdump.exe")
    ap.add_argument("--label", default="?")
    ap.add_argument("--function", default="evaluate_inner7recurse")
    ap.add_argument("--mul-pattern", default=r"mul")
    ap.add_argument("--no-whole-binary", action="store_true",
                    help="skip the whole-.text call census (slow on big binaries)")
    args = ap.parse_args()

    syms = parse_map(args.map)
    mul_re = re.compile(args.mul_pattern)
    mul_syms = [s for s in syms if mul_re.search(s["name"])]
    mul_addrs = {s["addr"] for s in mul_syms}

    def is_mul_target(addr: int) -> bool:
        # Exact entry-point match: a `call` lands on a function start, and ICF
        # folds every copy of the same monomorphisation onto one address.
        return addr in mul_addrs

    report: dict = {
        "label": args.label,
        "binary": args.bin,
        "map": args.map,
        "symbols_total": len(syms),
        "mul_symbols": len(mul_syms),
        "mul_entry_points": sorted(hex(a) for a in mul_addrs),
    }

    fns = find(syms, args.function)
    # One entry per distinct address: the map lists the same monomorphisation
    # once per contributing object file, but ICF already merged them.
    uniq: dict[int, dict] = {}
    for s in fns:
        if "?dtor$" in s["name"] or "?catch$" in s["name"] or s["name"].startswith("$"):
            continue  # unwind funclets / EH tables are not the function body
        uniq.setdefault(s["addr"], s)
    report["function_hits"] = [
        {"name": s["name"], "addr": hex(s["addr"]), "size": s["end"] - s["addr"]}
        for s in sorted(uniq.values(), key=lambda x: x["addr"])
    ]

    bodies = []
    for fn in sorted(uniq.values(), key=lambda x: x["addr"]):
        calls = disassemble(args.objdump, args.bin, fn["addr"], fn["end"])
        to_mul = [c for c in calls if is_mul_target(c[1])]
        targets: dict[str, int] = {}
        for _, t in calls:
            n = nearest(syms, t)
            targets[n] = targets.get(n, 0) + 1
        bodies.append({
            "function": fn["name"][:120],
            "addr": hex(fn["addr"]),
            "size_bytes": fn["end"] - fn["addr"],
            "calls_in_body": len(calls),
            "calls_to_mul": len(to_mul),
            "calls_to_mul_sites": [hex(a) for a, _ in to_mul[:20]],
            "call_targets_top": dict(sorted(targets.items(), key=lambda kv: -kv[1])[:10]),
        })
    report["bodies"] = bodies
    report["function"] = bodies[0]["function"] if bodies else None
    report["calls_in_body"] = sum(b["calls_in_body"] for b in bodies) if bodies else None
    report["calls_to_mul"] = sum(b["calls_to_mul"] for b in bodies) if bodies else None

    if not args.no_whole_binary:
        allcalls = disassemble_all_calls(args.objdump, args.bin)
        report["calls_total_in_binary"] = len(allcalls)
        report["mul_call_sites_total"] = sum(1 for _, t in allcalls if is_mul_target(t))

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
