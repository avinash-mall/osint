#!/usr/bin/env bash
# Convenience wrapper: pick a sample chip, run benchmark, save under bench/.
set -euo pipefail

URL="${INFERENCE_URL:-http://localhost:8001}"
CHIP="${BENCH_CHIP:-tests/fixtures/sample_chip.png}"
ITERS="${BENCH_ITERS:-100}"
TAG="${BENCH_TAG:-$(date +%Y%m%d_%H%M%S)}"
OUT="bench/${TAG}.json"

mkdir -p bench
python inference-sam3/benchmark_detect.py \
    --url "$URL" --chip "$CHIP" --iters "$ITERS" \
    --out "$OUT"
echo "Wrote $OUT"
