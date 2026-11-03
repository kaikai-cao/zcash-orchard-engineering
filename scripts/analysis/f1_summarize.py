#!/usr/bin/env python3
"""Stage F1 report generator.

Inputs (written by `experiments/f1_fp_mul/run_all.sh`):
  results/transaction_e2e/f1_fp_mul/raw_<config>.txt   raw bench logs (N rounds)

Outputs:
  measurements.csv   one row per (config, round)
  aggregate.csv      median / mean / min / max / CV per (config, metric)
  assembly.csv       instruction mix of one full multiply, from the linked exe
  summary.txt        human-readable roll-up

Assembly is taken from the final executable (objdump), not from `--emit asm`,
so it reflects what the linker actually produced -- including the case where
the multiply is only reachable through a call (pasta_curves 0.6.1 `asm`).
"""

import argparse
import os
import re
import statistics as st
import subprocess
from collections import OrderedDict

FIELDS = [
    "lat_ns",
    "thr_ns",
    "lat_call_ns",
    "thr_call_ns",
    "ilp_ratio",
    "add_ns",
    "call_overhead_ns",
]

CONFIG_ORDER = ["fp060_default", "fp060_bmi2adx", "fp061_asm"]
CONFIG_LABEL = {
    "fp060_default": "pasta_curves 0.6.0 (Orchard baseline)",
    "fp060_bmi2adx": "0.6.0 + target-feature=+bmi2,+adx",
    "fp061_asm": "0.6.1 + asm backend (reference ceiling)",
}

# Canonical instruction classes. Mnemonics are normalised first (GAS emits
# `mulxq`, objdump emits `mulx`; both mean the same thing).
OPS = ["mul", "mulx", "adcx", "adox", "adc", "add", "mov", "lea", "sub"]


def normalize(mnem):
    m = mnem.lower()
    for suf in ("q", "l", "w", "b"):
        if m.endswith(suf) and len(m) > 3:
            base = m[:-1]
            if base in ("mul", "mulx", "adcx", "adox", "adc", "add", "mov", "lea", "sub"):
                return base
    return m


def parse_summary(path):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("summary,"):
                continue
            d = {}
            for part in line.strip().split(",")[1:]:
                if "=" in part:
                    k, v = part.split("=", 1)
                    d[k] = float(v)
            rows.append(d)
    return rows


def objdump_text(exe):
    r = subprocess.run(["objdump", "-d", exe], capture_output=True, text=True, errors="replace")
    return r.stdout


def symbol_names(text):
    return re.findall(r"^([0-9a-f]+) <([^>]+)>:$", text, flags=re.M)


def extract_symbol(text, sym):
    """Return the instruction lines of `<sym>:` up to the next symbol."""
    out, on = [], False
    for line in text.splitlines():
        s = line.strip()
        if not on:
            if s.endswith(" <" + sym + ">:"):
                on = True
            continue
        if re.match(r"^[0-9a-f]+ <[^>]+>:$", s):
            break
        out.append(line)
    return out


def count_instrs(body):
    counts = {op: 0 for op in OPS}
    total = 0
    for line in body:
        parts = line.split("\t")
        mnem = None
        if len(parts) >= 3 and parts[2].strip():
            mnem = parts[2].strip().split()[0]
        else:
            m = re.match(r"\s*[0-9a-f]+:\s+(?:[0-9a-f]{2}\s+)+\s*([a-z][a-z0-9]*)", line)
            if m:
                mnem = m.group(1)
        if not mnem:
            continue
        if mnem.startswith(("nop", "data", "rex", ".byte")):
            continue
        total += 1
        op = normalize(mnem)
        if op in counts:
            counts[op] += 1
    counts["instr_total"] = total
    return counts


