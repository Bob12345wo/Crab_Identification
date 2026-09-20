import argparse
import json
import math
import subprocess
import sys
import time
import uuid
from pathlib import Path

from crab_thickness import add_depth_arguments, validate_depth_arguments
from upload_queue import attempt_upload, output_lock, read_json, retry_pending, rotate_outputs, write_json
from weight_trigger import WeightTrigger


LEG_FILE_KEYS = {
    "L-Claw": "LC",
    "L-Tleg": "LT",
    "L-Aleg": "LA",
    "L-Bleg": "LB",
    "L-Cleg": "LG",
    "R-Claw": "RC",
    "R-Tleg": "RT",
    "R-Aleg": "RA",
    "R-Bleg": "RB",
    "R-Cleg": "RG",
}


def run_command(cmd: list[str], timeout: float = 120.0) -> None:
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, timeout=timeout)


def capture_with_retries(cmd, args):
    for attempt in range(args.capture_retries + 1):
        try:
            run_command(cmd, timeout=args.measurement_timeout)
            return
        except (subprocess.SubprocessError, OSError):
            if attempt == args.capture_retries:
                raise
            print(f"warn: capture failed; restarting attempt {attempt + 2}/{args.capture_retries + 1}",
                  file=sys.stderr, flush=True)
            time.sleep(args.capture_retry_delay)


def compact_image_name(measurement: dict, stamp: str) -> str:
    weight = measurement.get("weight") or {}
    weight_value = weight.get("weight_g")
    weight_label = "NA" if weight_value is None else f"{float(weight_value):.1f}g"
    legs = {row.get("leg"): row for row in measurement.get("legs", [])}
    unit = "MM" if any(row.get("total_mm") is not None for row in legs.values()) else "PX"
    value_key = "total_mm" if unit == "MM" else "total_px"

    values = []
    quality_bits = []
    for leg_name, short_name in LEG_FILE_KEYS.items():
        row = legs.get(leg_name) or {}
        value = float(row.get(value_key) or 0.0)
        values.append(f"{short_name}{value:.1f}")
        quality_bits.append("1" if row.get("reliable") else "0")

    quality = "".join(quality_bits)
    return f"crab_{stamp}_W{weight_label}_{unit}_{'_'.join(values)}_Q{quality}.jpg"


def timestamp_name(prefix: str, suffix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}{suffix}"


def run_once(args: argparse.Namespace) -> Path:
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:12]
    measurement = out_dir / f"measurement_{stamp}.json"
    raw_image = out_dir / f"raw_{stamp}.jpg"
    upload_result = out_dir / f"onenet_upload_{stamp}.json"
    file_upload_result = out_dir / f"onenet_file_{stamp}.json"
    pending_result = out_dir / f"pending_{stamp}.json"

    measure_cmd = [
        sys.executable,
        "oak_crab_measure.py",
        "--blob",
        args.blob,
        "--input-size",
        str(args.input_size),
        "--frame-count",
        str(args.frame_count),
        "--capture-timeout",
        str(args.capture_timeout),
        "--camera-source-size",
        str(args.camera_source_size),
        "--roi-scale",
        str(args.roi_scale),
        "--roi-center-x",
        str(args.roi_center_x),
        "--roi-center-y",
        str(args.roi_center_y),
        "--min-reliable-legs",
        str(args.min_reliable_legs),
        "--min-score",
        str(args.min_score),
        "--max-bbox-area",
        str(args.max_bbox_area),
        "--conf",
        str(args.conf),
        "--max-segment-ratio",
        str(args.max_segment_ratio),
        "--claw-max-segment-ratio",
        str(args.claw_max_segment_ratio),
        "--json-out",
        str(measurement),
        "--raw-image",
        str(raw_image),
    ]
    if args.calibration:
        measure_cmd += ["--calibration", args.calibration]
    if args.depth:
        measure_cmd += ["--depth", "--depth-body-scale", str(args.depth_body_scale),
                        "--depth-max-height-mm", str(args.depth_max_height_mm),
                        "--depth-plane-tolerance-mm", str(args.depth_plane_tolerance_mm),
                        "--depth-out", str(out_dir / f"depth_{stamp}.npz")]
        if args.thickness_calibration:
            measure_cmd += ["--thickness-calibration", args.thickness_calibration]
    if args.weight_port:
        measure_cmd += [
            "--weight-port",
            args.weight_port,
            "--weight-baud",
            str(args.weight_baud),
            "--weight-samples",
            str(args.weight_samples),
            "--weight-sample-interval",
            str(args.weight_sample_interval),
            "--weight-zero-deadband",
            str(args.weight_zero_deadband),
            "--weight-max-spread",
            str(args.weight_max_spread),
            "--weight-min-samples",
            str(args.weight_min_samples),
        ]
    if args.debug:
        measure_cmd += ["--debug-image", str(out_dir / f"debug_{stamp}.jpg")]

    capture_with_retries(measure_cmd, args)

    measurement_data = read_json(measurement)
    compact_raw_image = out_dir / compact_image_name(measurement_data, stamp)
    raw_image.rename(compact_raw_image)
    raw_image = compact_raw_image
    measurement_data["image_name"] = raw_image.name
    write_json(measurement, measurement_data)
    print(f"Renamed raw image -> {raw_image}", flush=True)

    artifacts = [measurement, raw_image, upload_result, file_upload_result]
    if args.depth:
        artifacts.append(out_dir / f"depth_{stamp}.npz")
    if args.debug:
        artifacts.append(out_dir / f"debug_{stamp}.jpg")
    job = {
        "schema_version": 1,
        "timestamp": time.time(),
        "measurement_id": measurement_data["measurement_id"],
        "measurement": str(measurement),
        "image": str(raw_image),
        "measurement_ok": bool(measurement_data.get("measurement_ok")),
        "thickness": measurement_data.get("thickness"),
        "config": str(Path(args.config).resolve()),
        "file_upload_ok": False,
        "image_fid": "",
        "mqtt_upload_ok": False,
        "skip_file_upload": args.skip_file_upload,
        "file_upload_result": str(file_upload_result),
        "mqtt_upload_result": str(upload_result),
        "files": [str(path) for path in artifacts],
        "attempts": 0,
        "next_retry_at": 0,
    }
    # Commit the upload job before any network operation.
    write_json(pending_result, job)
    attempt_upload(pending_result, args, run_command)

    rotate_outputs(out_dir, args.keep)
    return measurement


