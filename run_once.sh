#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
source .venv/bin/activate

CALIBRATION_ARGS=()
if [[ -f calibration_plane.json ]]; then
  CALIBRATION_ARGS=(--calibration calibration_plane.json)
fi

python crab_pipeline.py \
  --blob best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob \
  --input-size 640 \
  --frame-count 5 \
  --camera-source-size 1920 \
  --roi-scale 0.42 \
  --roi-center-x 0.50 \
  --roi-center-y 0.50 \
  "${CALIBRATION_ARGS[@]}" \
  --min-reliable-legs 8 \
  --conf 0.20 \
  --min-score 0.25 \
  --max-bbox-area 0.90 \
  --max-segment-ratio 5.0 \
  --claw-max-segment-ratio 8.0 \
  --weight-port /dev/ttyUSB0 \
  --weight-baud 9600 \
  --weight-samples 3 \
  --weight-sample-interval 0.15 \
  --weight-zero-deadband 5.0 \
  --config onenet_mqtt_config.json \
  --out-dir runs \
  --debug \
  --keep 300 \
  "$@"
