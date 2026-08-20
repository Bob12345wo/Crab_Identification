"""Calibrate the fixed crab measurement plane with a metric ChArUco board.

The resulting homography maps the 640x640 model-input pixel coordinates to
millimetres on the fixed weighing plane. Camera position and ROI settings must
remain unchanged after calibration.
"""

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import cv2
import depthai as dai
import numpy as np


def build_pipeline(
    input_size: int,
    camera_source_size: int,
    roi_scale: float,
    roi_center_x: float,
    roi_center_y: float,
) -> tuple[dai.Device, dai.Pipeline, object, dict]:
    source_size = max(input_size, int(camera_source_size))
    source_size -= source_size % 2
    scale = min(1.0, max(0.10, float(roi_scale)))
    crop_size = max(32, min(source_size, int(round(source_size * scale))))
    crop_size -= crop_size % 2
    center_x = min(1.0, max(0.0, float(roi_center_x)))
    center_y = min(1.0, max(0.0, float(roi_center_y)))
    crop_x = int(round(center_x * source_size - crop_size / 2))
    crop_y = int(round(center_y * source_size - crop_size / 2))
    crop_x = max(0, min(source_size - crop_size, crop_x))
    crop_y = max(0, min(source_size - crop_size, crop_y))
    crop_x -= crop_x % 2
    crop_y -= crop_y % 2

    device = dai.Device()
    pipeline = dai.Pipeline(device)
    cam = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
    source_out = cam.requestOutput(
        (source_size, source_size),
        dai.ImgFrame.Type.NV12,
        dai.ImgResizeMode.CROP,
        5.0,
        True,
    )
    manip = pipeline.create(dai.node.ImageManip)
    manip.initialConfig.addCrop(crop_x, crop_y, crop_size, crop_size)
    manip.initialConfig.setOutputSize(input_size, input_size, dai.ImageManipConfig.ResizeMode.STRETCH)
    manip.initialConfig.setFrameType(dai.ImgFrame.Type.RGB888p)
    manip.setMaxOutputFrameSize(input_size * input_size * 3)
    source_out.link(manip.inputImage)
    queue = manip.out.createOutputQueue(maxSize=4, blocking=True)
    meta = {
        "source_size": [source_size, source_size],
        "model_size": [input_size, input_size],
        "roi_scale": scale,
        "roi_center": [center_x, center_y],
        "roi_pixels": [crop_x, crop_y, crop_size, crop_size],
        "undistortion_enabled": True,
    }
    return device, pipeline, queue, meta


def detect_charuco(frame: np.ndarray, detector: object) -> tuple[np.ndarray | None, np.ndarray | None]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _, _ = detector.detectBoard(gray)
    if corners is None or ids is None or len(ids) < 4:
        return None, None
    corners = np.asarray(corners, dtype=np.float32)
    cv2.cornerSubPix(
        gray,
        corners,
        (5, 5),
        (-1, -1),
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_COUNT, 30, 0.001),
    )
    return corners.reshape(-1, 2), np.asarray(ids, dtype=np.int32).reshape(-1)


