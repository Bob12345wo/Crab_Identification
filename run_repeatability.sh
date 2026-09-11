#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
source .venv/bin/activate

COUNT="${1:-20}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="runs/repeatability_${STAMP}"
mkdir -p "$OUT_DIR"

echo "Repeatability test: count=$COUNT output=$OUT_DIR"
echo "Keep camera, target, lighting, ROI, and scale unchanged."

for i in $(seq -w 1 "$COUNT"); do
  echo "[$i/$COUNT] measuring"
  python oak_crab_measure.py \
    --blob best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob \
    --input-size 640 \
    --frame-count 5 \
    --camera-source-size 1920 \
    --roi-scale 0.42 \
    --roi-center-x 0.50 \
    --roi-center-y 0.50 \
    --conf 0.20 \
    --min-score 0.25 \
    --max-bbox-area 0.90 \
    --min-reliable-legs 8 \
    --max-segment-ratio 5.0 \
    --claw-max-segment-ratio 8.0 \
    --weight-port /dev/ttyUSB0 \
    --weight-baud 9600 \
    --weight-samples 3 \
    --weight-sample-interval 0.15 \
    --weight-zero-deadband 5.0 \
    --json-out "$OUT_DIR/measurement_${i}.json" \
    --raw-image "$OUT_DIR/raw_${i}.jpg" \
    --debug-image "$OUT_DIR/debug_${i}.jpg"
  sleep 0.5
done

python analyze_repeatability.py --input-dir "$OUT_DIR" --min-samples 5
echo "done: $OUT_DIR"
