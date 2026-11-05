#!/usr/bin/env python3
"""Stage F4-A.3: action-count scaling report.

Reads three kinds of logs from `results/transaction_e2e/f4a3_scale/`

    time_<geo>_<pool>_<n>.log    clean build, ORCHARD_E2_TIMING only -> T + stages
    count_<geo>_<pool>_<n>.log   same build + ORCHARD_F4A_STATS          -> structure
    mem_<geo>_<pool>_<n>.log     diagnostic build + counting allocator   -> resources

and writes `summary.md`. Every number is a paired A/B inside one process.
Nothing here compares an absolute millisecond with another session or with
doc 24 (doc 26 §5).

Usage:
    python scripts/analysis/f4a_scale_report.py [--dir results/transaction_e2e/f4a3_scale]
"""

import argparse
import json
import random
import statistics
from math import comb
from pathlib import Path

STAGES = [
    "instance",
    "synthesis",
    "advice_commit",
    "eval_setup",
    "lookup_permute",
    "permutation",
    "lookup_product",
    "quotient",
    "opening",
]

POOLS = ["ironwood", "orchard"]
GEOS = [("1p", "1P"), ("8p", "8P")]
NS = {"ironwood": [1, 2, 4, 8], "orchard": [2, 4, 8]}


def jsonl(path, prefix):
    out = []
    if not Path(path).exists():
        return out
    for line in open(path, encoding="utf-8", errors="replace"):
        if line.startswith(prefix + " "):
            out.append(json.loads(line[len(prefix) + 1 :]))
    return out


def bootstrap_ci(xs, reps=10000, lo=2.5, hi=97.5, seed=0):
    if not xs:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(xs)
    meds = sorted(statistics.median(rng.choices(xs, k=n)) for _ in range(reps))
    return (meds[int(reps * lo / 100)], meds[int(reps * hi / 100) - 1])


def sign_test(xs):
    n = len(xs)
    if n == 0:
        return 0, 0, float("nan")
    k = sum(1 for x in xs if x < 1.0)
    p = 2 * sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2**n
    return k, n, min(1.0, p)


def pairs_of(rows):
    by = {}
    for r in rows:
        by.setdefault(r["pair"], {})[r["arm"]] = r
    return [(by[k]["A"], by[k]["B"]) for k in sorted(by) if "A" in by[k] and "B" in by[k]]


def f(x, nd=1):
    if x is None or x != x:
        return "—"
    return f"{x:,.{nd}f}"


def pct(x, nd=2):
    return f"{x*100:+,.{nd}f}%"


def load_time(d, geo, pool, n):
    return jsonl(d / f"time_{geo}_{pool}_{n}.log", "F4AS_RUN")


def load_count(d, geo, pool, n):
    """F4AS_RUN rows plus the F4A_IMPL line that follows each of them."""
    path = d / f"count_{geo}_{pool}_{n}.log"
    runs, impls = [], []
    if not path.exists():
        return runs, impls
    last = None
    for line in open(path, encoding="utf-8", errors="replace"):
        if line.startswith("F4AS_RUN "):
            last = json.loads(line[len("F4AS_RUN ") :])
            runs.append(last)
        elif line.startswith("F4A_IMPL ") and last is not None:
            rec = json.loads(line[len("F4A_IMPL ") :])
            rec["_arm"] = last["arm"]
            rec["_n"] = last["actions"]
            impls.append(rec)
    return runs, impls


def load_mem(d, geo, pool, n):
    base = d / f"mem_{geo}_{pool}_{n}.log"
    mem = jsonl(base, "F4A2_MEM")
    alloc = jsonl(base, "F4A2_ALLOC")
    by = {(r["pair"], r["arm"]): r for r in mem}
    for a in alloc:
        m = by.get((a["pair"], a["arm"]))
        if m is not None:
            m["alloc"] = a
    return mem