def save_preview(
    path: Path,
    frame: np.ndarray,
    corners: np.ndarray,
    ids: np.ndarray,
    world_to_pixel: np.ndarray,
    quality: dict,
) -> None:
    preview = frame.copy()
    cv2.aruco.drawDetectedCornersCharuco(
        preview,
        corners.reshape(-1, 1, 2).astype(np.float32),
        ids.reshape(-1, 1).astype(np.int32),
    )
    board_outline_mm = np.array(
        [[[0.0, 0.0], [150.0, 0.0], [150.0, 210.0], [0.0, 210.0]]],
        dtype=np.float32,
    )
    outline_px = cv2.perspectiveTransform(board_outline_mm, world_to_pixel)[0]
    cv2.polylines(preview, [np.round(outline_px).astype(np.int32)], True, (0, 255, 255), 2)
    text = (
        f"corners={quality['corner_count']} rms={quality['rms_error_mm']:.3f}mm "
        f"p95={quality['p95_error_mm']:.3f}mm ok={quality['ok']}"
    )
    cv2.rectangle(preview, (0, 0), (preview.shape[1], 28), (0, 0, 0), -1)
    cv2.putText(preview, text, (7, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 255), 1, cv2.LINE_AA)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), preview):
        raise RuntimeError(f"Failed to save preview: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate the fixed OAK crab measurement plane.")
    parser.add_argument("--output", default="calibration_plane.json")
    parser.add_argument("--preview", default="calibration_plane_preview.jpg")
    parser.add_argument("--input-size", type=int, default=640)
    parser.add_argument("--camera-source-size", type=int, default=1920)
    parser.add_argument("--roi-scale", type=float, default=0.42)
    parser.add_argument("--roi-center-x", type=float, default=0.5)
    parser.add_argument("--roi-center-y", type=float, default=0.5)
    parser.add_argument("--warmup-frames", type=int, default=10)
    parser.add_argument("--frames", type=int, default=30)
    parser.add_argument("--min-corners", type=int, default=12)
    parser.add_argument("--ransac-threshold-mm", type=float, default=0.7)
    args = parser.parse_args()

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    board = cv2.aruco.CharucoBoard((5, 7), 30.0, 22.0, dictionary)
    detector = cv2.aruco.CharucoDetector(board)
    board_corners_mm = np.asarray(board.getChessboardCorners(), dtype=np.float32)[:, :2]

    device, pipeline, queue, camera_meta = build_pipeline(
        args.input_size,
        args.camera_source_size,
        args.roi_scale,
        args.roi_center_x,
        args.roi_center_y,
    )
    observations: dict[int, list[np.ndarray]] = defaultdict(list)
    valid_frames = 0
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
                valid_frames += 1
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
        raise RuntimeError(
            f"Only {len(stable_ids)} stable ChArUco corners found; need at least {args.min_corners}. "
            "Keep the board flat, fully visible, sharp, and free of glare."
        )

    pixel_points = np.array(
        [np.median(np.asarray(observations[corner_id]), axis=0) for corner_id in stable_ids],
        dtype=np.float32,
    )
    world_points = np.array([board_corners_mm[corner_id] for corner_id in stable_ids], dtype=np.float32)
    pixel_to_mm, inlier_mask = cv2.findHomography(
        pixel_points,
        world_points,
        cv2.RANSAC,
        args.ransac_threshold_mm,
    )
    if pixel_to_mm is None:
        raise RuntimeError("Homography estimation failed.")
    world_to_pixel = np.linalg.inv(pixel_to_mm)
    predicted_mm = cv2.perspectiveTransform(pixel_points.reshape(1, -1, 2), pixel_to_mm)[0]
    errors_mm = np.linalg.norm(predicted_mm - world_points, axis=1)
    inliers = np.ones(len(stable_ids), dtype=bool) if inlier_mask is None else inlier_mask.reshape(-1).astype(bool)
    inlier_errors = errors_mm[inliers]

    min_xy = pixel_points.min(axis=0)
    max_xy = pixel_points.max(axis=0)
    span = (max_xy - min_xy) / float(args.input_size)
    hull = cv2.convexHull(pixel_points.reshape(-1, 1, 2))
    coverage = float(cv2.contourArea(hull) / (args.input_size * args.input_size))
    quality = {
        "ok": False,
        "corner_count": len(stable_ids),
        "inlier_count": int(np.count_nonzero(inliers)),
        "valid_frame_count": valid_frames,
        "capture_frame_count": max(1, args.frames),
        "rms_error_mm": float(np.sqrt(np.mean(np.square(inlier_errors)))),
        "mean_error_mm": float(np.mean(inlier_errors)),
        "p95_error_mm": float(np.percentile(inlier_errors, 95)),
        "max_error_mm": float(np.max(inlier_errors)),
        "span_x_ratio": float(span[0]),
        "span_y_ratio": float(span[1]),
        "convex_hull_area_ratio": coverage,
        "thresholds": {
            "min_corners": args.min_corners,
            "max_rms_error_mm": 0.5,
            "max_p95_error_mm": 1.0,
            "min_span_ratio": 0.30,
        },
        "reasons": [],
    }
    if quality["corner_count"] < args.min_corners:
        quality["reasons"].append("not_enough_corners")
    if quality["rms_error_mm"] > 0.5:
        quality["reasons"].append("rms_error_too_high")
    if quality["p95_error_mm"] > 1.0:
        quality["reasons"].append("p95_error_too_high")
    if quality["span_x_ratio"] < 0.30 or quality["span_y_ratio"] < 0.30:
        quality["reasons"].append("board_coverage_too_small")
    quality["ok"] = not quality["reasons"]

    result = {
        "version": 1,
        "type": "fixed_plane_pixel_to_mm_homography",
        "created_at_unix": time.time(),
        "coordinate_space": "model_input_pixels",
        "camera": camera_meta,
        "board": {
            "type": "charuco",
            "dictionary": "DICT_4X4_50",
            "squares_x": 5,
            "squares_y": 7,
            "square_length_mm": 30.0,
            "marker_length_mm": 22.0,
            "printed_board_width_mm": 150.0,
            "printed_board_height_mm": 210.0,
        },
        "pixel_to_mm_homography": pixel_to_mm.tolist(),
        "mm_to_pixel_homography": world_to_pixel.tolist(),
        "corner_ids": stable_ids,
        "corner_pixels": pixel_points.tolist(),
        "corner_world_mm": world_points.tolist(),
        "quality": quality,
    }

    if best_frame is not None:
        save_preview(Path(args.preview), best_frame, best_corners, best_ids, world_to_pixel, quality)
        print(f"Saved preview -> {args.preview}")

    output_path = Path(args.output)
    if not quality["ok"]:
        failed_path = output_path.with_name(output_path.stem + ".failed" + output_path.suffix)
        failed_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        raise RuntimeError(f"Calibration quality failed: {quality['reasons']}. Details: {failed_path}")

    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Saved calibration -> {output_path}")
    print(
        f"quality: corners={quality['corner_count']} inliers={quality['inlier_count']} "
        f"rms={quality['rms_error_mm']:.3f}mm p95={quality['p95_error_mm']:.3f}mm "
        f"max={quality['max_error_mm']:.3f}mm coverage={quality['convex_hull_area_ratio']:.3f}"
    )


if __name__ == "__main__":
    main()
