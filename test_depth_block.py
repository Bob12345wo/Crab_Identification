"""Capture or replay aligned OAK depth to measure a known-height block."""

import argparse
import json
from datetime import timedelta
from pathlib import Path
import time

import numpy as np

from crab_thickness import measure_thickness
from thickness_calibration import CalibrationError, attach_thickness_calibration, load_calibration


def summarize_frame_results(frame_results, expected_height_mm, min_valid_ratio,
                            max_frame_spread_mm, max_error_mm):
    successful = [row for row in frame_results if row["ok"]]
    frame_count = len(frame_results)
    required = max(1, int(np.ceil(frame_count * min_valid_ratio)))
    values = np.asarray([row["thickness_mm"] for row in successful], dtype=float)
    reasons = []
    if len(successful) < required:
        reasons.append("insufficient_valid_frames")

    result = {
        "ok": False,
        "reason": "pending",
        "quality_reasons": reasons,
        "unit": "mm",
        "frame_count": frame_count,
        "valid_frame_count": len(successful),
        "required_valid_frames": required,
        "valid_frame_ratio": len(successful) / frame_count if frame_count else 0.0,
        "thickness_mm": None,
        "mean_thickness_mm": None,
        "median_thickness_mm": None,
        "mad_mm": None,
        "std_mm": None,
        "range_mm": None,
        "valid_depth_ratio": None,
        "plane_inlier_ratio": None,
        "plane_rmse_mm": None,
    }
    if values.size:
        median = float(np.median(values))
        deviations = np.abs(values - median)
        result.update(
            thickness_mm=median,
            mean_thickness_mm=float(np.mean(values)),
            median_thickness_mm=median,
            mad_mm=float(np.median(deviations)),
            std_mm=float(np.std(values)),
            range_mm=float(np.ptp(values)),
            valid_depth_ratio=float(np.median([row["valid_depth_ratio"] for row in successful])),
            plane_inlier_ratio=float(np.median([row["plane_inlier_ratio"] for row in successful])),
            plane_rmse_mm=float(np.median([row["plane_rmse_mm"] for row in successful])),
        )
        if result["range_mm"] > max_frame_spread_mm:
            reasons.append("frame_spread_too_large")
        if expected_height_mm is not None:
            error = median - expected_height_mm
            result.update(
                expected_height_mm=expected_height_mm,
                error_mm=float(error),
                absolute_error_mm=float(abs(error)),
            )
            if abs(error) > max_error_mm:
                reasons.append("reference_error_too_high")
    else:
        result["expected_height_mm"] = expected_height_mm
        frame_reasons = [row.get("reason") for row in frame_results]
        if frame_reasons and all(reason == "thickness_below_depth_resolution" for reason in frame_reasons):
            reasons = ["thickness_below_depth_resolution"]

    result["quality_reasons"] = reasons
    result["reason"] = "ok" if not reasons else ";".join(reasons)
    result["ok"] = not reasons
    return result


