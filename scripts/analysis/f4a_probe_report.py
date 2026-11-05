#!/usr/bin/env python3
"""Stage F4-A feasibility probe: turn one probe log into the numbers the
preregistration asked for.

Reads `F4A_CHAIN` / `F4A_OCC` / `F4A_CS` / `F4A_BENCH` / `F4A_META` lines and
writes `summary.md`, `chains.csv`, `consumers.csv`.

Deliberately does **not** invent prices: the cost model takes the vector-multiply
price from wherever it is handed in (H6's frozen anchor by default) and the
reuse cost from this run's own kernels, and prints both sources side by side.
"""
import argparse
import collections
import csv
import json
import os

# H6's frozen anchor for one whole-block multiply (doc 16, D2): the quotient
# stage went 1767.0 -> 1276.2 ms when 2638 of them disappeared.
H6_MS_PER_MUL = (1767.0 - 1276.2) / 2638.0  # 0.1861 ms
PROVE_1P_MS = 2725.8  # H6 + H7, 1P, doc 20
F4_PRODUCTS_FROM_COMPRESSION = 869  # doc 21, section 2.2
F4_FIXED_FROM_COMPRESSION = 1062  # doc 21, section 2.2
ELEMS = 16384  # extended domain, j = 9, n = 2048


def load(path):
    chains, occ, cs, bench, meta = {}, collections.defaultdict(list), None, {}, None
    for line in open(path, encoding="utf-8"):
        if line.startswith("F4A_CHAIN "):
            d = json.loads(line[len("F4A_CHAIN "):])
            chains[d["chain"]] = d
        elif line.startswith("F4A_OCC "):
            d = json.loads(line[len("F4A_OCC "):])
            occ[d["chain"]].append(d)
        elif line.startswith("F4A_CS "):
            cs = json.loads(line[len("F4A_CS "):])
        elif line.startswith("F4A_BENCH "):
            d = json.loads(line[len("F4A_BENCH "):])
            bench[(d["kernel"], d["n"])] = d
        elif line.startswith("F4A_META "):
            meta = json.loads(line[len("F4A_META "):])
    return chains, occ, cs, bench, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--md", default=None)
    args = ap.parse_args()

    chains, occ, cs, bench, meta = load(args.log)
    os.makedirs(args.out_dir, exist_ok=True)

    rows = []
    for cid, d in chains.items():
        n = len(occ[cid])
        rows.append(
            {
                "chain": cid,
                "column": d["column"],
                "k": d["k"],
                "occ": n,
                "saved_muls": (n - 1) * (d["k"] - 1),
                "occ_muls": n * (d["k"] - 1),
                "root_factorable": d["root_factorable"],
                "gates": ";".join(
                    sorted({o["gate_name"] for o in occ[cid]})
                ),
            }
        )
    rows.sort(key=lambda r: -r["saved_muls"])

    total_occ_muls = sum(r["occ_muls"] for r in rows)
    total_saved = sum(r["saved_muls"] for r in rows)
    total_consumers = sum(r["occ"] for r in rows)
    shared = [r for r in rows if r["occ"] >= 2]
    singles = [r for r in rows if r["occ"] == 1]
    khist = collections.Counter(r["k"] for r in rows)
    sum_k_minus_1 = sum(r["k"] - 1 for r in rows)
    non_rf = [
        (r["chain"], r["column"], o["gate_name"], o["poly"])
        for r in rows
        for o in occ[r["chain"]]
        if not o["root_factorable"]
    ]

    # ---- cost model -------------------------------------------------------
    ip = bench.get(("in_place", ELEMS))
    fu = bench.get(("fused", ELEMS))
    ip4 = bench.get(("in_place", 4096))
    fu4 = bench.get(("fused", 4096))
    delta4 = (fu4["ns_per_elem"] - ip4["ns_per_elem"]) if (ip4 and fu4) else None
    delta = (fu["ns_per_elem"] - ip["ns_per_elem"]) if (ip and fu) else None

    saved_ms = total_saved * H6_MS_PER_MUL
    # Each consumer of a shared chain must write its own output, so the reuse
    # cost is one out-of-place multiply per consumer. The evaluator works in
    # 4096-element chunks, so the chunk geometry is the one that applies; the
    # 16384 number is reported as the sensitivity bound.
    shared_consumers = sum(r["occ"] for r in shared)
    reuse_ms = (
        shared_consumers * ELEMS * delta4 / 1e6 if delta4 is not None else float("nan")
    )
    reuse_ms_hi = (
        shared_consumers * ELEMS * delta / 1e6 if delta is not None else float("nan")
    )
    net_ms = saved_ms - reuse_ms
    net_pct = 100.0 * net_ms / PROVE_1P_MS
    gross_pct = 100.0 * saved_ms / PROVE_1P_MS
    # Same arithmetic priced by this run's own kernel instead of H6's anchor.
    own_ms = total_saved * ip["median_ns"] / 1e6 if ip else float("nan")

    # ---- csv --------------------------------------------------------------
    with open(os.path.join(args.out_dir, "chains.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(args.out_dir, "consumers.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["chain", "column", "k", "gate", "gate_name", "poly", "root_factorable"])
        for r in rows:
            for o in occ[r["chain"]]:
                w.writerow(
                    [r["chain"], r["column"], r["k"], o["gate"], o["gate_name"], o["poly"],
                     int(o["root_factorable"])]
                )

    # ---- closure checks ---------------------------------------------------
    checks = [
        ("occ_muls == F4 的 869（压缩替身总数）", total_occ_muls == F4_PRODUCTS_FROM_COMPRESSION),
        ("saved + Σ(k-1) == occ_muls", total_saved + sum_k_minus_1 == total_occ_muls),
        ("Σ(k-1) == 242（F4 候选 A 的差额）", sum_k_minus_1 == 242),
        ("consumers == 181", total_consumers == 181),
        ("identity_fail == 0", cs.get("identity_fail") == 0),
        ("domains == 1", cs.get("domains") == 1),
        ("proof 7264 B", meta["proof_bytes"] == 7264),
        ("proof 指纹 0xa6bcd171d69e969a", meta["proof_fnv"] == "0xa6bcd171d69e969a"),
    ]

    L = []
    L.append("# F4-A 探针汇总（自动生成）\n")
    L.append(f"日志：`{os.path.basename(args.log)}`　probe={meta['probe']}　"
             f"证明 {meta['proof_bytes']} B / `{meta['proof_fnv']}`\n")
    L.append("## 1. 结构总量\n")
    L.append("| 项 | 值 |\n|---|---:|")
    L.append(f"| 链 | {len(rows)} |")
    L.append(f"| 其中 occ ≥ 2（可共享） | {len(shared)} |")
    L.append(f"| 其中 occ = 1（无收益） | {len(singles)} |")
    L.append(f"| 消费者总数 | {total_consumers} |")
    L.append(f"| Σ(k−1) | {sum_k_minus_1} |")
    L.append(f"| Σ occ·(k−1) | {total_occ_muls} |")
    L.append(f"| 可省整块乘 | **{total_saved}** |")
    L.append(f"| 根位置可直接提取的消费者 | {sum(r['root_factorable'] for r in rows)} / {total_consumers} "
             f"（{100.0 * sum(r['root_factorable'] for r in rows) / total_consumers:.1f}%） |")
    L.append("\nk 分布：" + "，".join(f"k={k} × {v} 条" for k, v in sorted(khist.items())))
    L.append("\n## 2. 前 12 条链（按可省乘法排序）\n")
    L.append("| # | 列 | k | occ | 可省 | 根位置 | 来源门 |\n|---:|---:|---:|---:|---:|---:|---|")
    for i, r in enumerate(rows[:12], 1):
        L.append(f"| {i} | {r['column']} | {r['k']} | {r['occ']} | {r['saved_muls']} | "
                 f"{r['root_factorable']} | {r['gates']} |")
    L.append("\n## 3. 复用代价（本轮实测）\n")
    if delta is not None:
        L.append("| 内核 | n | ns/元素 | 中位 ns |\n|---|---:|---:|---:|")
        for name in ("in_place", "fused", "reused"):
            for n in (4096, ELEMS):
                b = bench.get((name, n))
                if b:
                    L.append(f"| {name} | {n} | {b['ns_per_elem']:.4f} | {b['median_ns']:.0f} |")
        L.append(f"\nΔ(fused − in_place)：n=4096 时 **{delta4:+.4f}** ns/元素，"
                 f"n=16384 时 **{delta:+.4f}** ns/元素。")
    L.append("\n## 4. 成本模型\n")
    L.append(f"- 毛节省 = {total_saved} × {H6_MS_PER_MUL:.4f} ms = **{saved_ms:.1f} ms = {gross_pct:.2f}%**"
             f"（H6 冻结锚点，外推）")
    L.append(f"- 同口径用本轮内核价（{ip["median_ns"]:.0f} ns/整块乘）"
             f"= {own_ms:.1f} ms = {100.0 * own_ms / PROVE_1P_MS:.2f}%（上界，二者相差 "
             f"{100.0 * (own_ms / saved_ms - 1):+.1f}%）")
    L.append(f"- 复用代价 = {shared_consumers} 个共享链消费者 × {ELEMS} 元素 × {delta4:+.4f} ns/chunk 几何 "
             f"= **{reuse_ms:.2f} ms**（若改用 16384 几何的 Δ={delta:+.4f}，则为 {reuse_ms_hi:.2f} ms）")
    L.append(f"- 净收益 = **{net_ms:.1f} ms = {net_pct:.2f}%** of {PROVE_1P_MS} ms")
    L.append("\n## 5. 闭合检查\n")
    for name, ok in checks:
        L.append(f"- {'PASS' if ok else 'FAIL'}　{name}")
    L.append(f"\n派生闭合：Σ occ·k = {total_occ_muls} + {total_consumers} = "
             f"{total_occ_muls + total_consumers}；F4 的 fixed 增量是 {F4_FIXED_FROM_COMPRESSION}，"
             f"差 {F4_FIXED_FROM_COMPRESSION - total_occ_muls - total_consumers} "
             f"= 2 个 k=1 单例 selector 的出现次数（不可由结构直接计数）。")
    if non_rf:
        L.append("\n非根位置的消费者：" + "，".join(
            f"列{c} / {g} / poly {p}" for _, c, g, p in non_rf))

    md = "\n".join(L) + "\n"
    out = args.md or os.path.join(args.out_dir, "summary.md")
    open(out, "w", encoding="utf-8").write(md)
    print(md)


if __name__ == "__main__":
    main()
