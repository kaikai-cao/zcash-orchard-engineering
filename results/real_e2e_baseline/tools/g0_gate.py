#!/usr/bin/env python3
"""doc 29 §11 G0 gate checker (Phase 1). Read-only except taskset affinity.

Derived from the frozen pre-registration; no RPC call shape is changed. Every
item is evaluated (no early exit) so the gate report is complete even on FAIL.
"""
import argparse
import base64
import hashlib
import http.client
import json
import os
import platform
import subprocess
import sys
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
NEED_ZAT = int((3.0 + 0.001) * 1e8) * 20          # 6_002_000_000 = 60.02 ZEC
ZIP212_MIN = 32257
# doc 29 SS11.2 writes "taskset -pc 0x1 <pid>" (the hex form taskset *prints*).
# util-linux taskset only accepts a decimal CPU list as INPUT, so the literal
# frozen command fails with "failed to parse CPU list: 0x1". "0" is the same
# target (CPU 0); read-back Cpus_allowed_list == "0" is the frozen condition.
# The failed literal attempt is preserved in g0_gate_g0_run2.json.
CPU_MASK = "0"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _post(host, port, payload, auth=None, timeout=30):
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


def resolve(name):
    """pgrep -x + /proc/PID/exe + SHA256. No pgrep -f fallback (doc 28 §5.2)."""
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