def build_parser():
    parser = argparse.ArgumentParser(description="Run crab OAK measurement, weight read, and OneNET MQTT upload.")
    add_depth_arguments(parser)
    parser.add_argument(
        "--blob",
        default=(
            "oak_export/best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob"
            if Path("oak_export/best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob").is_file()
            else "best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob"
        ),
    )
    parser.add_argument("--input-size", type=int, default=640)
    parser.add_argument("--frame-count", type=int, default=5)
    parser.add_argument("--camera-source-size", type=int, default=1920)
    parser.add_argument("--roi-scale", type=float, default=0.42)
    parser.add_argument("--roi-center-x", type=float, default=0.5)
    parser.add_argument("--roi-center-y", type=float, default=0.5)
    parser.add_argument("--calibration", default=None, help="Pixel-to-mm plane calibration JSON")
    parser.add_argument("--min-reliable-legs", type=int, default=8)
    parser.add_argument("--conf", type=float, default=0.20)
    parser.add_argument("--min-score", type=float, default=0.25)
    parser.add_argument("--max-bbox-area", type=float, default=0.90)
    parser.add_argument("--max-segment-ratio", type=float, default=5.0)
    parser.add_argument("--claw-max-segment-ratio", type=float, default=8.0)
    parser.add_argument("--weight-port", default="/dev/ttyUSB0")
    parser.add_argument("--weight-baud", type=int, default=9600)
    parser.add_argument("--weight-samples", type=int, default=3)
    parser.add_argument("--weight-sample-interval", type=float, default=0.15)
    parser.add_argument("--weight-zero-deadband", type=float, default=5.0)
    parser.add_argument("--config", default="onenet_mqtt_config.json")
    parser.add_argument("--out-dir", default="runs")
    parser.add_argument("--keep", type=int, default=300, help="Keep N completed measurement groups; never delete pending jobs")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--debug", action="store_true", help="Also save annotated debug images")
    parser.add_argument("--skip-file-upload", action="store_true", help="Skip OneNET file management upload")
    parser.add_argument("--capture-timeout", type=float, default=30.0)
    parser.add_argument("--measurement-timeout", type=float, default=120.0)
    parser.add_argument("--capture-retries", type=int, default=1, help="Additional capture attempts, not upload attempts")
    parser.add_argument("--capture-retry-delay", type=float, default=2.0)
    parser.add_argument("--upload-timeout", type=float, default=45.0)
    parser.add_argument("--retry-base-delay", type=float, default=10.0)
    parser.add_argument("--retry-max-delay", type=float, default=300.0)
    parser.add_argument("--retry-limit", type=int, default=3, help="Maximum pending jobs attempted per loop")
    parser.add_argument("--retry-only", action="store_true", help="Retry pending uploads without opening camera or scale")
    parser.add_argument("--weight-max-spread", type=float, default=2.0)
    parser.add_argument("--weight-min-samples", type=int, default=2)
    parser.add_argument("--auto-trigger", action="store_true", help="Require empty scale, stable load, then removal before next capture")
    parser.add_argument("--trigger-load-g", type=float, default=20.0)
    parser.add_argument("--trigger-empty-g", type=float, default=5.0)
    parser.add_argument("--trigger-windows", type=int, default=2)
    parser.add_argument("--trigger-poll-interval", type=float, default=1.0)
    parser.add_argument("--weight-timeout", type=float, default=15.0)
    return parser


