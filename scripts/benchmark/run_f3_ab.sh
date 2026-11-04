#!/usr/bin/env bash
# Stage F3 (H6) interleaved A/B runner.
#
# Ordering is ABBA per round -- A1 B1 B2 A2 -- not H1c's A-then-B. Within a
# block the A runs sit at the two ends and the B runs in the middle, so a
# linear drift (warm-up, frequency sag) biases the two sides in opposite
# directions and cancels on average. H1c's 8P arm showed +1.14% that looked
# like exactly this kind of ordering artefact.
#
# Each block yields two pairs: (A1,B1) and (B2,A2). The report resamples over
# pairs, not over individual samples, because a pair shares a thermal state.
#
# Datasets (see docs/orchard/15_h6_neg_prereg.md section 4):
#   D1  main timing    no env at all
#   D2  stage timing   ORCHARD_E2_TIMING=1
#   D3  mechanism      ORCHARD_F2=1 ORCHARD_F2_BENCH=0, F3_MODE=f2
#
# Usage:
#   bash scripts/benchmark/run_f3_ab.sh <A_BIN> <B_BIN> <OUT_DIR> [ROUNDS] [REPS] [ENV=VAL ...]
#
# Extra arguments are passed to both sides verbatim, so the only difference
# between A and B is the binary.

set -euo pipefail

A_BIN=${1:?A binary}
B_BIN=${2:?B binary}
OUT=${3:?output dir}
ROUNDS=${4:-5}
REPS=${5:-5}
shift 5 || true
EXTRA=("$@")

MODE=${F3_MODE:-v3}
TAG=${F3_TAG:-f3}

mkdir -p "$OUT/baseline" "$OUT/treatment"

run_one() {
    local bin=$1 side=$2 threads=$3 mask=$4 round=$5 pos=$6
    local dir log
    if [ "$side" = "a" ]; then dir="baseline"; else dir="treatment"; fi
    log="$OUT/$dir/${threads}p_${side}_r${round}_${pos}.log"
    RAYON_NUM_THREADS=$threads env "${EXTRA[@]}" "$bin" \
        --mode "$MODE" --version v3 --spends 1 --outputs 2 \
        --reps "$REPS" --seed 7 --mask "$mask" --tag "${TAG}_${threads}p_${side}_r${round}${pos}" \
        > "$log" 2>&1
    echo "  ${threads}p ${side} r${round}.${pos}"
}

for threads in 1 8; do
    if [ "$threads" = "1" ]; then mask=0x1; else mask=0xFF; fi
    echo "== ${threads}P (mask $mask) =="
    for round in $(seq 1 "$ROUNDS"); do
        echo " round $round  (A B B A)"
        run_one "$A_BIN" a "$threads" "$mask" "$round" 1
        run_one "$B_BIN" b "$threads" "$mask" "$round" 1
        run_one "$B_BIN" b "$threads" "$mask" "$round" 2
        run_one "$A_BIN" a "$threads" "$mask" "$round" 2
    done
done

echo "all runs complete -> $OUT"
