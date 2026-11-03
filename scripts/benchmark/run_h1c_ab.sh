#!/usr/bin/env bash
# Stage H1c interleaved A/B runner.
#
# Why interleaved: F2 measured 6.5% cross-batch drift on this machine, which is
# 3.6x larger than H1c's entire expected effect. Running all of A and then all
# of B would measure the machine's thermal state, not LTO. So each round runs A
# then B, and the round index is what the paired bootstrap resamples over.
#
# See docs/orchard/14_h1c_lto_prereg.md for the frozen protocol.
#
# Usage:
#   bash scripts/benchmark/run_h1c_ab.sh <A_BIN> <B_BIN> <OUT_DIR> [ROUNDS] [REPS]

set -euo pipefail

A_BIN=${1:?A binary}
B_BIN=${2:?B binary}
OUT=${3:?output dir}
ROUNDS=${4:-5}
REPS=${5:-5}

mkdir -p "$OUT"

run_one() {
    local bin=$1 side=$2 threads=$3 mask=$4 round=$5
    local tag="h1c_${threads}p_${side}_r${round}"
    local log="$OUT/$tag.log"
    RAYON_NUM_THREADS=$threads "$bin" \
        --mode v3 --version v3 --spends 1 --outputs 2 \
        --reps "$REPS" --seed 7 --mask "$mask" --tag "$tag" \
        > "$log" 2>&1
    echo "  $tag done"
}

for threads in 1 8; do
    if [ "$threads" = "1" ]; then mask=0x1; else mask=0xFF; fi
    echo "== ${threads}P (mask $mask) =="
    for round in $(seq 1 "$ROUNDS"); do
        echo " round $round"
        run_one "$A_BIN" a "$threads" "$mask" "$round"
        run_one "$B_BIN" b "$threads" "$mask" "$round"
    done
done

echo "all runs complete -> $OUT"