def validate_arguments(parser, args):
    validate_depth_arguments(parser, args)
    positive = ("capture_timeout", "measurement_timeout", "upload_timeout", "retry_base_delay",
                "retry_max_delay", "trigger_poll_interval", "weight_timeout")
    nonnegative = ("capture_retry_delay", "interval", "weight_max_spread", "weight_zero_deadband",
                   "weight_sample_interval", "trigger_empty_g")
    for name in positive + nonnegative:
        value = getattr(args, name)
        if not math.isfinite(value) or value < 0 or (name in positive and value == 0):
            parser.error(f"Invalid --{name.replace('_', '-')}")
    if args.capture_retries < 0 or args.retry_limit < 1 or args.trigger_windows < 1 or args.frame_count < 1:
        parser.error("Retries must be nonnegative; retry limit, trigger windows and frame count must be positive")
    if args.retry_max_delay < args.retry_base_delay:
        parser.error("--retry-max-delay must be >= --retry-base-delay")
    if args.weight_port and not 2 <= args.weight_min_samples <= args.weight_samples:
        parser.error("Require 2 <= --weight-min-samples <= --weight-samples")
    if not math.isfinite(args.trigger_load_g) or args.trigger_load_g <= args.trigger_empty_g:
        parser.error("--trigger-load-g must be finite and greater than --trigger-empty-g")
    if args.auto_trigger and (not args.weight_port or args.retry_only):
        parser.error("--auto-trigger requires --weight-port and cannot be combined with --retry-only")
    if args.auto_trigger and args.trigger_load_g <= args.weight_zero_deadband:
        parser.error("--trigger-load-g must be greater than --weight-zero-deadband")


def sample_trigger_weight(args):
    command = [sys.executable, "oak_crab_measure.py", "--weight-only", "--json-out", "-",
               "--weight-port", args.weight_port, "--weight-baud", str(args.weight_baud),
               "--weight-samples", str(args.weight_samples), "--weight-min-samples", str(args.weight_min_samples),
               "--weight-sample-interval", str(args.weight_sample_interval),
               "--weight-zero-deadband", str(args.weight_zero_deadband),
               "--weight-max-spread", str(args.weight_max_spread)]
    result = subprocess.run(command, check=True, timeout=args.weight_timeout, capture_output=True, text=True)
    return json.loads(result.stdout)["weight"]


def run_auto_trigger(args):
    trigger = WeightTrigger(args.trigger_load_g, args.trigger_empty_g, args.trigger_windows)
    print("Automatic capture: clear the scale first; waiting for a stable empty scale.", flush=True)
    while True:
        try:
            previous_state = trigger.state
            weight = sample_trigger_weight(args)
            fired = trigger.update(weight)
            if trigger.state != previous_state:
                print(f"Trigger state: {trigger.state}", flush=True)
            if fired:
                # The trigger is disarmed before capture, including failed attempts.
                try:
                    print(f"done: {run_once(args)}", flush=True)
                except Exception as exc:
                    print(f"error: capture failed; remove load before retrying: {exc}", file=sys.stderr, flush=True)
            elif weight.get("ok") and weight.get("weight_g") is not None and 0 <= weight["weight_g"] <= args.trigger_empty_g:
                retry_pending(args, run_command)
        except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
            trigger.update({"ok": False})
            print(f"warn: trigger weight unavailable: {exc}", file=sys.stderr, flush=True)
        time.sleep(args.trigger_poll_interval)


def run_pipeline(args):
    if args.auto_trigger:
        run_auto_trigger(args)
        return

    while True:
        try:
            retry_pending(args, run_command)
            if args.retry_only:
                rotate_outputs(Path(args.out_dir), args.keep)
            else:
                measurement = run_once(args)
                print(f"done: {measurement}", flush=True)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr, flush=True)
            if not args.loop:
                raise

        if not args.loop:
            break
        time.sleep(args.interval)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    validate_arguments(parser, args)
    with output_lock(args.out_dir):
        run_pipeline(args)


if __name__ == "__main__":
    main()
