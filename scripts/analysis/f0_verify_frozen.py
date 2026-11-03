#!/usr/bin/env python3
"""Verify the Stage F0 frozen fingerprints recorded in manifest.json.

The manifest pins two sha256 values over files that live *inside this repo*
(the frozen Cargo.lock copy and the vendored halo2_proofs sources). Both are
recorded over LF-normalised content, so a fresh clone -- on any platform --
can re-derive them exactly. See .gitattributes: those paths are pinned to
`text eol=lf` for that reason.

Usage:
    python scripts/analysis/f0_verify_frozen.py [--repo-root PATH]

Exit code 0 = every check passed, 1 = at least one mismatch.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys

MANIFEST = "results/transaction_e2e/f0_freeze/manifest.json"


def sha256_file(path: str) -> str:
    """sha256 over LF-normalised bytes (CRLF -> LF)."""
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read().replace(b"\r\n", b"\n")).hexdigest()


def sha256_concat(paths: list[str]) -> str:
    """sha256 over the concatenation of LF-normalised files, in the given order."""
    h = hashlib.sha256()
    for p in paths:
        with open(p, "rb") as fh:
            h.update(fh.read().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def check(label: str, expected: str, actual: str | None) -> bool:
    if actual is None:
        print(f"  SKIP  {label}  (source not present)")
        return True
    ok = expected == actual
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        expected {expected}")
        print(f"        actual   {actual}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--repo-root",
        default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )
    args = ap.parse_args()
    root = args.repo_root

    with open(os.path.join(root, MANIFEST), encoding="utf-8") as fh:
        m = json.load(fh)

    deps = m["dependencies"]
    ok = True

    print(f"manifest: {MANIFEST}")
    print(f"frozen at: {m.get('frozen_at_local')}")

    print("[1] cargo_lock.sha256")
    lock_copy = os.path.join(root, "results/transaction_e2e/f0_freeze/Cargo.lock.frozen")
    ok &= check(
        "Cargo.lock.frozen",
        deps["cargo_lock"]["sha256"],
        sha256_file(lock_copy) if os.path.exists(lock_copy) else None,
    )

    print("[2] halo2_proofs.src_sha256  (vendored, 57 .rs files, sorted by path)")
    vendor_src = os.path.join(root, "experiments/transaction_e2e/vendor/halo2_proofs-0.4.0/src")
    files = sorted(glob.glob(os.path.join(vendor_src, "**", "*.rs"), recursive=True))
    ok &= check(
        f"{len(files)} files under vendor/.../src",
        deps["halo2_proofs"]["src_sha256"],
        sha256_concat(files) if files else None,
    )

    print("[3] pasta_curves.src_sha256  (cargo registry, outside the repo)")
    reg = os.path.expanduser(
        f"~/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/pasta_curves-{deps['pasta_curves']['version']}/src"
    )
    if os.path.isdir(reg):
        pfiles = sorted(glob.glob(os.path.join(reg, "**", "*.rs"), recursive=True))
        ok &= check(
            f"{len(pfiles)} files under registry pasta_curves/src",
            deps["pasta_curves"]["src_sha256"],
            sha256_concat(pfiles),
        )
    else:
        print(f"  SKIP  pasta_curves sources not vendored at {reg}")

    print()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