def asm_stats(exe):
    text = objdump_text(exe)
    body = extract_symbol(text, "f1_mul_blackbox")
    followed = None
    if body:
        c = count_instrs(body)
        # If the wrapper has no multiply of its own, follow the single call.
        if c["mul"] + c["mulx"] == 0:
            m = re.search(r"call\s+[0-9a-f]+ <([^>]+)>", "\n".join(body))
            if m:
                target = m.group(1)
                body2 = extract_symbol(text, target)
                if body2:
                    c = count_instrs(body2)
                    followed = target
    else:
        c = {op: 0 for op in OPS}
        c["instr_total"] = 0
    c["followed_call"] = followed or ""
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--exe", action="append", default=[])
    ap.add_argument("--skip-asm", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    per_config = OrderedDict()
    for cfg in CONFIG_ORDER:
        p = os.path.join(args.out, f"raw_{cfg}.txt")
        if os.path.exists(p):
            rows = parse_summary(p)
            if rows:
                per_config[cfg] = rows

    with open(os.path.join(args.out, "measurements.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("config,round," + ",".join(FIELDS) + "\n")
        for cfg, rows in per_config.items():
            for i, r in enumerate(rows, 1):
                f.write(f"{cfg},{i}," + ",".join(f"{r.get(k, float('nan')):.3f}" for k in FIELDS) + "\n")

    agg = {}
    with open(os.path.join(args.out, "aggregate.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("config,n,metric,median,mean,min,max,cv_pct\n")
        for cfg, rows in per_config.items():
            agg[cfg] = {}
            for k in FIELDS:
                v = [r[k] for r in rows if k in r]
                if not v:
                    continue
                med = st.median(v)
                mean = st.mean(v)
                cv = (st.stdev(v) / mean * 100.0) if len(v) > 1 and mean else 0.0
                agg[cfg][k] = med
                f.write(f"{cfg},{len(v)},{k},{med:.3f},{mean:.3f},{min(v):.3f},{max(v):.3f},{cv:.2f}\n")

    asm_rows = OrderedDict()
    if not args.skip_asm:
        for spec in args.exe:
            if "=" not in spec:
                continue
            cfg, exe = spec.split("=", 1)
            if not os.path.exists(exe):
                continue
            asm_rows[cfg] = asm_stats(exe)

    if asm_rows:
        with open(os.path.join(args.out, "assembly.csv"), "w", encoding="utf-8", newline="\n") as f:
            f.write("config," + ",".join(OPS) + ",instr_total,followed_call\n")
            for cfg, s in asm_rows.items():
                f.write(cfg + "," + ",".join(str(s[o]) for o in OPS) + f",{s['instr_total']},{s['followed_call']}\n")

    lines = []
    lines.append("Stage F1 -- Pallas Fp::mul microbenchmark")
    lines.append("=" * 78)
    lines.append("")
    if per_config:
        lines.append(f"{'config':<45}{'lat':>8}{'thr':>8}{'lat_call':>10}{'thr_call':>10}")
        lines.append(f"{'':<45}{'ns':>8}{'ns':>8}{'ns':>10}{'ns':>10}")
        lines.append("-" * 81)
        for cfg in per_config:
            a = agg.get(cfg, {})
            lines.append(
                f"{CONFIG_LABEL.get(cfg, cfg):<45}{a.get('lat_ns', float('nan')):8.3f}"
                f"{a.get('thr_ns', float('nan')):8.3f}{a.get('lat_call_ns', float('nan')):10.3f}"
                f"{a.get('thr_call_ns', float('nan')):10.3f}"
            )
        lines.append("")
        if "fp060_default" in agg:
            b = agg["fp060_default"]
            lines.append("Relative to 0.6.0 baseline (negative = faster):")
            for cfg in per_config:
                if cfg == "fp060_default":
                    continue
                a = agg[cfg]
                parts = []
                for k in ("lat_ns", "thr_ns", "lat_call_ns", "thr_call_ns"):
                    if k in a and k in b and b.get(k):
                        parts.append(f"{k} {(a[k] - b[k]) / b[k] * 100:+.1f}%")
                lines.append(f"  {cfg:<20} " + ", ".join(parts))
            lines.append("")
    if asm_rows:
        lines.append("Instructions for one full 4-limb multiply (linked binary):")
        hdr = f"{'config':<20}" + "".join(f"{o:>7}" for o in OPS) + f"{'total':>8}"
        lines.append(hdr)
        lines.append("-" * len(hdr))
        for cfg, s in asm_rows.items():
            lines.append(f"{cfg:<20}" + "".join(f"{s[o]:7d}" for o in OPS) + f"{s['instr_total']:8d}")
        for cfg, s in asm_rows.items():
            if s["followed_call"]:
                lines.append(f"  {cfg}: measured inside {s['followed_call']}")
        lines.append("")
    with open(os.path.join(args.out, "summary.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
