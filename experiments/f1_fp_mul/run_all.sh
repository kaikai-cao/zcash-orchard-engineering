#!/usr/bin/env bash
# Stage F1 -- run all three configurations, 5 rounds each, serially.
# Serial by design: no two benchmarks may share the machine (same discipline as
# E3's 1P/8P rule). Every round is pinned to logical CPU 0 (a P-core).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../../results/transaction_e2e/f1_fp_mul"
mkdir -p "$OUT"

ROUNDS=${ROUNDS:-5}

run_cfg () {
  local name="$1" bin="$2"
  echo "=== $name ($ROUNDS rounds) ==="
  : > "$OUT/raw_${name}.txt"
  "$bin" info --mask 0x1              >> "$OUT/raw_${name}.txt" 2>&1
  "$bin" correctness --vectors "$HERE/vectors.csv" --mask 0x1 >> "$OUT/raw_${name}.txt" 2>&1
  for i in $(seq 1 "$ROUNDS"); do
    echo "--- round $i ---"            >> "$OUT/raw_${name}.txt"
    "$bin" bench --mask 0x1           >> "$OUT/raw_${name}.txt" 2>&1
  done
  grep -E "^summary" "$OUT/raw_${name}.txt" | tail -n "$ROUNDS" | sed "s/^/${name},/" >> "$OUT/summary_rows.csv"
}

: > "$OUT/summary_rows.csv"

# 1) baseline: pasta_curves 0.6.0, exactly what Orchard locks
run_cfg "fp060_default" "$HERE/target/release/f1-fp-mul.exe"

# 2) compile-flag probe: same source, BMI2+ADX enabled
run_cfg "fp060_bmi2adx" "$HERE/target/adx/release/f1-fp-mul.exe"

# 3) reference ceiling: pasta_curves 0.6.1 with the hand-written asm backend
run_cfg "fp061_asm" "$HERE/../f1_fp_mul_asmref/target/release/f1-fp-mul-asmref.exe"

# rebuild the tidy CSV from the three raw files
python "$HERE/../../scripts/analysis/f1_summarize.py" --out "$OUT" \
  --asm "fp060_default=$HERE/target/release/deps" \
  --asm "fp060_bmi2adx=$HERE/target/adx/release/deps" \
  --asm "fp061_asm=$HERE/../f1_fp_mul_asmref/target/release/deps"
echo "done -> $OUT"