def capture(args):
    import depthai as dai
    import cv2

    size = args.input_size
    source = args.camera_source_size
    crop = int(round(source * args.roi_scale)) // 2 * 2
    x = min(source - crop, max(0, int(round(source * args.roi_center_x - crop / 2)))) // 2 * 2
    y = min(source - crop, max(0, int(round(source * args.roi_center_y - crop / 2)))) // 2 * 2
    device = dai.Device()
    with dai.Pipeline(device) as pipeline:
        rgb = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
        left = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B)
        right = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C)
        source_out = rgb.requestOutput((source, source), dai.ImgFrame.Type.NV12,
                                       dai.ImgResizeMode.CROP, 5.0, True)
        manip = pipeline.create(dai.node.ImageManip)
        manip.initialConfig.addCrop(x, y, crop, crop)
        manip.initialConfig.setOutputSize(size, size, dai.ImageManipConfig.ResizeMode.STRETCH)
        manip.initialConfig.setFrameType(dai.ImgFrame.Type.RGB888p)
        manip.setMaxOutputFrameSize(size * size * 3)
        source_out.link(manip.inputImage)
        stereo = pipeline.create(dai.node.StereoDepth)
        stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.DEFAULT)
        stereo.setLeftRightCheck(True)
        stereo.setSubpixel(True)
        stereo.initialConfig.setDepthUnit(dai.LengthUnit.MILLIMETER)
        left.requestOutput((640, 400), dai.ImgFrame.Type.GRAY8, fps=5.0).link(stereo.left)
        right.requestOutput((640, 400), dai.ImgFrame.Type.GRAY8, fps=5.0).link(stereo.right)
        manip.out.link(stereo.inputAlignTo)
        stereo.setOutputSize(size, size)
        sync = pipeline.create(dai.node.Sync)
        sync.setSyncThreshold(timedelta(milliseconds=40))
        sync.setSyncAttempts(-1)
        manip.out.link(sync.inputs['rgb'])
        stereo.depth.link(sync.inputs['depth'])
        queue = sync.out.createOutputQueue(maxSize=4, blocking=False)
        pipeline.start()
        samples = []
        for index in range(args.warmup_frames + args.frames):
            deadline = time.monotonic() + args.capture_timeout
            group = None
            while time.monotonic() < deadline:
                group = queue.tryGet()
                if group is not None:
                    break
                time.sleep(.01)
            if group is None:
                raise TimeoutError('Timed out waiting for aligned RGB and depth frames')
            color, depth = group['rgb'], group['depth']
            frame, depth_mm = color.getCvFrame(), depth.getFrame()
            if frame.shape[:2] != depth_mm.shape:
                raise RuntimeError('RGB and depth dimensions differ')
            intrinsics = np.asarray(color.getTransformation().getIntrinsicMatrix(), dtype=np.float64)
            delta = abs((color.getTimestamp() - depth.getTimestamp()).total_seconds()) * 1000
            if index >= args.warmup_frames:
                samples.append((frame, depth_mm, intrinsics, delta))
    frame = samples[-1][0]
    if not cv2.imwrite(str(args.image), frame):
        raise RuntimeError(f'Could not save image: {args.image}')
    np.savez_compressed(
        args.depth,
        depth_mm=np.stack([sample[1] for sample in samples]),
        intrinsics=np.stack([sample[2] for sample in samples]),
        timestamp_delta_ms=np.asarray([sample[3] for sample in samples]),
    )
    print(f'Saved {args.image} and {args.depth}; synchronized frames={len(samples)}')


