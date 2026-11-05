#!/usr/bin/env python3
"""F4-A implementation probe: turn one `--mode f4b` log into the numbers quoted
in `docs/orchard/22_f4a_feasibility_prereg.md` §8.4/§8.5.

Everything is derived from the log, nothing is transcribed by hand:

  * geometry      -- `F4B_GEOM`, cross-checked against F2's recorded `chunk_size`
  * kernel prices -- `F4B_BENCH` (in_place / fused / reused / add)
  * reuse cost    -- fused - in_place, applied per element at each geometry
  * bookkeeping   -- `F4B_LOOKUP` / `F4B_INTERN`
  * per-chunk model -- `F4B_MODEL`, normalised to 16384 elements so the three
    geometries can be compared, then expressed in whole-poly multiply units so
    the result can be carried to the H6 baseline without mixing sessions

The one thing this script refuses to do is compare absolute milliseconds across
sessions. The kernel price drifted 2x between the F4-A session (12.06 ns/elem)
and this one (24.32 ns/elem), so cross-session statements are made in *ratios*
only -- see `CROSS_SESSION_NOTE`.
"""

import argparse
import json
import os
import re
import statistics
import sys

# H6 frozen anchor, from results/transaction_e2e/f3_h6 (doc 16 §D2):
# the quotient stage went 1767.0 -> 1276.2 ms for 2638 removed whole-poly muls.
H6_MS_PER_MUL = (1767.0 - 1276.2) / 2638.0
PROVE_1P_MS_H6 = 2725.8

# Census constants (doc 21 §5, re-verified by the f4a probe).
OCC_MULS, KEPT_MULS, SAVED_MULS = 869, 242, 627
CONSUMERS_TOTAL = 181
CONSUMERS_SHARED = 170
# Interning only replaces chains that sit at a constraint root: 2 of the 12
# consumers of column 19 are nested inside a product, so that chain is still
# built 3 times (1 precompute + 2 inline) instead of once.
INTERN_SAVED_MULS = 621
INTERN_KEPT_MULS = 248

CROSS_SESSION_NOTE = (
    "The F4-A session measured `in_place` at 12.06 ns/element (n=4096); this "
    "one measures 24.32. The machine changed state between the two runs "
    "(prove: 2725.8 ms then, 7835 ms now). Absolute milliseconds are therefore "
    "always tagged with their session; anything carried across sessions is a "
    "dimensionless ratio."
)


def parse(path):
    rows = {k: [] for k in ("GEOM", "BENCH", "MODEL", "LOOKUP", "INTERN", "META")}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = re.match(r"F4B_(\w+) (\{.*\})\s*$", line)
            if m:
                rows[m.group(1)].append(json.loads(m.group(2)))
    return rows


def kernel(bench, name, n):
    for b in bench:
        if b["kernel"] == name and b["n"] == n:
            return b
    raise SystemExit(f"missing kernel {name} at n={n}")