def set_affinity(pid):
    r = subprocess.run(["taskset", "-pc", CPU_MASK, str(pid)],
                       capture_output=True, text=True)
    return {"rc": r.returncode, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="g0")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--no-affinity", action="store_true",
                    help="do not change affinity, only read back")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    items = []
    raw = {}

    def put(i, name, ok, value, note=""):
        items.append({"id": i, "name": name, "status": "PASS" if ok else "FAIL",
                      "value": value, "note": note})

    # G0-A kernel / host (record only; drift is expected and documented)
    kernel = platform.release()
    raw["kernel"] = kernel
    put("G0-A", "WSL kernel recorded (record-only)", True, kernel,
        "fingerprint 2026-10-29 was 6.18.54.1; drift is expected, doc 29 §2.2")

    # G0-B three binaries on disk
    disk = {}
    for name, want in EXPECT_SHA.items():
        p = os.path.join(BIN, name)
        try:
            got = sha256(p)
            disk[name] = {"path": p, "sha256": got,
                          "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                                 time.localtime(os.path.getmtime(p))),
                          "bytes": os.path.getsize(p)}
        except OSError as e:
            disk[name] = {"error": str(e)}
    raw["binaries_on_disk"] = disk
    bin_ok = all(disk.get(n, {}).get("sha256") == w for n, w in EXPECT_SHA.items())
    put("G0-B", "3 binary SHA256 == frozen fingerprint", bin_ok,
        {n: disk.get(n, {}).get("sha256") for n in EXPECT_SHA})

    # G0-C/D running PIDs, /proc/PID/exe, SHA256
    procs = {}
    for name in ("zebrad", "zallet-zaino"):
        procs[name] = resolve(name)
    raw["processes"] = procs
    pid_ok = True
    for name in ("zebrad", "zallet-zaino"):
        r = procs.get(name)
        if not r or r["sha256"] != EXPECT_SHA[name]:
            pid_ok = False
    put("G0-C", "zebrad + zallet-zaino resolved via /proc/PID/exe", pid_ok, procs,
        "pgrep -x only; pgrep -f fallback forbidden")

    # G0-D CPU affinity: set once, then read back
    aff = {}
    for name in ("zebrad", "zallet-zaino"):
        r = procs.get(name)
        if not r:
            aff[name] = {"error": "no pid"}
            continue
        act = None if a.no_affinity else set_affinity(r["pid"])
        aff[name] = {"set": act, "Cpus_allowed_list": cpu_allowed(r["pid"])}
    raw["affinity"] = aff
    aff_ok = all(aff.get(n, {}).get("Cpus_allowed_list") == "0"
                 for n in ("zebrad", "zallet-zaino"))
    put("G0-D", "CPU affinity read-back == 0x1 (CPU 0) for both PIDs", aff_ok, aff)

    # G0-E RPC reachability + chain height + ZIP 212
    try:
        blocks = b("getblockchaininfo", [])["blocks"]
        ws = z("getwalletstatus", [])
        wt = (ws.get("wallet_tip") or {}).get("height")
        nt = (ws.get("node_tip") or {}).get("height")
        target = max(nt or 0, blocks)
        rpc = {"zebra_blocks": blocks, "wallet_tip": wt, "node_tip": nt,
               "sync_target": target, "synced": bool(wt and wt >= target)}
        rpc_ok = bool(blocks >= ZIP212_MIN and rpc["synced"])
    except Exception as e:
        rpc = {"error": str(e)[:400]}
        rpc_ok = False
    raw["rpc"] = rpc
    put("G0-E", "RPC up + height >= 32257 + wallet synced", rpc_ok, rpc)

    # G0-F account
    try:
        accts = z("z_listaccounts", [])
        raw["accounts_raw"] = accts
        hit = [x for x in accts if (x.get("account_uuid") or x.get("uuid")) == ACCOUNT]
        acct_ok = bool(hit)
        acct_val = hit or accts
    except Exception as e:
        acct_ok, acct_val = False, {"error": str(e)[:400]}
    raw["accounts"] = acct_val
    put("G0-F", "bench account UUID present", acct_ok, acct_val)

    # G0-G balance (the frozen 20-round requirement)
    try:
        pools = z("z_getbalanceforaccount", [ACCOUNT]).get("pools", {})
        vals = {k: int(v.get("valueZat", 0)) for k, v in pools.items()}
        iron = vals.get("ironwood", 0)
        bal_ok = iron >= NEED_ZAT
        bal_val = {"zat": vals, "zec": {k: round(v / 1e8, 4) for k, v in vals.items()},
                   "need_zat": NEED_ZAT, "need_zec": NEED_ZAT / 1e8,
                   "shortfall_zec": round(max(0, NEED_ZAT - iron) / 1e8, 4)}
    except Exception as e:
        bal_ok, bal_val = False, {"error": str(e)[:400]}
    raw["balance"] = bal_val
    put("G0-G", "ironwood balance >= 60.02 ZEC (20 rounds x 3 ZEC)", bal_ok, bal_val)

    # G0-H shape probe: create + inspect, then discard (no sign/prove/broadcast)
    try:
        created = z("pczt_create", [ACCOUNT, [{"address": SAP_UA, "amount": 3.0}],
                                    1, "AllowRevealedAmounts", "orchard"])
        insp = z("pczt_inspect", [created["pczt"]])
        ia = (insp.get("ironwood") or {}).get("actions")
        oa = (insp.get("orchard") or {}).get("actions", 0)
        shape = {"ironwood_actions": ia, "orchard_actions": oa,
                 "sapling_spends": (insp.get("sapling") or {}).get("spends"),
                 "fee_zat": insp.get("fee_zat"),
                 "privacy_policy": created.get("privacy_policy"),
                 "pczt_b64_len": len(created["pczt"])}
        shape_ok = (ia == 2 and (oa in (0, None)))
        shape["note"] = "PCZT discarded: unsigned, unproven, not broadcast, no chain effect"
    except Exception as e:
        shape_ok, shape = False, {"error": str(e)[:500]}
    raw["shape_probe"] = shape
    put("G0-H", "shape ironwood.actions=2 / orchard.actions=0", shape_ok, shape)

    overall = all(i["status"] == "PASS" for i in items)
    result = {"label": a.label, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "overall": "PASS" if overall else "FAIL",
              "n_pass": sum(i["status"] == "PASS" for i in items),
              "n_items": len(items), "items": items, "raw": raw}
    stem = os.path.join(a.outdir, "g0_gate_%s" % a.label)
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    with open(stem + ".txt", "w", encoding="utf-8") as f:
        f.write("G0 GATE [%s]  %s  overall=%s (%d/%d PASS)\n\n"
                % (a.label, result["timestamp"], result["overall"],
                   result["n_pass"], result["n_items"]))
        for i in items:
            f.write("%-7s %-4s %-58s %s\n" %
                    (i["id"], i["status"], i["name"],
                     json.dumps(i["value"], ensure_ascii=False)[:900]))
            if i["note"]:
                f.write("        note: %s\n" % i["note"])
    print(json.dumps({"label": a.label, "overall": result["overall"],
                      "n_pass": result["n_pass"], "n_items": result["n_items"],
                      "fails": [i["id"] + " " + i["name"] for i in items
                                if i["status"] == "FAIL"]}, ensure_ascii=False))
    return 0 if overall else 2


if __name__ == "__main__":
    raise SystemExit(main())
