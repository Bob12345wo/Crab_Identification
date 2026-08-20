"""Validate a fixed-plane homography with a moved ChArUco board."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from calibrate_plane import build_pipeline, detect_charuco


def transform_points(points: np.ndarray, homography: np.ndarray) -> np.ndarray:
    return cv2.perspectiveTransform(points.reshape(1, -1, 2).astype(np.float32), homography)[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate plane calibration using a moved ChArUco board.")
    parser.add_argument("--calibration", default="calibration_plane.json")
    parser.add_argument("--output", default="calibration_validation.json")
    parser.add_argument("--preview", default="calibration_validation_preview.jpg")
    parser.add_argument("--warmup-frames", type=int, default=10)
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument("--min-corners", type=int, default=12)
    args = parser.parse_args()

    calibration = json.loads(Path(args.calibration).read_text(encoding="utf-8"))
    if not calibration.get("quality", {}).get("ok", False):
        raise RuntimeError("Calibration file is not marked valid.")
    camera = calibration["camera"]
    input_size = int(camera["model_size"][0])
    source_size = int(camera["source_size"][0])
    roi_scale = float(camera["roi_scale"])
    roi_center_x, roi_center_y = map(float, camera["roi_center"])
    pixel_to_mm = np.asarray(calibration["pixel_to_mm_homography"], dtype=np.float64)

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    board = cv2.aruco.CharucoBoard((5, 7), 30.0, 22.0, dictionary)
    detector = cv2.aruco.CharucoDetector(board)
    board_corners_mm = np.asarray(board.getChessboardCorners(), dtype=np.float32)[:, :2]

    device, pipeline, queue, actual_camera = build_pipeline(
        input_size,
        source_size,
        roi_scale,
        roi_center_x,
        roi_center_y,
    )
    if actual_camera != camera:
        raise RuntimeError("Camera/ROI settings do not match the calibration file.")

    observations: dict[int, list[np.ndarray]] = defaultdict(list)
    best_frame = None
    best_corners = None
    best_ids = None
    try:
        with pipeline:
            pipeline.start()
            for _ in range(max(0, args.warmup_frames)):
                queue.get()
            for index in range(max(1, args.frames)):
                frame = queue.get().getCvFrame()
                corners, ids = detect_charuco(frame, detector)
                count = 0 if ids is None else len(ids)
                print(f"frame {index + 1}/{args.frames}: corners={count}")
                if ids is None:
                    continue
                for corner, corner_id in zip(corners, ids):
                    observations[int(corner_id)].append(corner.copy())
                if best_ids is None or len(ids) > len(best_ids):
                    best_frame = frame.copy()
                    best_corners = corners.copy()
                    best_ids = ids.copy()
    finally:
        del pipeline
        del device

    stable_ids = sorted(corner_id for corner_id, rows in observations.items() if len(rows) >= 3)
    if len(stable_ids) < args.min_corners:
        raise RuntimeError(f"Only {len(stable_ids)} stable corners found; need {args.min_corners}.")

    pixels = np.asarray(
        [np.median(np.asarray(observations[corner_id]), axis=0) for corner_id in stable_ids],
        dtype=np.float32,
    )
    measured_plane_mm = transform_points(pixels, pixel_to_mm)
    expected_local_mm = np.asarray([board_corners_mm[corner_id] for corner_id in stable_ids], dtype=np.float32)

    comparisons = []
    for left in range(len(stable_ids)):
        for right in range(left + 1, len(stable_ids)):
            expected = float(np.linalg.norm(expected_local_mm[left] - expected_local_mm[right]))
            if expected < 30.0:
                continue
            measured = float(np.linalg.norm(measured_plane_mm[left] - measured_plane_mm[right]))
            comparisons.append(
                {
                    "corner_ids": [stable_ids[left], stable_ids[right]],
                    "expected_mm": expected,
                    "measured_mm": measured,
                    "error_mm": measured - expected,
                    "abs_error_mm": abs(measured - expected),
                }
            )
    absolute_errors = np.asarray([row["abs_error_mm"] for row in comparisons], dtype=np.float64)
    signed_errors = np.asarray([row["error_mm"] for row in comparisons], dtype=np.float64)
    expected_lengths = np.asarray([row["expected_mm"] for row in comparisons], dtype=np.float64)
    measured_lengths = np.asarray([row["measured_mm"] for row in comparisons], dtype=np.float64)
    quality = {
        "ok": False,
        "corner_count": len(stable_ids),
        "distance_comparison_count": len(comparisons),
        "mean_signed_error_mm": float(np.mean(signed_errors)),
        "mean_abs_error_mm": float(np.mean(absolute_errors)),
        "rms_error_mm": float(np.sqrt(np.mean(np.square(signed_errors)))),
        "p95_abs_error_mm": float(np.percentile(absolute_errors, 95)),
        "max_abs_error_mm": float(np.max(absolute_errors)),
        "median_scale_ratio": float(np.median(measured_lengths / expected_lengths)),
        "reasons": [],
        "thresholds": {
            "max_rms_error_mm": 0.8,
            "max_p95_abs_error_mm": 1.5,
            "max_scale_error_percent": 1.0,
        },
    }
    if quality["rms_error_mm"] > 0.8:
        quality["reasons"].append("rms_error_too_high")
    if quality["p95_abs_error_mm"] > 1.5:
        quality["reasons"].append("p95_error_too_high")
    if abs(quality["median_scale_ratio"] - 1.0) > 0.01:
        quality["reasons"].append("scale_error_too_high")
    quality["ok"] = not quality["reasons"]

    result = {
        "type": "fixed_plane_calibration_validation",
        "calibration": args.calibration,
        "camera": camera,
        "quality": quality,
        "corner_ids": stable_ids,
        "comparisons": comparisons,
    }
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")

    if best_frame is not None:
        preview = best_frame.copy()
        cv2.aruco.drawDetectedCornersCharuco(
            preview,
            best_corners.reshape(-1, 1, 2).astype(np.float32),
            best_ids.reshape(-1, 1).astype(np.int32),
        )
        text = (
            f"independent validation rms={quality['rms_error_mm']:.3f}mm "
            f"p95={quality['p95_abs_error_mm']:.3f}mm "
            f"scale={quality['median_scale_ratio']:.5f} ok={quality['ok']}"
        )
        cv2.rectangle(preview, (0, 0), (preview.shape[1], 28), (0, 0, 0), -1)
        cv2.putText(preview, text, (7, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.imwrite(args.preview, preview)

    print(
        f"validation: ok={quality['ok']} corners={quality['corner_count']} "
        f"rms={quality['rms_error_mm']:.3f}mm p95={quality['p95_abs_error_mm']:.3f}mm "
        f"max={quality['max_abs_error_mm']:.3f}mm scale={quality['median_scale_ratio']:.6f}"
    )
    print(f"Saved {args.output} and {args.preview}")
    if not quality["ok"]:
        raise RuntimeError(f"Independent validation failed: {quality['reasons']}")


if __name__ == "__main__":
    main()