def describe(rows):
    """Paired statistics for one (geo, pool, n) timing log."""
    prs = pairs_of(rows)
    if not prs:
        return None
    dT = [a["prove_ms"] - b["prove_ms"] for a, b in prs]
    ratio = [b["prove_ms"] / a["prove_ms"] for a, b in prs]
    med_a = statistics.median(a["prove_ms"] for a, _ in prs)
    med_b = statistics.median(b["prove_ms"] for _, b in prs)
    ci = bootstrap_ci(dT)
    ci_r = bootstrap_ci(ratio)
    k, nn, p = sign_test(ratio)
    stage_d, stage_a, stage_b = {}, {}, {}
    for s in STAGES:
        da = [a["stages_ms"][s] for a, b in prs if s in a["stages_ms"]]
        db = [b["stages_ms"][s] for a, b in prs if s in b["stages_ms"]]
        if da and db:
            stage_a[s] = statistics.median(da)
            stage_b[s] = statistics.median(db)
            stage_d[s] = statistics.median(
                [a["stages_ms"][s] - b["stages_ms"][s] for a, b in prs]
            )
    resid_a = statistics.median(
        a["prove_ms"] - sum(a["stages_ms"].values()) for a, _ in prs
    )
    resid_b = statistics.median(
        b["prove_ms"] - sum(b["stages_ms"].values()) for _, b in prs
    )
    return dict(
        n=len(prs),
        med_a=med_a,
        med_b=med_b,
        dT=statistics.median(dT),
        dT_ci=ci,
        ratio=statistics.median(ratio),
        ratio_ci=ci_r,
        k=k,
        p=p,
        stage_d=stage_d,
        stage_a=stage_a,
        stage_b=stage_b,
        dsum=statistics.median(
            [sum(b["stages_ms"].values()) - sum(a["stages_ms"].values()) for a, b in prs]
        ),
        resid_a=resid_a,
        resid_b=resid_b,
        bytes_=sorted({r["proof_bytes"] for r in rows}),
        fnv=sorted({r["proof_fnv"] for r in rows}),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="results/transaction_e2e/f4a3_scale")
    d = Path(ap.parse_args().dir)

    time = {}
    for geo, _ in GEOS:
        for pool in POOLS:
            for n in NS[pool]:
                st = describe(load_time(d, geo, pool, n))
                if st:
                    time[(geo, pool, n)] = st

    cnt = {}
    for geo, _ in GEOS:
        for pool in POOLS:
            for n in NS[pool]:
                runs, impls = load_count(d, geo, pool, n)
                good = [r for r in impls if r.get("enabled")]
                if good:
                    cnt[(geo, pool, n)] = good[-1]

    memd = {}
    for geo, _ in GEOS:
        for pool in POOLS:
            for n in NS[pool]:
                rows = load_mem(d, geo, pool, n)
                prs = pairs_of(rows)
                if prs:
                    def med(key, arm):
                        vals = [p[0 if arm == "A" else 1].get(key) for p in prs]
                        vals = [v for v in vals if v is not None]
                        return statistics.median(vals) if vals else None
                    memd[(geo, pool, n)] = dict(
                        prs=prs,
                        ws_delta=(
                            med("ws_delta_mib", "A"),
                            med("ws_delta_mib", "B"),
                        ),
                        priv_delta=(
                            med("priv_delta_mib", "A"),
                            med("priv_delta_mib", "B"),
                        ),
                        alloc_calls=(
                            statistics.median(p[0]["alloc"]["alloc_calls"] for p in prs),
                            statistics.median(p[1]["alloc"]["alloc_calls"] for p in prs),
                        ),
                        alloc_bytes=(
                            statistics.median(p[0]["alloc"]["alloc_bytes"] for p in prs),
                            statistics.median(p[1]["alloc"]["alloc_bytes"] for p in prs),
                        ),
                        memo_max=statistics.median(
                            p[1]["alloc"].get("memo_bytes_max_chunk", 0) for p in prs
                        ),
                        memo_slots=statistics.median(
                            p[1]["alloc"].get("memo_slots", 0) for p in prs
                        ),
                        fnv=sorted({r["proof_fnv"] for r in rows}),
                        bytes_=sorted({r["proof_bytes"] for r in rows}),
                    )

    L = []
    A = L.append
    A("# F4-A.3 · Action 数量适用边界（实测）")
    A("")
    A("预注册见 [`26_f4a_action_scaling_prereg.md`](../../docs/orchard/26_f4a_action_scaling_prereg.md)。")
    A("全部数字为**同进程、逐对交错**的 A/B；不与 doc 24 / doc 25 的绝对毫秒比较。")
    A("")

    # ---- 1. main table ---------------------------------------------------
    for geo, gl in GEOS:
        A(f"## 1{'a' if geo == '1p' else 'b'}. 主曲线（{gl}）")
        A("")
        A("| 池 | n | A 中位 ms | B 中位 ms | ΔT ms | 95% CI（ΔT） | ΔT/n | B/A | 95% CI（比） | B<A | p |")
        A("|---|---:|---:|---:|---:|---|---:|---:|---|---:|---:|")
        for pool in POOLS:
            for n in NS[pool]:
                st = time.get((geo, pool, n))
                if not st:
                    continue
                A(
                    f"| {pool} | {n} | {f(st['med_a'])} | {f(st['med_b'])} | "
                    f"**{f(st['dT'])}** | [{f(st['dT_ci'][0])}, {f(st['dT_ci'][1])}] | "
                    f"{f(st['dT']/n)} | **{st['ratio']:.4f}** | "
                    f"[{st['ratio_ci'][0]:.4f}, {st['ratio_ci'][1]:.4f}] | "
                    f"{st['k']}/{st['n']} | {st['p']:.1e} |"
                )
        A("")

    # ---- 2. stage attribution --------------------------------------------
    A("## 2. 阶段归因")
    A("")
    A("每格是 Δstage（A−B，ms，正数表示 B 更快）与它占 Δprove 的份额。")
    A("")
    for geo, gl in GEOS:
        A(f"**{gl}**")
        A("")
        A("| 池 | n | quotient Δ | 份额 | Σ九阶段 Δ | Δprove | 残差 A/B ms | 其它阶段最大 \\|Δ\\| |")
        A("|---|---:|---:|---:|---:|---:|---|---:|")
        for pool in POOLS:
            for n in NS[pool]:
                st = time.get((geo, pool, n))
                if not st:
                    continue
                q = st["stage_d"].get("quotient")
                share = (q / st["dT"]) if q and st["dT"] else float("nan")
                others = {k: v for k, v in st["stage_d"].items() if k != "quotient"}
                worst = max(others.items(), key=lambda kv: abs(kv[1])) if others else ("—", 0.0)
                A(
                    f"| {pool} | {n} | {f(q)} | {pct(share)} | {f(st['dsum'])} | "
                    f"{f(st['dT'])} | {f(st['resid_a'])} / {f(st['resid_b'])} | "
                    f"{worst[0]} {f(worst[1])} |"
                )
        A("")

    # ---- 3. structure -----------------------------------------------------
    A("## 3. 结构计数（`ORCHARD_F4A_STATS=1`）")
    A("")
    A("| 几何 | 池 | n | chains | slots | root 消费者 | chain_muls | structural_mul_eliminations | memo_reads | `h` FNV |")
    A("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for geo, gl in GEOS:
        for pool in POOLS:
            for n in NS[pool]:
                c = cnt.get((geo, pool, n))
                if not c:
                    continue
                A(
                    f"| {gl} | {pool} | {n} | {c['chains']} | {c['slots']} | "
                    f"{c['root_consumers']} | {c['chain_muls']} | "
                    f"**{c['structural_mul_eliminations']}** | {c['memo_reads']} | `{c['h_fnv']}` |"
                )
    A("")

    # ---- 4. resources -----------------------------------------------------
    A("## 4. 资源（诊断构建）")
    A("")
    A("| 几何 | 池 | n | WS Δ A→B MiB | Private Δ A→B MiB | memo/chunk MiB | memo 槽 | alloc calls A→B | alloc bytes A→B GiB |")
    A("|---|---|---:|---|---|---:|---:|---|---|")
    for geo, gl in GEOS:
        for pool in POOLS:
            for n in NS[pool]:
                m = memd.get((geo, pool, n))
                if not m:
                    continue
                A(
                    f"| {gl} | {pool} | {n} | {f(m['ws_delta'][0],2)} → {f(m['ws_delta'][1],2)} | "
                    f"{f(m['priv_delta'][0],2)} → {f(m['priv_delta'][1],2)} | "
                    f"{f(m['memo_max']/1048576,3)} | {int(m['memo_slots'])} | "
                    f"{m['alloc_calls'][0]:,.0f} → {m['alloc_calls'][1]:,.0f} | "
                    f"{m['alloc_bytes'][0]/2**30:.3f} → {m['alloc_bytes'][1]/2**30:.3f} |"
                )
    A("")

    # ---- 5. criteria ------------------------------------------------------
    A("## 5. 判据逐条")
    A("")
    A("| # | 判据 | 实测 | 结论 |")
    A("|---|---|---|---|")

    # G1
    bad = []
    for k, st in time.items():
        if len(st["bytes_"]) != 1 or len(st["fnv"]) != 1:
            bad.append(f"{k[0]}/{k[1]}/{k[2]}: bytes={st['bytes_']} fnv={st['fnv']}")
    anchor = time.get(("1p", "orchard", 2), {}).get("fnv")
    ok_anchor = anchor == ["0xa6bcd171d69e969a"]
    A(
        f"| G1 | 每配置 proof 字节与指纹 A/B 一致；orchard n=2 指纹 = `0xa6bcd171d69e969a` | "
        f"{'全部一致，' if not bad else '; '.join(bad[:3])} 锚点{'命中' if ok_anchor else '未命中：' + str(anchor)} | "
        f"**{'PASS' if (not bad and ok_anchor) else 'FAIL'}** |"
    )

    # G2
    g2 = time.get(("1p", "orchard", 2))
    if g2:
        ok = 3400 <= g2["med_a"] <= 4100
        A(
            f"| G2 | orchard n=2 的 1P A 中位 ∈ [3 400, 4 100] ms | {f(g2['med_a'])} | "
            f"**{'PASS' if ok else 'FAIL'}** |"
        )

    # G3
    worst_g3, worst_g3_key = 0.0, None
    for k, st in time.items():
        for arm_key, arm in (("resid_a", "A"), ("resid_b", "B")):
            rel = abs(st[arm_key]) / (st["med_a"] if arm == "A" else st["med_b"])
            if rel > worst_g3:
                worst_g3, worst_g3_key = rel, (k, arm)
    A(
        f"| G3 | 每配置残差 < 该臂 prove 的 1% | 最大 {worst_g3*100:.2f}%（{worst_g3_key[0][0]}/{worst_g3_key[0][1]}/{worst_g3_key[0][2]} arm {worst_g3_key[1]}） | "
        f"**{'PASS' if worst_g3 < 0.01 else 'FAIL'}** |"
    )

    # A1
    for geo, gl in GEOS:
        vals = [time[(geo, "ironwood", n)]["dT"] / n for n in (2, 4, 8) if (geo, "ironwood", n) in time]
        if len(vals) == 3:
            spread = (max(vals) - min(vals)) / statistics.mean(vals)
            A(
                f"| A1 | ΔT(n)/n 在 n=2,4,8 的（极差/均值）≤ 20% | {gl}: "
                f"{', '.join(f(n, 0) for n in vals)} ⇒ {spread*100:.1f}% | "
                f"**{'PASS' if spread <= 0.20 else 'FAIL'}** |"
            )

    # A2
    worst = None
    for k, st in time.items():
        q = st["stage_d"].get("quotient")
        if q and st["dT"]:
            sh = q / st["dT"]
            if worst is None or sh < worst[0]:
                worst = (sh, k)
    if worst:
        A(
            f"| A2 | 每配置 Δquotient/Δprove ≥ 0.90 | 最小 {worst[0]*100:.1f}%"
            f"（{worst[1][0]}/{worst[1][1]}/{worst[1][2]}） | "
            f"**{'PASS' if worst[0] >= 0.90 else 'FAIL'}** |"
        )

    # A3
    seq = [time[("1p", "ironwood", n)]["ratio"] for n in (1, 2, 4, 8) if ("1p", "ironwood", n) in time]
    mono = all(seq[i + 1] <= seq[i] + 0.01 for i in range(len(seq) - 1))
    A(
        f"| A3 | ironwood 的 B/A 随 n 非递增（1P，容差 0.01） | "
        f"{' → '.join(f'{x:.4f}' for x in seq)} | **{'PASS' if mono else 'FAIL'}** |"
    )

    # A4
    for geo, gl in GEOS:
        per_pool = {}
        for pool in POOLS:
            vals = [memd[(geo, pool, n)]["memo_max"] for n in NS[pool] if (geo, pool, n) in memd]
            if len(vals) >= 2:
                spread = (max(vals) - min(vals)) / statistics.mean(vals)
                per_pool[pool] = spread
        if per_pool:
            ok = all(v <= 0.05 for v in per_pool.values())
            A(
                f"| A4 | memo 每 chunk 驻留对 n 不变（≤5%） | {gl}: "
                f"{', '.join(f'{p} {v*100:.1f}%' for p, v in per_pool.items())} | "
                f"**{'PASS' if ok else 'FAIL'}** |"
            )

    # A5
    for geo, gl in GEOS:
        per_pool = {}
        for pool in POOLS:
            vals = [
                cnt[(geo, pool, n)]["memo_reads"] / n
                for n in NS[pool]
                if (geo, pool, n) in cnt
            ]
            if len(vals) >= 2:
                per_pool[pool] = (max(vals) - min(vals)) / statistics.mean(vals)
        if per_pool:
            ok = all(v <= 0.15 for v in per_pool.values())
            A(
                f"| A5 | memo_reads / n 相对极差 ≤ 15% | {gl}: "
                f"{', '.join(f'{p} {v*100:.1f}%' for p, v in per_pool.items())} | "
                f"**{'PASS' if ok else 'FAIL'}** |"
            )

    # A6
    for geo, gl in GEOS:
        per_pool = {}
        for pool in POOLS:
            vals = []
            for n in NS[pool]:
                m = memd.get((geo, pool, n))
                if m:
                    vals.append((m["alloc_calls"][0] - m["alloc_calls"][1]) / n)
            if len(vals) >= 2:
                per_pool[pool] = (max(vals) - min(vals)) / statistics.mean(vals)
        if per_pool:
            ok = all(v <= 0.25 for v in per_pool.values())
            A(
                f"| A6 | Δalloc_calls / n 相对极差 ≤ 25% | {gl}: "
                f"{', '.join(f'{p} {v*100:.1f}%' for p, v in per_pool.items())} | "
                f"**{'PASS' if ok else 'FAIL'}** |"
            )

    # A7
    for geo, gl in GEOS:
        per_pool = {}
        for pool in POOLS:
            vals = [
                cnt[(geo, pool, n)]["structural_mul_eliminations"] / n
                for n in NS[pool]
                if (geo, pool, n) in cnt
            ]
            if len(vals) >= 2:
                per_pool[pool] = (max(vals) - min(vals)) / statistics.mean(vals)
        if per_pool:
            ok = all(v <= 0.15 for v in per_pool.values())
            A(
                f"| A7 | structural_mul_eliminations / n 相对极差 ≤ 15% | {gl}: "
                f"{', '.join(f'{p} {v*100:.1f}%' for p, v in per_pool.items())} | "
                f"**{'PASS' if ok else 'FAIL'}** |"
            )
    A("")

    # ---- 5b. boundary recheck (criteria unchanged) ------------------------
    A("### 5b. A4 / A6 / A7 的 n=1 边界复核")
    A("")
    A("三条判据都按**全部 n** 取极差，而 n=1 只有一个 action 实例，跨 action 的重复链"
      "**不存在**，所以它天然是标度区之外的一个端点。判据不改；这里把 ironwood 的 n≥2 子集"
      "单独算一遍，用来区分“模型错了”与“端点被算进了极差”。")
    A("")
    A("| 判据 | 范围 | 相对极差 | 结论 |")
    A("|---|---|---:|---|")

    def spread(vals):
        return (max(vals) - min(vals)) / statistics.mean(vals) if len(vals) >= 2 else float("nan")

    for geo, gl in GEOS:
        vals = [memd[(geo, "ironwood", n)]["memo_max"] for n in (2, 4, 8) if (geo, "ironwood", n) in memd]
        A(f"| A4 | {gl} ironwood n≥2 | {spread(vals)*100:.1f}% | **{'PASS' if spread(vals) <= 0.05 else 'FAIL'}** |")
        vals = [
            (memd[(geo, "ironwood", n)]["alloc_calls"][0] - memd[(geo, "ironwood", n)]["alloc_calls"][1]) / n
            for n in (2, 4, 8)
            if (geo, "ironwood", n) in memd
        ]
        A(f"| A6 | {gl} ironwood n≥2 | {spread(vals)*100:.1f}% | **{'PASS' if spread(vals) <= 0.25 else 'FAIL'}** |")
        vals = [
            (memd[(geo, "ironwood", n)]["alloc_bytes"][0] - memd[(geo, "ironwood", n)]["alloc_bytes"][1]) / n
            for n in (2, 4, 8)
            if (geo, "ironwood", n) in memd
        ]
        A(f"| A6（字节侧） | {gl} ironwood n≥2 | {spread(vals)*100:.1f}% | **{'PASS' if spread(vals) <= 0.25 else 'FAIL'}** |")
        vals = [
            cnt[(geo, "ironwood", n)]["structural_mul_eliminations"] / n
            for n in (2, 4, 8)
            if (geo, "ironwood", n) in cnt
        ]
        A(f"| A7 | {gl} ironwood n≥2 | {spread(vals)*100:.1f}% | **{'PASS' if spread(vals) <= 0.15 else 'FAIL'}** |")
    A("")
    A("### 5c. 分配字节的比值")
    A("")
    A("它才是“代价随规模如何变”的直接读数：")
    A("")
    A("| 几何 | 池 | n | alloc bytes A GiB | B GiB | B/A |")
    A("|---|---|---:|---:|---:|---:|")
    for geo, gl in GEOS:
        for pool in POOLS:
            for n in NS[pool]:
                m = memd.get((geo, pool, n))
                if not m:
                    continue
                A(
                    f"| {gl} | {pool} | {n} | {m['alloc_bytes'][0]/2**30:.3f} | "
                    f"{m['alloc_bytes'][1]/2**30:.3f} | {m['alloc_bytes'][1]/m['alloc_bytes'][0]:.3f} |"
                )
    A("")

    # ---- 6. shape ---------------------------------------------------------
    A("## 6. 形状判定")
    A("")
    for geo, gl in GEOS:
        A(f"**{gl} · ironwood**（同一 flags、同一 pk，n 是唯一变量）")
        A("")
        base = time.get((geo, "ironwood", 1))
        A("| n | ΔT ms | ΔT/n ms | ΔT(n)/ΔT(1) | B/A |")
        A("|---:|---:|---:|---:|---:|")
        for n in (1, 2, 4, 8):
            st = time.get((geo, "ironwood", n))
            if st:
                scale = f"{st['dT']/base['dT']:.2f}×" if base and base["dT"] else "—"
                A(f"| {n} | {f(st['dT'])} | {f(st['dT']/n)} | {scale} | {st['ratio']:.4f} |")
        A("")

    out = d / "summary.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(L)} lines)")


if __name__ == "__main__":
    main()
