import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


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


def run_command(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def compact_image_name(measurement: dict, stamp: str) -> str:
    weight = measurement.get("weight") or {}
    weight_g = float(weight.get("weight_g") or 0.0)
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
    return f"crab_{stamp}_W{weight_g:.1f}g_{unit}_{'_'.join(values)}_Q{quality}.jpg"


def timestamp_name(prefix: str, suffix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}{suffix}"


def rotate_outputs(out_dir: Path, keep: int) -> None:
    if keep <= 0:
        return
    files = sorted(
        [p for p in out_dir.iterdir() if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in files[keep:]:
        try:
            path.unlink()
        except OSError as exc:
            print(f"warn: failed to remove old file {path}: {exc}", file=sys.stderr)


def run_once(args: argparse.Namespace) -> Path:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    measurement = out_dir / f"measurement_{stamp}.json"
    raw_image = out_dir / f"raw_{stamp}.jpg"
    upload_result = out_dir / f"onenet_upload_{stamp}.json"
    file_upload_result = out_dir / f"onenet_file_{stamp}.json"
    pipeline_result = out_dir / f"pipeline_{stamp}.json"
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
        ]
    if args.debug:
        measure_cmd += ["--debug-image", str(out_dir / f"debug_{stamp}.jpg")]

    run_command(measure_cmd)

    measurement_data = read_json(measurement)
    compact_raw_image = out_dir / compact_image_name(measurement_data, stamp)
    raw_image.rename(compact_raw_image)
    raw_image = compact_raw_image
    measurement_data["image_name"] = raw_image.name
    write_json(measurement, measurement_data)
    print(f"Renamed raw image -> {raw_image}", flush=True)

    image_fid = ""
    file_result = {"ok": False, "skipped": bool(args.skip_file_upload)}
    if not args.skip_file_upload:
        file_upload_cmd = [
            sys.executable,
            "onenet_file_upload.py",
            "--config",
            args.config,
            "--image",
            str(raw_image),
            "--result-out",
            str(file_upload_result),
        ]
        try:
            run_command(file_upload_cmd)
        except subprocess.CalledProcessError as exc:
            print(f"warn: file upload failed with exit code {exc.returncode}; preserving local data", file=sys.stderr)
        if file_upload_result.exists():
            file_result = read_json(file_upload_result)
        image_fid = str(file_result.get("fid") or "")
        measurement_data = read_json(measurement)
        measurement_data["image_upload"] = {
            "ok": bool(file_result.get("ok")),
            "fid": image_fid,
            "image_name": raw_image.name,
            "result_file": str(file_upload_result),
            "response": file_result.get("response"),
        }
        write_json(measurement, measurement_data)

    upload_cmd = [
        sys.executable,
        "onenet_mqtt_upload.py",
        "--config",
        args.config,
        "--measurement",
        str(measurement),
        "--image",
        str(raw_image),
        "--image-fid",
        image_fid,
        "--result-out",
        str(upload_result),
    ]
    mqtt_error = None
    try:
        run_command(upload_cmd)
    except subprocess.CalledProcessError as exc:
        mqtt_error = f"exit_code:{exc.returncode}"
        print(f"warn: MQTT upload failed with exit code {exc.returncode}; preserving local data", file=sys.stderr)

    mqtt_result = read_json(upload_result) if upload_result.exists() else {"ok": False, "error": mqtt_error}
    summary = {
        "timestamp": time.time(),
        "measurement": str(measurement),
        "image": str(raw_image),
        "measurement_ok": bool(read_json(measurement).get("measurement_ok")),
        "file_upload_ok": bool(file_result.get("ok") or file_result.get("skipped")),
        "image_fid": image_fid,
        "mqtt_upload_ok": bool(mqtt_result.get("ok")),
        "file_upload_result": str(file_upload_result) if file_upload_result.exists() else None,
        "mqtt_upload_result": str(upload_result) if upload_result.exists() else None,
    }
    write_json(pipeline_result, summary)
    if not summary["file_upload_ok"] or not summary["mqtt_upload_ok"]:
        write_json(pending_result, summary)
        print(f"pending upload preserved -> {pending_result}", file=sys.stderr, flush=True)

    try:
        replies = mqtt_result.get("replies", [])
        print(f"upload replies: {replies}", flush=True)
    except Exception as exc:
        print(f"warn: failed to read upload result: {exc}", file=sys.stderr)

    rotate_outputs(out_dir, args.keep)
    return measurement


def main() -> None:
    parser = argparse.ArgumentParser(description="Run crab OAK measurement, weight read, and OneNET MQTT upload.")
    parser.add_argument("--blob", default="crab_pose_best_openvino_2022.1_4shave.blob")
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
    parser.add_argument("--keep", type=int, default=300, help="Keep newest N output files in out-dir")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--debug", action="store_true", help="Also save annotated debug images")
    parser.add_argument("--skip-file-upload", action="store_true", help="Skip OneNET file management upload")
    args = parser.parse_args()

    while True:
        try:
            measurement = run_once(args)
            print(f"done: {measurement}", flush=True)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr, flush=True)
            if not args.loop:
                raise

        if not args.loop:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
