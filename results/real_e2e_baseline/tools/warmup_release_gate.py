#!/usr/bin/env python3
"""doc 29 §14.3 step 6: NEW post-warm-up release gate.

Runs AFTER the single warm-up and BEFORE the 20 counted rounds. It is the final
balance gate the user asked for, plus a re-check that the warm-up really
validated the measurement chain. Read-only: no RPC that changes chain state,
no transaction, no parameter change.

PASS requires all of:
  W-1  warm-up produced exactly 1 row, label=warmup, valid=false (not counted)
  W-2  extract_success / broadcast_accepted / confirmed all true
  W-3  shape ironwood.actions=2 / orchard.actions=0
  W-4  rss_windows.csv: >= 10 samples, phase_anomalies == 0, low_confidence false
  W-5  |wall - build| / wall <= 5% (T_wall cross-check, §3.4)
  W-6  wallet PID / exe / SHA256 identical to the G0 report
  W-7  ACTUAL ironwood balance >= 60.02 ZEC (= 20 valid rounds, §14.3 step 6)
  W-8  no failure_codes on the warm-up row

Only the warm-up row and the live balance are read. No warm-up timing or RSS
number becomes a baseline statistic (§14.4).
"""
import argparse
import base64
import csv
import hashlib
import http.client
import json
import os
import time

ZA = ("127.0.0.1", 40232)
COOKIE = os.path.expanduser("~/z3-regtest/data/zallet/.cookie")
ACCOUNT = "5fd7056a-3e6e-455f-b75d-d80b0556fea9"
NEED_20_ZAT = int((3.0 + 0.001) * 1e8) * 20       # 6_002_000_000 = 60.02 ZEC
EXPECT_SHA = "e602e4a3d4e919c155b8f05dae29f0b9311631f167eaf25455166edf268208a7"


def _post(payload):
    conn = http.client.HTTPConnection(ZA[0], ZA[1], timeout=60)
    hdr = {"Content-Type": "application/json",
           "Authorization": "Basic " + base64.b64encode(
               open(COOKIE).read().strip().encode()).decode()}
    conn.request("POST", "/", json.dumps(payload), hdr)
    raw = conn.getresponse().read()
    conn.close()
    return json.loads(raw)


