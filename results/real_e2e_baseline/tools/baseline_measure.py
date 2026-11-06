#!/usr/bin/env python3
"""doc 29 route-A real-PCZT single-arm baseline measurement.

Derived from zcash-sapling-engineering@5e46712
  experiments/e2e_testnet_tx/pczt_bench_pool.py
Only the five change classes allowed by doc 29 SS11.3 are applied:
  1. T_build = create+sign+prove+extract, plus T_wall as a cross-check
  2. extra per-round fields + valid flag + invalid_runs.csv
  3. find_pids() replaced by pgrep -x + /proc/PID/exe + SHA256
  4. 50 ms sampling with phase + monotonic window boundaries + window summary
  5. prove_share kept as a per-round ratio (both estimators are computed later)
RPC method names / parameter order / transaction parameters are unchanged.

No F4-A arm, no A/B, no cross-batch comparison.
"""
import argparse
import base64
import csv
import hashlib
import http.client
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time

ZA = ("127.0.0.1", 40232)
ZB = ("127.0.0.1", 18232)
COOKIE = os.path.expanduser("~/z3-regtest/data/zallet/.cookie")
ACCOUNT = "5fd7056a-3e6e-455f-b75d-d80b0556fea9"
SAP_UA = ("uregtest19tu34g0t8np34f43fwlnn7wy4lk59t7zmwh8xy5yr4vvgj3yfuu3zaga4yyjhx69ynphl5"
          "cwyte0ej8hqct6cqk3kez0rjhdl8lwwhgkt9x3v2zlsxjymru75mnm0mz62gc2uk6205n")
BIN = os.path.expanduser("~/z3-regtest/bin")

EXPECT_SHA = {
    "zebrad": "baf0b2cd31110c7fe7c05809fd7323d07548489be78d31caeccc91e1469cc6a7",
    "zallet": "523bf86a14cba2dfb1ea048c3a3d6ccc68bde0dbb92da60d44ce12568dea1c5a",
    "zallet-zaino": "e602e4a3d4e919c155b8f05dae29f0b9311631f167eaf25455166edf268208a7",
}
NEED_20_ZAT = int((3.0 + 0.001) * 1e8) * 20        # 60.02 ZEC, doc 29 SS8.2
VERSION = "official_beta3_no_f4a"
SCENARIO = "frozen_pczt"
STAGES = ["pczt_create", "pczt_sign", "pczt_prove", "pczt_extract",
          "broadcast", "mine", "confirm"]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _post(host, port, payload, auth=None, timeout=60):
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    hdr = {"Content-Type": "application/json"}
    if auth:
        hdr["Authorization"] = "Basic " + auth
    conn.request("POST", "/", json.dumps(payload), hdr)
    raw = conn.getresponse().read()
    conn.close()
    return json.loads(raw)


def zauth():
    return base64.b64encode(open(COOKIE).read().strip().encode()).decode()


def z(method, params, timeout=60):
    d = _post(ZA[0], ZA[1], {"jsonrpc": "2.0", "method": method,
                             "params": params, "id": 1}, zauth(), timeout)
    if d.get("error"):
        raise RuntimeError("%s -> %s" % (method, json.dumps(d["error"], ensure_ascii=False)))
    return d["result"]


def b(method, params, timeout=30):
    d = _post(ZB[0], ZB[1], {"jsonrpc": "2.0", "method": method,
                             "params": params, "id": 1}, None, timeout)
    if d.get("error"):
        raise RuntimeError("%s -> %s" % (method, json.dumps(d["error"], ensure_ascii=False)))
    return d["result"]


def height():
    try:
        return b("getblockchaininfo", [])["blocks"]
    except Exception:
        return None


def advance(n, timeout=180):
    """Mine n blocks; only the height delta counts (beta.3 returns a fake error)."""
    h0 = height() or 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            b("generate", [n])
        except RuntimeError:
            pass
        h1 = height()
        if h1 is not None and h1 >= h0 + n:
            return h1 - h0, (time.monotonic() - t0) * 1000
        time.sleep(1.5)
    return (height() or h0) - h0, (time.monotonic() - t0) * 1000