def analyze(args):
    if args.bbox is None:
        print('Open the saved image, find block bounds in pixels, then rerun with --bbox X1 Y1 X2 Y2')
        return
    with np.load(args.depth, allow_pickle=False) as data:
        depth = data['depth_mm']
        intrinsics = data['intrinsics']
        delta = data['timestamp_delta_ms'] if 'timestamp_delta_ms' in data else None
    if depth.ndim == 2:
        depth = depth[None, ...]
    if intrinsics.ndim == 2:
        intrinsics = np.repeat(intrinsics[None, ...], len(depth), axis=0)
    if len(intrinsics) != len(depth):
        raise ValueError('Depth and intrinsics frame counts differ')
    if delta is not None:
        delta = np.atleast_1d(delta)
        if len(delta) != len(depth):
            raise ValueError('Depth and timestamp frame counts differ')
    frame_results = []
    for index, (frame_depth, matrix) in enumerate(zip(depth, intrinsics)):
        row = measure_thickness(frame_depth, matrix, args.bbox, args.body_scale,
                                args.max_height_mm, args.plane_tolerance_mm)
        row['frame_index'] = index
        if delta is not None:
            row['timestamp_delta_ms'] = float(delta[index])
        frame_results.append(row)
    try:
        thickness_calibration = (
            load_calibration(args.thickness_calibration)
            if args.thickness_calibration else None
        )
    except CalibrationError as exc:
        raise SystemExit(f'Invalid thickness calibration: {exc}') from exc
    result = summarize_frame_results(
        frame_results,
        args.height_mm,
        args.min_valid_ratio,
        args.max_frame_spread_mm,
        args.max_error_mm,
    )
    attach_thickness_calibration(result, thickness_calibration, args.max_error_mm)
    result.update({
        'unit': 'mm',
        'bbox_xyxy': args.bbox,
        'depth_file': str(args.depth),
        'frames': frame_results,
    })
    report = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    args.output.write_text(report + '\n', encoding='utf-8')
    print(report)
    if not result['ok']:
        raise SystemExit(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', action='store_true', help='Capture from a USB-connected OAK')
    parser.add_argument('--image', type=Path, default=Path('block_rgb.jpg'))
    parser.add_argument('--depth', type=Path, default=Path('block_depth.npz'))
    parser.add_argument('--output', type=Path, default=Path('block_result.json'))
    parser.add_argument('--bbox', type=int, nargs=4, metavar=('X1', 'Y1', 'X2', 'Y2'))
    parser.add_argument('--height-mm', type=float, help='Height measured with calipers')
    parser.add_argument('--body-scale', type=float, default=.35)
    parser.add_argument('--max-height-mm', type=float, default=150.)
    parser.add_argument('--plane-tolerance-mm', type=float, default=3.)
    parser.add_argument('--min-valid-ratio', type=float, default=.8,
                        help='Minimum fraction of valid frames required')
    parser.add_argument('--max-frame-spread-mm', type=float, default=5.,
                        help='Maximum valid-frame thickness range')
    parser.add_argument('--max-error-mm', type=float, default=2.,
                        help='Maximum error against --height-mm')
    parser.add_argument('--thickness-calibration', type=Path,
                        help='Bounded linear calibration JSON for raw OAK thickness')
    parser.add_argument('--frames', type=int, default=5)
    parser.add_argument('--warmup-frames', type=int, default=15,
                        help='Discard initial synchronized frames while stereo depth settles')
    parser.add_argument('--capture-timeout', type=float, default=30.)
    parser.add_argument('--input-size', type=int, default=640)
    parser.add_argument('--camera-source-size', type=int, default=1920)
    parser.add_argument('--roi-scale', type=float, default=.42)
    parser.add_argument('--roi-center-x', type=float, default=.5)
    parser.add_argument('--roi-center-y', type=float, default=.5)
    args = parser.parse_args()
    if args.frames < 1 or args.warmup_frames < 0 or args.input_size < 32 or args.camera_source_size < args.input_size:
        parser.error('Invalid frame count or camera size')
    if not 0 < args.roi_scale <= 1 or not 0 <= args.roi_center_x <= 1 or not 0 <= args.roi_center_y <= 1:
        parser.error('Invalid ROI settings')
    if not 0 < args.body_scale <= 1 or not np.isfinite(args.max_height_mm) or args.max_height_mm <= 0:
        parser.error('Invalid body scale or maximum height')
    if not 0 < args.plane_tolerance_mm or not np.isfinite(args.plane_tolerance_mm):
        parser.error('--plane-tolerance-mm must be finite and positive')
    if not 0 < args.min_valid_ratio <= 1 or not np.isfinite(args.min_valid_ratio):
        parser.error('--min-valid-ratio must be in (0, 1]')
    if not np.isfinite(args.max_frame_spread_mm) or args.max_frame_spread_mm <= 0:
        parser.error('--max-frame-spread-mm must be finite and positive')
    if not np.isfinite(args.max_error_mm) or args.max_error_mm <= 0:
        parser.error('--max-error-mm must be finite and positive')
    if args.height_mm is not None and (not np.isfinite(args.height_mm) or args.height_mm <= 0):
        parser.error('--height-mm must be positive')
    if args.thickness_calibration is not None and not args.thickness_calibration.is_file():
        parser.error(f'Thickness calibration file does not exist: {args.thickness_calibration}')
    if args.capture:
        capture(args)
    elif not args.depth.exists():
        parser.error(f'Depth file does not exist: {args.depth}; use --capture')
    analyze(args)


if __name__ == '__main__':
    main()