def pools_zat():
    d = _post({"jsonrpc": "2.0", "method": "z_getbalanceforaccount",
               "params": [ACCOUNT], "id": 1})
    if d.get("error"):
        raise RuntimeError(str(d["error"]))
    return {k: int(v.get("valueZat", 0))
            for k, v in d["result"].get("pools", {}).items()}


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def truthy(v):
    return str(v).strip().lower() in ("true", "1", "yes")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--warmup-dir", required=True)
    ap.add_argument("--g0-json", required=True)
    ap.add_argument("--outdir", required=True)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    items = []
    raw = {}

    def put(i, name, ok, value, note=""):
        items.append({"id": i, "name": name, "status": "PASS" if ok else "FAIL",
                      "value": value, "note": note})

    rows = read_csv(os.path.join(a.warmup_dir, "rounds.csv"))
    wins = read_csv(os.path.join(a.warmup_dir, "rss_windows.csv"))
    g0 = json.load(open(a.g0_json, encoding="utf-8"))
    raw["n_round_rows"] = len(rows)
    raw["n_window_rows"] = len(wins)

    put("W-1", "exactly 1 warm-up row, label=warmup, valid=false (not counted)",
        len(rows) == 1 and rows[0].get("label") == "warmup"
        and not truthy(rows[0].get("valid")),
        [{"run_id": r.get("run_id"), "label": r.get("label"),
          "valid": r.get("valid")} for r in rows])
    if not rows:
        put("W-2", "three correctness gates", False, "no warm-up row")
        put("W-3", "shape", False, "no warm-up row")
        put("W-5", "T_wall cross-check", False, "no warm-up row")
        put("W-8", "no failure codes", False, "no warm-up row")
    else:
        r = rows[0]
        gates = {"extract_success": r.get("extract_success"),
                 "broadcast_accepted": r.get("broadcast_accepted"),
                 "confirmed": r.get("confirmed"),
                 "confirmations": r.get("confirmations")}
        put("W-2", "extract / broadcast / confirmed all true",
            all(truthy(gates[k]) for k in
                ("extract_success", "broadcast_accepted", "confirmed")), gates)
        shape = {"actions": r.get("actions"),
                 "orchard_actions": r.get("orchard_actions")}
        put("W-3", "shape ironwood.actions=2 / orchard.actions=0",
            str(shape["actions"]) == "2" and str(shape["orchard_actions"]) in ("0", "", "None"),
            shape)
        try:
            wall = float(r["wall_ms"]); delta = abs(float(r["wall_minus_build_ms"]))
            ratio = delta / wall if wall else 1.0
        except (KeyError, ValueError):
            ratio = 1.0
        put("W-5", "|T_wall - T_build| / T_wall <= 5%", ratio <= 0.05,
            {"wall_ms": r.get("wall_ms"),
             "wall_minus_build_ms": r.get("wall_minus_build_ms"),
             "ratio": round(ratio, 6)})
        put("W-8", "no failure codes on warm-up row",
            not (r.get("failure_codes") or "").strip(),
            {"failure_codes": r.get("failure_codes")})

    if wins:
        w = wins[0]
        n = int(w.get("rss_window_samples") or 0)
        anom = int(w.get("phase_anomalies") or 0)
        lc = truthy(w.get("low_confidence"))
        put("W-4", "prove window RSS >= 10 samples, 0 phase anomalies, confident",
            n >= 10 and anom == 0 and not lc,
            {"rss_window_samples": n, "phase_anomalies": anom,
             "low_confidence": w.get("low_confidence"),
             "window_seconds": w.get("window_seconds")})
    else:
        put("W-4", "prove window RSS >= 10 samples, 0 phase anomalies, confident",
            False, "no rss_windows.csv row")

    # W-6: PID / exe / SHA256 vs the PASSed G0 report
    g0_procs = (g0.get("raw") or {}).get("processes") or {}
    ref = g0_procs.get("zallet-zaino") or {}
    if rows:
        r = rows[0]
        pid_ok = (str(r.get("wallet_pid")) == str(ref.get("pid"))
                  and r.get("wallet_exe") == ref.get("exe")
                  and r.get("wallet_sha256") == EXPECT_SHA)
        put("W-6", "wallet PID / exe / SHA256 identical to G0", pid_ok,
            {"warmup": {"pid": r.get("wallet_pid"), "exe": r.get("wallet_exe"),
                        "sha256": r.get("wallet_sha256")},
             "g0": {"pid": ref.get("pid"), "exe": ref.get("exe"),
                    "sha256": ref.get("sha256")}})
    else:
        put("W-6", "wallet PID / exe / SHA256 identical to G0", False, "no warm-up row")

    # W-7: the NEW balance gate - ACTUAL balance, never an assumed credit
    try:
        vals = pools_zat()
        iron = vals.get("ironwood", 0)
        bal = {"zat": vals, "zec": {k: round(v / 1e8, 5) for k, v in vals.items()},
               "need_zat": NEED_20_ZAT, "need_zec": NEED_20_ZAT / 1e8,
               "margin_zec": round((iron - NEED_20_ZAT) / 1e8, 5),
               "shortfall_zec": round(max(0, NEED_20_ZAT - iron) / 1e8, 5)}
        raw["balance"] = bal
        put("W-7", "ACTUAL post-warm-up ironwood >= 60.02 ZEC (20 valid rounds)",
            iron >= NEED_20_ZAT, bal,
            "§14.3 step 6 release gate; independent of G0-G 63.021 ZEC")
    except Exception as e:
        put("W-7", "ACTUAL post-warm-up ironwood >= 60.02 ZEC (20 valid rounds)",
            False, {"error": str(e)[:400]})

    overall = all(i["status"] == "PASS" for i in items)
    result = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "overall": "PASS" if overall else "FAIL",
              "n_pass": sum(i["status"] == "PASS" for i in items),
              "n_items": len(items), "items": items, "raw": raw,
              "gate": "docs/orchard/29_real_e2e_baseline.md §14.3 step 6",
              "note": "PASS releases the 20 counted rounds; FAIL stops the batch"}
    stem = os.path.join(a.outdir, "warmup_release_gate")
    json.dump(result, open(stem + ".json", "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    with open(stem + ".txt", "w", encoding="utf-8") as f:
        f.write("WARM-UP RELEASE GATE  %s  overall=%s (%d/%d PASS)\n\n"
                % (result["timestamp"], result["overall"],
                   result["n_pass"], result["n_items"]))
        for i in items:
            f.write("%-5s %-4s %-64s %s\n" %
                    (i["id"], i["status"], i["name"],
                     json.dumps(i["value"], ensure_ascii=False)[:900]))
            if i["note"]:
                f.write("      note: %s\n" % i["note"])
    print(json.dumps({"overall": result["overall"], "n_pass": result["n_pass"],
                      "n_items": result["n_items"],
                      "fails": [i["id"] for i in items if i["status"] == "FAIL"]}))
    return 0 if overall else 2


if __name__ == "__main__":
    raise SystemExit(main())