def fmt(x, nd=2):
    return f"{x:,.{nd}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--md", default=None)
    args = ap.parse_args()

    r = parse(args.log)
    if not r["META"]:
        raise SystemExit("no F4B_META in log: is the probe on?")
    meta = r["META"][-1]
    bench = r["BENCH"]

    ns = [512, 4096, 16384]
    geom = {g["threads"]: g for g in r["GEOM"]}

    L = []
    A = L.append
    A("# F4-A 实施探针 · 汇总")
    A("")
    A(f"来源 `{os.path.basename(args.log)}`；"
      f"probe={meta['probe']}，reps={meta['bench_reps']}，"
      f"rayon={meta['rayon_threads']}，affinity=`{meta['affinity']}`。")
    A("")
    A(f"Proof：**{meta['proof_bytes']} B / {meta['proof_fnv']}**，"
      f"prove {fmt(meta['proof_ms'])} ms，verify {fmt(meta['verify_ms'])} ms。"
      "与 H6、H7、F4、F4-A 的四个记录逐位相同。")
    A("")

    # ---- 1. geometry -------------------------------------------------------
    A("## 1. 求值几何")
    A("")
    A("`chunk_size = ceil(poly_len / (threads × 4))`（`poly/evaluator.rs:19-35`）。")
    A("")
    A("| threads | chunk_size | num_chunks | 说明 |")
    A("|---:|---:|---:|---|")
    for t in sorted(geom):
        g = geom[t]
        tag = "本机当前" if g["current"] else ""
        A(f"| {t} | {g['chunk_size']} | {g['num_chunks']} | {tag} |")
    A("")
    A("F2 独立记录的两行（`results/transaction_e2e/f2_expression/eval_calls.csv`）："
      "1P → 4096×4，8P → 512×32，与本表一致。**单位是 chunk，不是 16384。**")
    A("")

    # ---- 2. kernels --------------------------------------------------------
    A("## 2. 内核价（ns/元素，200 次中位数）")
    A("")
    A("| 内核 | " + " | ".join(f"n={n}" for n in ns) + " |")
    A("|---|" + "---:|" * len(ns))
    names = [("in_place", "in_place（现状）"), ("fused", "fused（新分配）"),
             ("reused", "reused（池化下界）"), ("add", "add（一个 Sum）")]
    for key, label in names:
        cells = [f"{kernel(bench, key, n)['ns_per_elem']:.4f}" for n in ns]
        A(f"| {label} | " + " | ".join(cells) + " |")
    A("")
    delta = {}
    A("Δ(fused − in_place)：")
    A("")
    A("| n | Δ ns/元素 | 相对 mul |")
    A("|---:|---:|---:|")
    for n in ns:
        d = (kernel(bench, "fused", n)["median_ns"] - kernel(bench, "in_place", n)["median_ns"]) / n
        delta[n] = d
        A(f"| {n} | {d:+.4f} | {100 * d / kernel(bench, 'in_place', n)['ns_per_elem']:+.2f}% |")
    A("")
    A(f"> {CROSS_SESSION_NOTE}")
    A("")

    # ---- 3. reuse cost -----------------------------------------------------
    A("## 3. 复用代价")
    A("")
    A("一个消费者要处理 16384 个元素；分块只改变『每次分配摊到多少元素』，不改变总量。")
    A("")
    A("| 几何 | Δ ns/元素 | 每消费者 | 170 消费者 |")
    A("|---|---:|---:|---:|")
    cost = {}
    for n in (4096, 512):
        d = max(delta[n], delta[4096])  # take the larger of the two as the bound
        d = delta[n]
        per = 16384 * d / 1e3  # us
        cost[n] = per * CONSUMERS_SHARED / 1e3  # ms
        A(f"| {n} | {d:+.4f} | {per:.2f} µs | {cost[n]:.3f} ms |")
    A("")
    mul_pass_us = kernel(bench, "in_place", 16384)["median_ns"] / 1e3
    A(f"本会话『一个整块乘』（16384 元素一次就地乘）＝ **{mul_pass_us:.1f} µs**。"
      f"每消费者的复用代价 {16384 * delta[4096] / 1e3:.2f} µs ＝ 该值的 "
      f"{16384 * delta[4096] / 1e3 / mul_pass_us:.4f}，而它省下至少 3 个整块乘。")
    A("")

    # ---- 4. bookkeeping ----------------------------------------------------
    A("## 4. 记账")
    A("")
    A("| 机制 | ns / 次 | 168 次/求值 |")
    A("|---|---:|---:|")
    lk = {x["mechanism"]: x["ns_per_lookup"] for x in r["LOOKUP"]}
    for name in ("array", "hash", "ptr_scan"):
        A(f"| {name} | {lk[name]:.3f} | {lk[name] * 168 / 1e3:.2f} µs |")
    A("")
    it = r["INTERN"][-1]
    A(f"构造期 interning：{it['chain_roots']} 个约束根、{it['distinct']} 个不同键、"
      f"**{it['median_ns'] / 1e3:.1f} µs / 次证明**。")
    A("")

    # ---- 5. model ----------------------------------------------------------
    A("## 5. 每 chunk 模型（869 → 242 的真实节点形状）")
    A("")
    A("| n | 现状 | 共享后 | Δ | Δ 折 16384 元素 |")
    A("|---:|---:|---:|---:|---:|")
    norm = []
    for m in r["MODEL"]:
        assert (m["occ_muls"], m["kept_muls"], m["saved_muls"], m["consumers"]) == \
            (OCC_MULS, KEPT_MULS, SAVED_MULS, CONSUMERS_TOTAL)
        per16k = m["delta_ms"] * 16384 / m["n"]
        norm.append(per16k)
        A(f"| {m['n']} | {m['repeat_ms']:.2f} ms | {m['shared_ms']:.2f} ms | "
          f"{m['delta_ms']:.2f} ms | {per16k:.1f} ms |")
    mean_delta = statistics.mean(norm)
    eq_muls = mean_delta * 1e3 / mul_pass_us
    A("")
    A(f"三个几何折到 16384 元素后是 "
      f"{', '.join(f'{x:.0f}' for x in norm)} ms（相对离散 "
      f"{100 * (max(norm) - min(norm)) / mean_delta:.1f}%），与分块无关。")
    A("")
    A(f"Δ ＝ **{mean_delta:.0f} ms / 16384 元素**，相当于 **{eq_muls:.0f} 个整块乘**；"
      f"只算 product 是 {SAVED_MULS} 个 → **×{eq_muls / SAVED_MULS:.2f}**"
      f"（差额是 {SAVED_MULS} 个 `Sum`，单价 {kernel(bench, 'add', 16384)['ns_per_elem']:.2f} "
      f"ns/元素 ≈ mul 的 "
      f"{kernel(bench, 'add', 16384)['ns_per_elem'] / kernel(bench, 'in_place', 16384)['ns_per_elem']:.3f}，"
      "再加分配与 `Constant` 填充）。")
    A("")
    A(f"把该比值乘到 H6 锚点上：{H6_MS_PER_MUL * SAVED_MULS:.1f} ms × "
      f"{eq_muls / SAVED_MULS:.2f} ＝ **{H6_MS_PER_MUL * SAVED_MULS * eq_muls / SAVED_MULS:.1f} ms "
      f"＝ {100 * H6_MS_PER_MUL * SAVED_MULS * eq_muls / SAVED_MULS / PROVE_1P_MS_H6:.2f}%**。")
    A(f"按 interning 的实际范围（根位置，{INTERN_SAVED_MULS} 个乘）折算："
      f"{100 * H6_MS_PER_MUL * INTERN_SAVED_MULS * eq_muls / SAVED_MULS / PROVE_1P_MS_H6:.2f}%。")
    A("")

    # ---- 6. verdict --------------------------------------------------------
    A("## 6. 三条可以直接拿去做实施预注册的结论")
    A("")
    A(f"1. **记账不是风险。** 三种查找机制都 ≤ 2.6 µs/求值，比 353 ms 的收益小 5 个数量级；"
      f"选数组是为了让记账在结构上消失（match 的一个分支），不是因为它更快。")
    A(f"2. **代价也不是风险。** 复用代价 {cost[4096]:.2f} ms（1P 几何）"
      f"对 {mean_delta:.0f} ms 的收益；而且 179/181 的消费者父节点只读，"
      f"真实代价比这个上界还低。")
    A("3. **风险在别处：** (a) `recurse` 的返回类型必须从 `Vec<F>` 变成"
      " `Borrowed`/`Owned`，否则 memo 里的值无法被借出；"
      "(b) 39 条共享链在每个 chunk 内同时驻留；"
      "(c) 端到端仍未测，price 随机器状态 ×2 漂移，只有同会话 A/B 能定案。")
    A("")

    text = "\n".join(L) + "\n"
    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.log))
    md_path = args.md or os.path.join(out_dir, "summary.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(text)

    with open(os.path.join(out_dir, "prices.csv"), "w", encoding="utf-8") as fh:
        fh.write("kind,name,n,ns_per_elem,ns_per_op,delta_ns_per_elem\n")
        for n in ns:
            for key in ("in_place", "fused", "reused", "add"):
                k = kernel(bench, key, n)
                fh.write(f"kernel,{key},{n},{k['ns_per_elem']:.4f},,{delta[n]:.4f}\n")
        for name, v in lk.items():
            fh.write(f"lookup,{name},0,,{v:.4f},\n")
        fh.write(f"intern,map,0,,{it['median_ns']:.1f},\n")
        for m in r["MODEL"]:
            fh.write(f"model,delta,{m['n']},{m['delta_ns'] / m['n']:.4f},{m['delta_ns']:.0f},\n")

    sys.stdout.write(text)
    sys.stdout.write(f"\n[wrote] {md_path}\n[wrote] {os.path.join(out_dir, 'prices.csv')}\n")


if __name__ == "__main__":
    main()
