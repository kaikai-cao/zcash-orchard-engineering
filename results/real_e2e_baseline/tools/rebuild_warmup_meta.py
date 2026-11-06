#!/usr/bin/env python3
"""Rebuild warm-up meta.json from ALREADY-WRITTEN CSVs. No transaction, no
state-changing RPC. Exists only because the first run of baseline_measure_v2.py
crashed in the OUTPUT layer after all measurement CSVs were flushed (empty
valid_rows, since warm-up rows are valid=false by design).

The warm-up transaction was executed exactly ONCE and is NOT re-run: re-running
would add a second transaction and change chain state, violating §9.3. This tool
only reads local files plus two read-only RPCs (height / balance).
"""
import base64
import csv
import http.client
import json
import os
import platform
import time

ACCOUNT = "5fd7056a-3e6e-455f-b75d-d80b0556fea9"
COOKIE = os.path.expanduser("~/z3-regtest/data/zallet/.cookie")


def rpc(port, method, params, auth=True, timeout=30):
    hdr = {"Content-Type": "application/json"}
    if auth:
        hdr["Authorization"] = "Basic " + base64.b64encode(
            open(COOKIE).read().strip().encode()).decode()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.request("POST", "/", json.dumps({"jsonrpc": "2.0", "method": method,
                                       "params": params, "id": 1}), hdr)
    d = json.loads(c.getresponse().read())
    c.close()
    if d.get("error"):
        raise RuntimeError(str(d["error"]))
    return d["result"]


def read(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--warmup-dir", required=True)
    ap.add_argument("--g0-json", required=True)
    ap.add_argument("--run-dir", required=True)
    a = ap.parse_args()

    rows = read(os.path.join(a.warmup_dir, "rounds.csv"))
    wins = read(os.path.join(a.warmup_dir, "rss_windows.csv"))
    stages = read(os.path.join(a.warmup_dir, "stage_timings.csv"))
    g0 = json.load(open(a.g0_json, encoding="utf-8"))
    bal_initial = json.load(open(os.path.join(a.run_dir, "logs/balance_initial.json")))
    bal_post_fund = json.load(open(os.path.join(a.run_dir, "logs/balance_post_fund.json")))

    pools = rpc(40232, "z_getbalanceforaccount", [ACCOUNT])["pools"]
    z = {k: int(v.get("valueZat", 0)) for k, v in pools.items()}
    h = rpc(18232, "getblockchaininfo", [], False)["blocks"]

    meta = {
        "label": "warmup", "valid": False, "counted_in_statistics": False,
        "rebuilt_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "why_rebuilt": ("first run of baseline_measure_v2.py crashed in the OUTPUT layer "
                        "(statistics.median on empty valid_rows) AFTER all measurement CSVs "
                        "were flushed; the transaction was NOT re-run"),
        "transaction_executed_exactly_once": True,
        "kernel": platform.release(),
        "prereg_prefix_sha256": "c3d8f999e910e471c218777dc62680994c596324829b1eb16703055a6e666de1",
        "errata": "docs/orchard/29_real_e2e_baseline.md §14 Errata-1 (v1.0-errata-1)",
        "derived_from": "zcash-sapling-engineering@5e46712:experiments/e2e_testnet_tx/pczt_bench_pool.py",
        "scenario": "frozen_pczt", "version": "official_beta3_no_f4a",
        "g0": {"label": g0.get("label"), "overall": g0.get("overall"),
               "n_pass": g0.get("n_pass"), "need_zat": g0.get("need_zat"),
               "processes": g0.get("raw", {}).get("processes"),
               "affinity": g0.get("raw", {}).get("affinity")},
        "blocks_before_fund": bal_initial["blocks"],
        "balances_initial_zat": bal_initial["pools_zat"],
        "fund_once": {"nominal_zec": 55, "count": 1,
                      "txid": bal_post_fund.get("fund_txid"),
                      "actual_ironwood_zec": bal_post_fund["pools_zec"]["ironwood"],
                      "blocks_after": bal_post_fund["blocks"]},
        "blocks_after_warmup": h,
        "balances_after_warmup_zat": z,
        "balances_after_warmup_zec": {k: round(v / 1e8, 5) for k, v in z.items()},
        "n_rows": len(rows), "n_valid": 0,
        "warmup_row": rows[0] if rows else None,
        "rss_window": wins[0] if wins else None,
        "stage_timings": stages,
        "measurement_chain_validation": {
            "rss_window_samples": int(wins[0]["rss_window_samples"]) if wins else 0,
            "phase_anomalies": int(wins[0]["phase_anomalies"]) if wins else -1,
            "wall_minus_build_ms": float(rows[0]["wall_minus_build_ms"]) if rows else None,
            "three_gates": [rows[0].get("extract_success"),
                            rows[0].get("broadcast_accepted"),
                            rows[0].get("confirmed")] if rows else None,
            "note": "measurement-chain evidence only; timing/RSS numbers are NOT baseline statistics (§14.4)"},
    }
    with open(os.path.join(a.warmup_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(json.dumps({"rebuilt": True, "n_rows": meta["n_rows"],
                      "n_valid": meta["n_valid"], "blocks": h,
                      "ironwood_zec": round(z.get("ironwood", 0) / 1e8, 5)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