def wait_sync(timeout=300):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            s = z("getwalletstatus", [])
            wt = (s.get("wallet_tip") or {}).get("height") or 0
            nt = (s.get("node_tip") or {}).get("height") or 0
            target = max(nt, height() or 0)
            if wt and wt >= target:
                return wt, target
        except RuntimeError:
            pass
        time.sleep(1)
    return None, None


def pools():
    r = z("z_getbalanceforaccount", [ACCOUNT])
    return {k: int(v.get("valueZat", 0)) for k, v in r.get("pools", {}).items()}


# ---- doc 29 SS5.2: exact PID resolution (no pgrep -f fallback) -------------
def resolve(name):
    out = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True).stdout.split()
    for tok in out:
        if not tok.isdigit():
            continue
        pid = int(tok)
        try:
            exe = os.path.realpath("/proc/%d/exe" % pid)
        except OSError:
            continue
        if not os.path.isfile(exe):
            continue
        return {"pid": pid, "exe": exe, "sha256": sha256(exe),
                "basename": os.path.basename(exe)}
    return None


def cpu_allowed(pid):
    try:
        for line in open("/proc/%d/status" % pid):
            if line.startswith("Cpus_allowed_list:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def rss_kb(pid):
    if not pid:
        return None
    try:
        for line in open("/proc/%d/status" % pid):
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


class Sampler(threading.Thread):
    """50 ms VmRSS sampler; t_mono is the ABSOLUTE time.monotonic() value so the
    main loop can define the prove window with the same clock (doc 29 SS5.3)."""

    def __init__(self, path, wallet, zebra, interval=0.05):
        super().__init__(daemon=True)
        self.path, self.interval = path, interval
        self.wallet_pid = wallet["pid"]
        self.zebra_pid = zebra["pid"]
        self.stop_evt = threading.Event()
        self.phase = "outside"
        self.run_id = ""
        self.rows = []
        self.fh = open(path, "w", newline="")
        self.w = csv.writer(self.fh)
        self.w.writerow(["t_wall", "t_mono", "run_id", "phase", "wallet_pid",
                         "wallet_rss_kb", "zebrad_rss_kb"])
        self.fh.flush()

    def run(self):
        while not self.stop_evt.is_set():
            tm = time.monotonic()
            row = [time.strftime("%H:%M:%S"), "%.6f" % tm, self.run_id, self.phase,
                   self.wallet_pid, rss_kb(self.wallet_pid), rss_kb(self.zebra_pid)]
            self.w.writerow(row)
            self.fh.flush()
            self.rows.append(row)
            self.stop_evt.wait(self.interval)
        self.fh.close()

    def window(self, run_id, t0, t1):
        """Authoritative window = timestamps; phase is a cross-check only."""
        picked, anomalous = [], 0
        for row in self.rows:
            tm = float(row[1])
            in_window = (t0 <= tm <= t1)
            in_phase = (row[2] == run_id and row[3] == "in_prove")
            if in_window != in_phase:
                anomalous += 1
            if in_window:
                picked.append(row)
        return picked, anomalous


def one_round(idx, a, sampler, wallet, zebra, T):
    """One full create->sign->prove->extract + the 3 correctness gates."""
    row = {"run_id": "r%02d" % idx, "version": VERSION, "scenario": SCENARIO,
           "label": a.label, "wallet_pid": wallet["pid"], "wallet_exe": wallet["exe"],
           "wallet_sha256": wallet["sha256"]}
    fail_codes = []
    print("\n----- %s round %d/%d -----" % (a.label, idx, a.rounds))

    wait_sync()
    sampler.run_id = row["run_id"]

    t_wall_start = time.monotonic()
    t0 = time.monotonic()
    created = z("pczt_create", [ACCOUNT, [{"address": SAP_UA, "amount": a.send_zec}],
                                a.minconf, a.policy, a.fund_source])
    T["pczt_create"] = (time.monotonic() - t0) * 1000
    pczt = created["pczt"]
    insp = z("pczt_inspect", [pczt])          # diagnostic only, NOT timed
    row["policy"] = created.get("privacy_policy")
    row["fee_zat"] = insp.get("fee_zat")
    row["size_b64"] = len(pczt)
    row["actions"] = (insp.get("ironwood") or {}).get("actions")
    row["orchard_actions"] = (insp.get("orchard") or {}).get("actions", 0)
    row["sapling_spends"] = (insp.get("sapling") or {}).get("spends")

    t0 = time.monotonic()
    signed = z("pczt_sign", [pczt, row["policy"], False])
    T["pczt_sign"] = (time.monotonic() - t0) * 1000
    row["signed"] = signed.get("ironwood_signed")

    sampler.phase = "in_prove"
    t_prove_start = time.monotonic()
    t0 = t_prove_start
    proved = z("pczt_prove", [signed["pczt"]], timeout=1800)
    T["pczt_prove"] = (time.monotonic() - t0) * 1000
    t_prove_end = time.monotonic()
    sampler.phase = "outside"
    row["ironwood_proven"] = proved.get("ironwood_proven")

    t0 = time.monotonic()
    ext = z("pczt_extract", [proved["pczt"]])
    T["pczt_extract"] = (time.monotonic() - t0) * 1000
    t_wall_end = time.monotonic()

    row["txid"] = ext.get("txid")
    row["size_bytes"] = len(ext.get("hex", "")) // 2
    row["stored"] = ext.get("stored")
    row["extract_success"] = bool(row["txid"] and ext.get("hex") and ext.get("stored"))
    if not row["extract_success"]:
        fail_codes.append("extract_failed")

    # ---- main indicators (doc 29 SS3.2) -----------------------------------
    row["create_ms"] = round(T["pczt_create"], 3)
    row["sign_ms"] = round(T["pczt_sign"], 3)
    row["prove_ms"] = round(T["pczt_prove"], 3)
    row["extract_ms"] = round(T["pczt_extract"], 3)
    row["build_total_ms"] = round(sum(T[k] for k in
                                      ("pczt_create", "pczt_sign", "pczt_prove", "pczt_extract")), 3)
    row["wall_ms"] = round((t_wall_end - t_wall_start) * 1000, 3)
    row["other_ms"] = round(row["build_total_ms"] - row["prove_ms"], 3)
    row["wall_minus_build_ms"] = round(row["wall_ms"] - row["build_total_ms"], 3)
    row["prove_share"] = round(row["prove_ms"] / row["build_total_ms"], 6)

    # ---- RSS window (doc 29 SS5.3) ---------------------------------------
    picked, anomalous = sampler.window(row["run_id"], t_prove_start, t_prove_end)
    vals = [int(r[5]) for r in picked if r[5] is not None]
    win = {"run_id": row["run_id"],
           "t_prove_start": "%.6f" % t_prove_start,
           "t_prove_end": "%.6f" % t_prove_end,
           "window_seconds": round(t_prove_end - t_prove_start, 6),
           "rss_window_samples": len(vals),
           "phase_anomalies": anomalous}
    if vals:
        win.update({"rss_first_kb": vals[0], "rss_min_kb": min(vals),
                    "rss_max_kb": max(vals),
                    "rss_median_kb": round(statistics.median(vals), 1),
                    "delta_window_kb": max(vals) - min(vals)})
    else:
        win.update({"rss_first_kb": None, "rss_min_kb": None, "rss_max_kb": None,
                    "rss_median_kb": None, "delta_window_kb": None})
    win["low_confidence"] = len(vals) < 10
    row["rss_window_samples"] = len(vals)
    if win["low_confidence"]:
        fail_codes.append("rss_low_confidence_time_only")
    print("  build %8.1f ms (wall %8.1f, delta %6.1f) prove %8.1f ms share %.4f"
          % (row["build_total_ms"], row["wall_ms"], row["wall_minus_build_ms"],
             row["prove_ms"], row["prove_share"]))
    print("  rss window: %d samples, min=%s max=%s delta=%s KB, phase anomalies=%d"
          % (len(vals), win["rss_min_kb"], win["rss_max_kb"],
             win["delta_window_kb"], anomalous))

    # ---- correctness gates ONLY, not part of T_build (doc 29 SS10) --------
    t0 = time.monotonic()
    try:
        b("sendrawtransaction", [ext["hex"]])
        row["broadcast_accepted"] = True
    except Exception as e:
        row["broadcast_accepted"] = False
        row["broadcast_error"] = str(e)[:200]
        fail_codes.append("broadcast_rejected")
    T["broadcast"] = (time.monotonic() - t0) * 1000

    dh, ms = advance(a.mine_blocks)
    T["mine"] = ms
    row["mined_blocks_delta"] = dh

    t0 = time.monotonic()
    conf = None
    while time.monotonic() - t0 < 60:
        try:
            conf = b("getrawtransaction", [row["txid"], 1]).get("confirmations")
            if conf:
                break
        except RuntimeError:
            pass
        time.sleep(1)
    T["confirm"] = (time.monotonic() - t0) * 1000
    row["confirmations"] = conf
    row["confirmed"] = bool(conf and conf >= 1)
    if not row["confirmed"]:
        fail_codes.append("not_confirmed")
    wait_sync()

    # ---- shape / pid / timing gates ---------------------------------------
    if not (row["actions"] == 2 and row["orchard_actions"] in (0, None)):
        fail_codes.append("shape_changed")
    now = resolve("zallet-zaino")
    if not now or now["pid"] != wallet["pid"] or now["sha256"] != EXPECT_SHA["zallet-zaino"]:
        fail_codes.append("pid_changed")
    if not all(k in T for k in ("pczt_create", "pczt_sign", "pczt_prove", "pczt_extract")):
        fail_codes.append("timing_incomplete")

    row["failure_codes"] = ";".join(fail_codes)
    row["valid"] = (not fail_codes and a.label != "warmup")
    print("  gates: extract=%s broadcast=%s confirmed=%s actions=%s orchard=%s pid=%s -> valid=%s"
          % (row["extract_success"], row["broadcast_accepted"], row["confirmed"],
             row["actions"], row["orchard_actions"], row["wallet_pid"], row["valid"]))
    return row, win, T


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--label", default="final")
    ap.add_argument("--send-zec", type=float, default=3)
    ap.add_argument("--minconf", type=int, default=1)
    ap.add_argument("--policy", default="AllowRevealedAmounts")
    ap.add_argument("--fund-source", default="orchard")
    ap.add_argument("--mine-blocks", type=int, default=2)
    ap.add_argument("--outdir", required=True)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    wallet = resolve("zallet-zaino")
    zebra = resolve("zebrad")
    if not wallet or not zebra:
        print("[!] G0-C: exact PID resolution failed", file=sys.stderr)
        return 2
    for name, r in (("zallet-zaino", wallet), ("zebrad", zebra)):
        if r["sha256"] != EXPECT_SHA[name]:
            print("[!] G0-C: SHA mismatch %s" % name, file=sys.stderr)
            return 2

    p_before = pools()
    need_this = int((a.send_zec + 0.001) * 1e8) * a.rounds
    meta = {"label": a.label, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "version": VERSION, "scenario": SCENARIO,
            "args": vars(a), "kernel": platform.release(),
            "derived_from": "zcash-sapling-engineering@5e46712:experiments/e2e_testnet_tx/pczt_bench_pool.py",
            "prereg_doc": "docs/orchard/29_real_e2e_baseline.md",
            "wallet": wallet, "zebrad": zebra,
            "cpu_allowed": {"zallet-zaino": cpu_allowed(wallet["pid"]),
                            "zebrad": cpu_allowed(zebra["pid"])},
            "binaries_on_disk": {n: sha256(os.path.join(BIN, n)) for n in EXPECT_SHA},
            "blocks_before": height(),
            "balances_before": p_before,
            "need_this_batch_zat": need_this,
            "need_20_rounds_zat": NEED_20_ZAT,
            "g0_balance_gate_20_rounds": p_before.get("ironwood", 0) >= NEED_20_ZAT,
            "g0_shortfall_20_rounds_zec": round(max(0, NEED_20_ZAT - p_before.get("ironwood", 0)) / 1e8, 6)}
    print("[env] kernel=%s wallet_pid=%s zebra_pid=%s cpu=%s"
          % (meta["kernel"], wallet["pid"], zebra["pid"], meta["cpu_allowed"]))
    print("[pool] before=%s | need(this batch)=%.4f ZEC | need(20 rounds)=%.4f ZEC | G0-G=%s (short %.6f ZEC)"
          % ({k: round(v / 1e8, 6) for k, v in p_before.items()},
             need_this / 1e8, NEED_20_ZAT / 1e8,
             meta["g0_balance_gate_20_rounds"], meta["g0_shortfall_20_rounds_zec"]))
    if p_before.get("ironwood", 0) < need_this:
        print("[!] balance below this batch's need; abort (no parameter change allowed)",
              file=sys.stderr)
        return 2

    sampler = Sampler(os.path.join(a.outdir, "rss_timeseries.csv"), wallet, zebra)
    sampler.start()
    rows, wins, long_rows, invalid = [], [], [], []
    try:
        for i in range(1, a.rounds + 1):
            T = {}
            try:
                row, win, T = one_round(i, a, sampler, wallet, zebra, T)
                rows.append(row)
                wins.append(win)
                for st in STAGES:
                    if st in T:
                        long_rows.append([row["run_id"], st, "%.3f" % T[st]])
                if not row["valid"]:
                    invalid.append([row["run_id"], a.label, row["failure_codes"]])
            except Exception as e:
                print("  [error] round %d: %s" % (i, str(e)[:400]))
                invalid.append(["r%02d" % i, a.label, "rpc_error: " + str(e)[:300]])
                rows.append({"run_id": "r%02d" % i, "label": a.label,
                             "valid": False, "failure_codes": "rpc_error",
                             "error": str(e)[:400]})
    finally:
        sampler.stop_evt.set()
        sampler.join(timeout=8)
        sampler.phase = "outside"
        sampler.run_id = ""

    def dump(name, header, data):
        with open(os.path.join(a.outdir, name), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(data)

    dump("stage_timings.csv", ["run_id", "stage", "ms"], long_rows)
    dump("invalid_runs.csv", ["run_id", "label", "failure_codes"], invalid)
    cols = ["run_id", "version", "scenario", "label", "create_ms", "sign_ms", "prove_ms",
            "extract_ms", "build_total_ms", "wall_ms", "other_ms", "wall_minus_build_ms",
            "prove_share", "txid", "size_bytes", "size_b64", "actions", "orchard_actions",
            "sapling_spends", "signed", "ironwood_proven", "fee_zat", "policy",
            "extract_success", "broadcast_accepted", "confirmed", "confirmations",
            "mined_blocks_delta", "wallet_pid", "wallet_exe", "wallet_sha256",
            "rss_window_samples", "valid", "failure_codes"]
    with open(os.path.join(a.outdir, "rounds.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    wincols = ["run_id", "t_prove_start", "t_prove_end", "window_seconds",
               "rss_window_samples", "phase_anomalies", "rss_first_kb", "rss_min_kb",
               "rss_max_kb", "rss_median_kb", "delta_window_kb", "low_confidence"]
    with open(os.path.join(a.outdir, "rss_windows.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=wincols, extrasaction="ignore")
        w.writeheader()
        for r in wins:
            w.writerow(r)

    meta.update({"blocks_after": height(), "balances_after": pools(),
                 "n_rows": len(rows), "n_valid": sum(1 for r in rows if r.get("valid")),
                 "n_rss_samples_total": len(sampler.rows)})
    json.dump(meta, open(os.path.join(a.outdir, "meta.json"), "w"), indent=2,
              ensure_ascii=False)
    print("\n[out] %s  rows=%d valid=%d rss_samples=%d"
          % (a.outdir, len(rows), meta["n_valid"], len(sampler.rows)))
    return 0 if meta["n_valid"] == a.rounds or a.label == "warmup" else 2


if __name__ == "__main__":
    raise SystemExit(main())
