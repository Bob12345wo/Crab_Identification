"""Run one OAK crab pose inference, measure legs, and optionally send RS485 data.

This script targets DepthAI v3 and the YOLO pose blob exported at 640x640.
It intentionally avoids ultralytics on the Orange Pi.
"""

import argparse
import json
import statistics
import struct
import time
import uuid
from datetime import timedelta
from pathlib import Path

import depthai as dai
import numpy as np

from crab_thickness import add_depth_arguments, measure_thickness, validate_depth_arguments
from upload_queue import write_json


LEG_DEFS = [
    ("L-Claw", [4, 5, 6, 7, 8, 9]),
    ("L-Tleg", [10, 11, 12, 13]),
    ("L-Aleg", [14, 15, 16, 17]),
    ("L-Bleg", [18, 19, 20, 21]),
    ("L-Cleg", [22, 23, 24, 25]),
    ("R-Claw", [26, 27, 28, 29, 30, 31]),
    ("R-Tleg", [32, 33, 34, 35]),
    ("R-Aleg", [36, 37, 38, 39]),
    ("R-Bleg", [40, 41, 42, 43]),
    ("R-Cleg", [44, 45, 46, 47]),
]

CLAW_NAMES = {"L-Claw", "R-Claw"}


def xywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    cx, cy, w, h = boxes.T
    return np.stack((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), axis=1)


def box_iou(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    x1 = np.maximum(box[0], boxes[:, 0])
    y1 = np.maximum(box[1], boxes[:, 1])
    x2 = np.minimum(box[2], boxes[:, 2])
    y2 = np.minimum(box[3], boxes[:, 3])
    inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    area1 = max(0, box[2] - box[0]) * max(0, box[3] - box[1])
    area2 = np.maximum(0, boxes[:, 2] - boxes[:, 0]) * np.maximum(0, boxes[:, 3] - boxes[:, 1])
    return inter / np.maximum(area1 + area2 - inter, 1e-6)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thres: float) -> list[int]:
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        idx = int(order[0])
        keep.append(idx)
        if order.size == 1:
            break
        ious = box_iou(boxes[idx], boxes[order[1:]])
        order = order[1:][ious <= iou_thres]
    return keep


def decode_pose(output: np.ndarray, conf_thres: float, iou_thres: float, input_size: int) -> dict | None:
    # output shape: (149, 8400), rows are [cx, cy, w, h, cls_conf, kpt0_x, kpt0_y, kpt0_conf, ...]
    pred = output.T
    scores = pred[:, 4]
    mask = scores >= conf_thres
    if not np.any(mask):
        best = int(np.argmax(scores))
        return {
            "ok": False,
            "reason": "no_detection_above_threshold",
            "best_score": float(scores[best]),
        }

    pred = pred[mask]
    scores = pred[:, 4]
    boxes_xyxy = xywh_to_xyxy(pred[:, :4])
    keep = nms(boxes_xyxy, scores, iou_thres)
    best_idx = keep[0]
    best = pred[best_idx]

    kpts = best[5:].reshape(48, 3).astype(np.float32)
    kpts[:, 0] = np.clip(kpts[:, 0], 0, input_size - 1)
    kpts[:, 1] = np.clip(kpts[:, 1], 0, input_size - 1)
    kpts[:, 2] = np.clip(kpts[:, 2], 0, 1)

    return {
        "ok": True,
        "score": float(best[4]),
        "bbox_xyxy": boxes_xyxy[best_idx].clip(0, input_size - 1).tolist(),
        "keypoints": kpts.tolist(),
        "detections_after_conf": int(len(pred)),
    }


def load_homography(path: str | None, expected_camera: dict | None = None) -> np.ndarray | None:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    quality = data.get("quality")
    if quality is not None and not quality.get("ok", False):
        raise RuntimeError(f"Calibration quality is not valid: {quality.get('reasons', [])}")
    if expected_camera is not None and data.get("camera"):
        calibrated_camera = data["camera"]
        mismatches = []
        for key in ("source_size", "model_size", "roi_pixels"):
            if list(calibrated_camera.get(key, [])) != list(expected_camera.get(key, [])):
                mismatches.append(key)
        if abs(float(calibrated_camera.get("roi_scale", -1)) - float(expected_camera.get("roi_scale", -2))) > 1e-6:
            mismatches.append("roi_scale")
        if calibrated_camera.get("undistortion_enabled") != expected_camera.get("undistortion_enabled"):
            mismatches.append("undistortion_enabled")
        calibrated_center = np.asarray(calibrated_camera.get("roi_center", []), dtype=np.float64)
        expected_center = np.asarray(expected_camera.get("roi_center", []), dtype=np.float64)
        if calibrated_center.shape != expected_center.shape or not np.allclose(calibrated_center, expected_center, atol=1e-6):
            mismatches.append("roi_center")
        if mismatches:
            raise RuntimeError(
                "Calibration does not match the current camera/ROI settings: " + ", ".join(mismatches)
            )
    return np.array(data["pixel_to_mm_homography"], dtype=np.float64)


def transform_points(points: np.ndarray, homography: np.ndarray) -> np.ndarray:
    pts = points.astype(np.float64).reshape(-1, 1, 2)
    out = np.empty((len(points), 2), dtype=np.float64)
    for i, p in enumerate(pts):
        x, y = p[0]
        den = homography[2, 0] * x + homography[2, 1] * y + homography[2, 2]
        out[i, 0] = (homography[0, 0] * x + homography[0, 1] * y + homography[0, 2]) / den
        out[i, 1] = (homography[1, 0] * x + homography[1, 1] * y + homography[1, 2]) / den
    return out


def measure_legs(kpts: np.ndarray, kpt_conf: float, homography: np.ndarray | None) -> list[dict]:
    world = transform_points(kpts[:, :2], homography) if homography is not None else None
    rows = []
    for leg_name, indices in LEG_DEFS:
        seg_px = []
        seg_mm = []
        confs = [float(kpts[i, 2]) for i in indices]
        for a, b in zip(indices, indices[1:]):
            if kpts[a, 2] >= kpt_conf and kpts[b, 2] >= kpt_conf:
                seg_px.append(float(np.linalg.norm(kpts[a, :2] - kpts[b, :2])))
                if world is not None:
                    seg_mm.append(float(np.linalg.norm(world[a] - world[b])))
            else:
                seg_px.append(0.0)
                if world is not None:
                    seg_mm.append(0.0)

        valid = sum(1 for x in seg_px if x > 0)
        expected = len(indices) - 1
        reliable = valid == expected and min(confs) >= kpt_conf
        rows.append(
            {
                "leg": leg_name,
                "total_px": float(sum(seg_px)),
                "total_mm": None if world is None else float(sum(seg_mm)),
                "min_conf": float(min(confs)),
                "valid_segments": valid,
                "expected_segments": expected,
                "reliable": reliable,
                "reason": "ok" if reliable else "missing_or_low_confidence",
                "segments_px": seg_px,
                "segments_mm": None if world is None else seg_mm,
            }
        )
    return rows


def assess_pose_quality(
    pose: dict | None,
    input_size: int,
    min_score: float,
    min_bbox_area: float,
    max_bbox_area: float,
    max_bbox_aspect: float,
    max_outside_kpts: int,
) -> dict:
    if not pose or not pose.get("ok"):
        reason = "no_pose" if not pose else pose.get("reason", "pose_decode_failed")
        return {"ok": False, "reasons": [reason]}

    reasons = []
    score = float(pose["score"])
    x1, y1, x2, y2 = [float(v) for v in pose["bbox_xyxy"]]
    w = max(0.0, x2 - x1)
    h = max(0.0, y2 - y1)
    area_ratio = (w * h) / float(input_size * input_size)
    aspect = max(w / max(h, 1e-6), h / max(w, 1e-6))

    if score < min_score:
        reasons.append(f"low_score:{score:.3f}< {min_score:.3f}")
    if area_ratio < min_bbox_area:
        reasons.append(f"bbox_too_small:{area_ratio:.3f}< {min_bbox_area:.3f}")
    if area_ratio > max_bbox_area:
        reasons.append(f"bbox_too_large:{area_ratio:.3f}> {max_bbox_area:.3f}")
    if aspect > max_bbox_aspect:
        reasons.append(f"bbox_aspect_extreme:{aspect:.2f}> {max_bbox_aspect:.2f}")

    kpts = np.array(pose["keypoints"], dtype=np.float32)
    margin = 0.03 * input_size
    outside = np.sum(
        (kpts[:, 0] < x1 - margin)
        | (kpts[:, 0] > x2 + margin)
        | (kpts[:, 1] < y1 - margin)
        | (kpts[:, 1] > y2 + margin)
    )
    if int(outside) > max_outside_kpts:
        reasons.append(f"too_many_keypoints_outside_bbox:{int(outside)}> {max_outside_kpts}")

    return {
        "ok": not reasons,
        "reasons": reasons,
        "score": score,
        "bbox_area_ratio": area_ratio,
        "bbox_aspect": aspect,
        "keypoints_outside_bbox": int(outside),
    }


def assess_leg_geometry(
    legs: list[dict],
    max_segment_ratio: float,
    claw_max_segment_ratio: float,
    min_total_px: float,
) -> None:
    for row in legs:
        positive = [x for x in row["segments_px"] if x > 0]
        ratio_limit = claw_max_segment_ratio if row["leg"] in CLAW_NAMES else max_segment_ratio
        reasons = [] if row["reason"] == "ok" else [row["reason"]]

        if row["total_px"] < min_total_px:
            reasons.append(f"leg_too_short:{row['total_px']:.2f}< {min_total_px:.2f}")
        if len(positive) >= 2:
            ratio = max(positive) / max(min(positive), 1e-6)
            row["max_segment_ratio_observed"] = float(ratio)
            if ratio > ratio_limit:
                reasons.append(f"implausible_segment_ratio:{ratio:.2f}> {ratio_limit:.2f}")
        else:
            row["max_segment_ratio_observed"] = None

        if reasons:
            row["reliable"] = False
            row["reason"] = ";".join(dict.fromkeys(reasons))


def invalidate_legs(legs: list[dict], reason: str) -> None:
    for row in legs:
        row["reliable"] = False
        row["reason"] = reason if row["reason"] == "ok" else f"{reason};{row['reason']}"


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def build_rs485_packet(seq: int, legs: list[dict], use_mm: bool, weight_g: float | None = None) -> bytes:
    # Frame: AA55 version seq flags int32(weight_g*10) 10*uint16 reliable_mask crc16_le
    # Length unit: mm*100 when calibrated, otherwise px*100.
    flags = 1 if use_mm else 0
    if weight_g is not None:
        flags |= 0x02
    payload = bytearray([0xAA, 0x55, 1, seq & 0xFF, flags])
    encoded_weight = 0 if weight_g is None else int(round(max(-214748364.8, min(weight_g, 214748364.7)) * 10.0))
    payload += struct.pack("<i", encoded_weight)
    reliable_mask = 0
    for i, row in enumerate(legs):
        value = row["total_mm"] if use_mm else row["total_px"]
        if row["reliable"]:
            reliable_mask |= 1 << i
        encoded = int(round(max(0.0, min(float(value) * 100.0, 65535.0))))
        payload += struct.pack("<H", encoded)
    payload += struct.pack("<H", reliable_mask)
    crc = crc16_modbus(payload)
    payload += struct.pack("<H", crc)
    return bytes(payload)


def send_rs485(port: str, baud: int, packet: bytes) -> None:
    import serial

    with serial.Serial(port, baudrate=baud, timeout=1) as ser:
        ser.write(packet)
        ser.flush()


def read_modbus_registers(port: str, baud: int, slave: int, start: int, count: int) -> list[int]:
    import serial

    frame = struct.pack(">B B H H", slave, 0x03, start, count)
    frame += struct.pack("<H", crc16_modbus(frame))
    with serial.Serial(port, baudrate=baud, bytesize=8, parity="N", stopbits=1, timeout=1, write_timeout=1) as ser:
        ser.write(frame)
        ser.flush()
        resp = ser.read(5 + count * 2)
    if len(resp) < 5:
        raise RuntimeError(f"No Modbus response from {port}. TX={frame.hex(' ')}")
    data = resp[:-2]
    recv_crc = struct.unpack("<H", resp[-2:])[0]
    if crc16_modbus(data) != recv_crc:
        raise RuntimeError(f"Modbus CRC error. RX={resp.hex(' ')}")
    if resp[0] != slave or resp[1] != 0x03:
        raise RuntimeError(f"Unexpected Modbus response. RX={resp.hex(' ')}")
    if len(resp) != 5 + count * 2 or resp[2] != count * 2:
        raise RuntimeError(f"Unexpected Modbus register count. RX={resp.hex(' ')}")
    return [struct.unpack(">H", resp[3 + i * 2:5 + i * 2])[0] for i in range(count)]


def read_weight(port: str, baud: int, slave: int, start: int, scale: float, offset: float) -> dict:
    regs = read_modbus_registers(port, baud, slave, start, 4)
    raw_u32 = (regs[0] << 16) | regs[1]
    raw_i32 = raw_u32 - 0x100000000 if raw_u32 & 0x80000000 else raw_u32
    precision = regs[2]
    if not 0 <= precision <= 6:
        raise RuntimeError(f"Invalid weight precision register: {precision}")
    status = regs[3]
    weight_g = raw_i32 / (10 ** precision) * scale + offset
    return {
        "port": port,
        "baud": baud,
        "slave": slave,
        "start_register": start,
        "registers": regs,
        "raw": raw_i32,
        "precision": precision,
        "status": status,
        "stable": bool(status & (1 << 0)),
        "zero": bool(status & (1 << 1)),
        "overload": bool(status & (1 << 2)),
        "valid": bool(status & (1 << 5)),
        "weight_g": weight_g,
    }


def read_stable_weight(
    port: str,
    baud: int,
    slave: int,
    start: int,
    scale: float,
    offset: float,
    sample_count: int,
    interval: float,
    zero_deadband_g: float,
    max_spread_g: float = 2.0,
    min_samples: int = 2,
) -> dict:
    samples = []
    errors = []
    for index in range(max(1, sample_count)):
        try:
            sample = read_weight(port, baud, slave, start, scale, offset)
            samples.append(sample)
        except (OSError, RuntimeError, ValueError) as exc:
            samples.append(None)
            errors.append(str(exc))
        if index + 1 < sample_count:
            time.sleep(max(0.0, interval))

    valid = [row for row in samples if row and row["valid"] and not row["overload"]
             and np.isfinite(row["weight_g"])]
    stable = [row for row in valid if row["stable"]]
    values = [float(row["weight_g"]) for row in valid]
    usable = stable or valid
    median = statistics.median(row["weight_g"] for row in usable) if usable else None
    selected = min(usable, key=lambda row: abs(row["weight_g"] - median)) if usable else {}
    result = dict(selected)
    measured = float(selected["weight_g"]) if selected else None
    spread = max(values) - min(values) if values else None
    reasons = []
    if len(stable) < min_samples:
        reasons.append("insufficient_stable_samples")
    last = samples[-1]
    if not last or not last["valid"] or last["overload"] or not last["stable"] or not np.isfinite(last["weight_g"]):
        reasons.append("latest_sample_invalid_or_unstable")
    if spread is not None and spread > max_spread_g:
        reasons.append("weight_spread_too_large")
    if measured is not None and measured < -zero_deadband_g:
        reasons.append("negative_weight")
    ok = not reasons
    corrected = (0.0 if abs(measured) <= zero_deadband_g else measured) if ok else None
    result.update(
        ok=ok, reason="ok" if ok else ",".join(reasons), timestamp=time.time(),
        weight_g=corrected, measured_weight_g=measured,
        zero_deadband_g=zero_deadband_g, zero_corrected=ok and corrected != measured,
        sample_count=len(samples), valid_sample_count=len(valid), stable_sample_count=len(stable),
        weight_min_g=min(values) if values else None, weight_max_g=max(values) if values else None,
        weight_spread_g=spread, max_spread_g=max_spread_g, required_stable_samples=min_samples,
        samples=[None if row is None else {
            "weight_g": float(row["weight_g"]) if np.isfinite(row["weight_g"]) else None,
            "stable": bool(row["stable"]), "valid": bool(row["valid"]), "overload": bool(row["overload"]),
        } for row in samples], errors=errors,
    )
    return result


def wait_for_message(queue, deadline):
    while time.monotonic() < deadline:
        message = queue.tryGet()
        if message is not None:
            return message
        time.sleep(0.01)
    raise TimeoutError("Timed out waiting for OAK frame; check camera and USB connection")


def run_oak_frames(
    blob_path: str,
    input_size: int,
    frame_count: int,
    camera_source_size: int,
    roi_scale: float,
    roi_center_x: float,
    roi_center_y: float,
    depth_frames: list | None = None,
    capture_timeout: float = 30.0,
) -> tuple[list[tuple[np.ndarray, np.ndarray]], dict]:
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
    with dai.Pipeline(device) as pipeline:
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
        # The exported YOLO pose model expects RGB planar input. getCvFrame()
        # converts this output back to OpenCV BGR for local image saving.
        manip.initialConfig.setFrameType(dai.ImgFrame.Type.RGB888p)
        manip.setMaxOutputFrameSize(input_size * input_size * 3)
        source_out.link(manip.inputImage)
        img_out = manip.out

        nn = pipeline.create(dai.node.NeuralNetwork)
        nn.setBlobPath(blob_path)
        img_out.link(nn.input)

        queue_size = max(2, min(int(frame_count), 8))
        if depth_frames is not None:
            left = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B)
            right = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C)
            stereo = pipeline.create(dai.node.StereoDepth)
            stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.DEFAULT)
            stereo.setLeftRightCheck(True)
            stereo.setSubpixel(True)
            stereo.initialConfig.setDepthUnit(dai.LengthUnit.MILLIMETER)
            left.requestOutput((640, 400), dai.ImgFrame.Type.GRAY8, fps=5.0).link(stereo.left)
            right.requestOutput((640, 400), dai.ImgFrame.Type.GRAY8, fps=5.0).link(stereo.right)
            img_out.link(stereo.inputAlignTo)
            stereo.setOutputSize(input_size, input_size)
            sync = pipeline.create(dai.node.Sync)
            sync.setSyncThreshold(timedelta(milliseconds=40))
            sync.setSyncAttempts(-1)
            nn.passthrough.link(sync.inputs["rgb"])
            nn.out.link(sync.inputs["nn"])
            stereo.depth.link(sync.inputs["depth"])
            sync_q = sync.out.createOutputQueue(maxSize=queue_size, blocking=False)
        else:
            img_q = nn.passthrough.createOutputQueue(maxSize=queue_size, blocking=True)
            q = nn.out.createOutputQueue(maxSize=queue_size, blocking=True)
        pipeline.start()
        frames = []
        for _ in range(max(1, int(frame_count))):
            deadline = time.monotonic() + capture_timeout
            if depth_frames is not None:
                group = wait_for_message(sync_q, deadline)
                rgb, out, depth_msg = group["rgb"], group["nn"], group["depth"]
                frame = rgb.getCvFrame()
                depth = depth_msg.getFrame()
                if depth.shape != frame.shape[:2]:
                    raise RuntimeError("Aligned depth dimensions do not match inference image")
                depth_frames.append({
                    "depth": depth,
                    "intrinsics": np.asarray(rgb.getTransformation().getIntrinsicMatrix()),
                    "timestamp_delta_ms": abs((rgb.getTimestamp() - depth_msg.getTimestamp()).total_seconds()) * 1000,
                })
            else:
                frame = wait_for_message(img_q, deadline).getCvFrame()
                out = wait_for_message(q, deadline)
            tensor = out.getTensor(out.getAllLayerNames()[0])
            output = np.asarray(tensor[0] if tensor.ndim == 3 else tensor, dtype=np.float32)
            frames.append((output, frame))
        return frames, {
            "source_size": [source_size, source_size],
            "model_size": [input_size, input_size],
            "roi_scale": scale,
            "roi_center": [center_x, center_y],
            "roi_pixels": [crop_x, crop_y, crop_size, crop_size],
            "undistortion_enabled": True,
        }


def candidate_rank(candidate: dict) -> tuple:
    pose = candidate.get("pose") or {}
    quality = candidate.get("quality") or {}
    legs = candidate.get("legs") or []
    reliable_count = sum(1 for row in legs if row.get("reliable"))
    mean_kpt_conf = 0.0
    if pose.get("ok"):
        kpts = np.array(pose.get("keypoints", []), dtype=np.float32)
        if len(kpts):
            mean_kpt_conf = float(np.mean(kpts[:, 2]))
    return (
        1 if quality.get("ok") else 0,
        reliable_count,
        mean_kpt_conf,
        float(pose.get("score") or pose.get("best_score") or 0.0),
    )


def save_raw_image(path: str, frame: np.ndarray) -> None:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("Raw image output needs opencv-python installed.") from exc

    cv2.imwrite(path, frame)


def save_debug_image(path: str, frame: np.ndarray, pose: dict | None, kpt_conf: float,
                     thickness: dict | None = None) -> None:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("Debug image output needs opencv-python installed.") from exc

    vis = frame.copy()
    if pose and pose.get("ok"):
        x1, y1, x2, y2 = [int(v) for v in pose["bbox_xyxy"]]
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 255), 2)
        kpts = np.array(pose["keypoints"], dtype=np.float32)
        for _, indices in LEG_DEFS:
            for idx in indices:
                x, y, conf = kpts[idx]
                color = (0, 255, 0) if conf >= kpt_conf else (0, 0, 255)
                cv2.circle(vis, (int(x), int(y)), 3, color, -1)
            for a, b in zip(indices, indices[1:]):
                if kpts[a, 2] >= kpt_conf and kpts[b, 2] >= kpt_conf:
                    cv2.line(vis, (int(kpts[a, 0]), int(kpts[a, 1])), (int(kpts[b, 0]), int(kpts[b, 1])), (255, 0, 0), 2)
        quality = pose.get("quality", {})
        status = "ok" if quality.get("ok", True) else "bad"
        cv2.putText(
            vis,
            f"score={pose['score']:.3f} quality={status}",
            (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 255) if status == "ok" else (0, 0, 255),
            2,
        )
    else:
        reason = "no_pose" if not pose else pose.get("reason", "pose_decode_failed")
        best_score = "" if not pose or "best_score" not in pose else f" best={pose['best_score']:.3f}"
        cv2.putText(
            vis,
            f"quality=bad {reason}{best_score}",
            (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 0, 255),
            2,
        )
    if thickness is not None:
        roi = thickness.get("body_roi_xyxy")
        if roi:
            x1, y1, x2, y2 = map(int, roi)
            cv2.rectangle(vis, (x1, y1), (x2, y2), (255, 255, 0), 2)
        label = (f"Shell height: {thickness['thickness_mm']:.1f} mm" if thickness.get("ok")
                 else "Depth: " + thickness.get("reason", "unavailable"))
        cv2.putText(vis, label, (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 1)
    cv2.imwrite(path, vis)


def main():
    parser = argparse.ArgumentParser()
    add_depth_arguments(parser)
    parser.add_argument("--depth-out", help="Save selected aligned depth and intrinsics as NPZ (requires --depth)")
    parser.add_argument("--capture-timeout", type=float, default=30.0, help="Seconds allowed per RGB/NN/depth frame group")
    parser.add_argument("--weight-only", action="store_true", help="Only sample weight; --json-out - emits JSON to stdout")
    parser.add_argument("--blob", default="crab_pose_best_openvino_2022.1_4shave.blob")
    parser.add_argument("--input-size", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--kpt-conf", type=float, default=0.3)
    parser.add_argument("--min-score", type=float, default=0.30)
    parser.add_argument("--min-bbox-area", type=float, default=0.08, help="Minimum bbox area ratio in model input")
    parser.add_argument("--max-bbox-area", type=float, default=0.90, help="Maximum bbox area ratio in fixed ROI input")
    parser.add_argument("--max-bbox-aspect", type=float, default=3.0)
    parser.add_argument("--max-outside-kpts", type=int, default=2)
    parser.add_argument("--max-segment-ratio", type=float, default=5.0)
    parser.add_argument("--claw-max-segment-ratio", type=float, default=8.0)
    parser.add_argument("--min-leg-total-px", type=float, default=20.0)
    parser.add_argument(
        "--min-reliable-legs",
        type=int,
        default=8,
        help="Minimum reliable legs required for measurement_ok. Individual bad legs remain marked unreliable.",
    )
    parser.add_argument("--frame-count", type=int, default=5, help="Capture N frames and select the best measurement")
    parser.add_argument("--camera-source-size", type=int, default=1920, help="Square RGB source size before ROI crop")
    parser.add_argument("--roi-scale", type=float, default=0.42, help="Centered fraction of source retained for inference")
    parser.add_argument("--roi-center-x", type=float, default=0.5)
    parser.add_argument("--roi-center-y", type=float, default=0.5)
    parser.add_argument("--calibration", default=None)
    parser.add_argument("--json-out", default="measurement.json")
    parser.add_argument("--serial-port", default=None)
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--seq", type=int, default=0)
    parser.add_argument("--raw-image", default=None, help="Save model input image without any overlay")
    parser.add_argument("--debug-image", default=None, help="Save model input image with decoded pose overlay")
    parser.add_argument("--weight-port", default=None, help="Modbus-RTU weight module serial port, e.g. /dev/ttyUSB0")
    parser.add_argument("--weight-baud", type=int, default=9600)
    parser.add_argument("--weight-slave", type=lambda x: int(x, 0), default=0x01)
    parser.add_argument("--weight-register", type=lambda x: int(x, 0), default=0)
    parser.add_argument("--weight-scale", type=float, default=1.0, help="grams per raw count")
    parser.add_argument("--weight-offset", type=float, default=0.0, help="grams added after scaling")
    parser.add_argument("--weight-samples", type=int, default=3)
    parser.add_argument("--weight-sample-interval", type=float, default=0.15)
    parser.add_argument("--weight-zero-deadband", type=float, default=5.0, help="Clamp stable near-zero weight to 0 g")
    parser.add_argument("--weight-max-spread", type=float, default=2.0)
    parser.add_argument("--weight-min-samples", type=int, default=2)
    args = parser.parse_args()
    validate_depth_arguments(parser, args)
    if args.depth_out and not args.depth:
        parser.error("--depth-out requires --depth")
    if not np.isfinite(args.capture_timeout) or args.capture_timeout <= 0:
        parser.error("--capture-timeout must be finite and positive")
    if args.weight_only and not args.weight_port:
        parser.error("--weight-only requires --weight-port")
    if args.json_out == "-" and not args.weight_only:
        parser.error("--json-out - requires --weight-only")
    if args.weight_port:
        if not 2 <= args.weight_min_samples <= args.weight_samples:
            parser.error("Require 2 <= --weight-min-samples <= --weight-samples")
        if any(not np.isfinite(value) or value < 0 for value in
               (args.weight_max_spread, args.weight_zero_deadband, args.weight_sample_interval)):
            parser.error("Weight spread, deadband and interval must be finite and nonnegative")

    weight = None
    if args.weight_port:
        weight = read_stable_weight(
            args.weight_port,
            args.weight_baud,
            args.weight_slave,
            args.weight_register,
            args.weight_scale,
            args.weight_offset,
            args.weight_samples,
            args.weight_sample_interval,
            args.weight_zero_deadband,
            args.weight_max_spread,
            args.weight_min_samples,
        )
        if not args.weight_only:
            print(f"Weight: weight_g={weight['weight_g']} ok={weight['ok']} reason={weight['reason']} "
                  f"stable={weight['stable_sample_count']}/{weight['sample_count']} spread={weight['weight_spread_g']}")

    if args.weight_only:
        if args.json_out == "-":
            print(json.dumps({"weight": weight}, allow_nan=False))
        else:
            write_json(args.json_out, {"weight": weight})
        return

    depth_frames = [] if args.depth else None
    oak_frames, camera_meta = run_oak_frames(
        args.blob,
        args.input_size,
        args.frame_count,
        args.camera_source_size,
        args.roi_scale,
        args.roi_center_x,
        args.roi_center_y,
        depth_frames=depth_frames,
        capture_timeout=args.capture_timeout,
    )
    homography = load_homography(args.calibration, camera_meta)
    candidates = []
    for index, (output, frame) in enumerate(oak_frames):
        pose = decode_pose(output, args.conf, args.iou, args.input_size)
        quality = assess_pose_quality(
            pose,
            args.input_size,
            args.min_score,
            args.min_bbox_area,
            args.max_bbox_area,
            args.max_bbox_aspect,
            args.max_outside_kpts,
        )
        if pose:
            pose["quality"] = quality

        legs = []
        if pose and pose.get("ok"):
            kpts = np.array(pose["keypoints"], dtype=np.float32)
            legs = measure_legs(kpts, args.kpt_conf, homography)
            assess_leg_geometry(legs, args.max_segment_ratio, args.claw_max_segment_ratio, args.min_leg_total_px)
            if not quality["ok"]:
                invalidate_legs(legs, "pose_quality_failed")
        thickness = None
        if args.depth:
            thickness = {"ok": False, "thickness_mm": None, "reason": "pose_quality_failed", "unit": "mm"}
            if quality["ok"]:
                sample = depth_frames[index]
                thickness = measure_thickness(sample["depth"], sample["intrinsics"], pose["bbox_xyxy"],
                                              args.depth_body_scale, args.depth_max_height_mm,
                                              args.depth_plane_tolerance_mm)
                thickness["timestamp_delta_ms"] = sample["timestamp_delta_ms"]
        candidates.append({"index": index, "pose": pose, "quality": quality, "legs": legs,
                           "frame": frame, "thickness": thickness})

    selected = max(candidates, key=lambda row: (
        bool(row["quality"].get("ok") and (not args.depth or row["thickness"].get("ok"))
             and sum(1 for leg in row["legs"] if leg.get("reliable")) >= args.min_reliable_legs),
        *candidate_rank(row),
    ))
    selected_index = int(selected["index"])
    pose = selected["pose"]
    quality = selected["quality"]
    debug_frame = selected["frame"]
    selected_rank = candidate_rank(selected)
    print(
        f"Selected frame {selected_index + 1}/{len(candidates)}: "
        f"pose_score={selected_rank[3]:.3f} quality_ok={bool(selected_rank[0])} "
        f"reliable_legs={int(selected_rank[1])} reasons={quality.get('reasons', [])}"
    )
    candidate_summaries = [
        {
            "index": int(row["index"]),
            "quality_ok": bool(row["quality"].get("ok")),
            "quality_reasons": row["quality"].get("reasons", []),
            "thickness": row["thickness"],
            "reliable_leg_count": sum(1 for leg in row["legs"] if leg.get("reliable")),
            "pose_score": float((row.get("pose") or {}).get("score") or (row.get("pose") or {}).get("best_score") or 0.0),
        }
        for row in candidates
    ]

    result = {
        "measurement_id": uuid.uuid4().hex,
        "timestamp": time.time(),
        "model": Path(args.blob).name,
        "input_size": args.input_size,
        "camera": camera_meta,
        "pose": pose,
        "measurement_ok": False,
        "measurement_reasons": quality["reasons"],
        "legs": [],
        "unit": "mm" if args.calibration else "px",
        "weight": weight,
        "thickness": selected["thickness"],
        "capture": {
            "frame_count": len(candidates),
            "selected_frame_index": selected_index,
            "selected_rank": {
                "quality_ok": bool(selected_rank[0]),
                "reliable_leg_count": int(selected_rank[1]),
                "mean_keypoint_confidence": float(selected_rank[2]),
                "pose_score": float(selected_rank[3]),
            },
            "candidates": candidate_summaries,
        },
    }

    if pose and pose.get("ok"):
        result["legs"] = selected["legs"]
        reliable_count = sum(1 for row in result["legs"] if row["reliable"])
        result["unreliable_legs"] = [row["leg"] for row in result["legs"] if not row["reliable"]]
        result["reliable_leg_count"] = reliable_count
        result["required_reliable_leg_count"] = args.min_reliable_legs
        result["measurement_ok"] = bool(quality["ok"] and reliable_count >= args.min_reliable_legs)
        if reliable_count < args.min_reliable_legs:
            result["measurement_reasons"] = [
                *result["measurement_reasons"],
                f"not_enough_reliable_legs:{reliable_count}< {args.min_reliable_legs}",
            ]
        elif result["unreliable_legs"]:
            result["measurement_reasons"] = [
                *result["measurement_reasons"],
                "partial_unreliable_legs:" + ",".join(result["unreliable_legs"]),
            ]

    if weight is not None and not weight["ok"]:
        result["measurement_ok"] = False
        result["measurement_reasons"] = [*result["measurement_reasons"], "weight:" + weight["reason"]]

    if args.depth:
        thickness = result["thickness"]
        if not thickness["ok"]:
            result["measurement_ok"] = False
            result["measurement_reasons"] = [*result["measurement_reasons"], "thickness:" + thickness["reason"]]
        print(f"Thickness: {thickness['thickness_mm']} mm; status={thickness['reason']}")
        if args.depth_out:
            sample = depth_frames[selected_index]
            with open(args.depth_out, "wb") as stream:
                np.savez_compressed(stream, depth_mm=sample["depth"], intrinsics=sample["intrinsics"])
            result["thickness"]["depth_file"] = str(args.depth_out)

    if args.raw_image and debug_frame is not None:
        save_raw_image(args.raw_image, debug_frame)
        print(f"Saved raw image -> {args.raw_image}")

    if args.debug_image and debug_frame is not None:
        save_debug_image(args.debug_image, debug_frame, pose, args.kpt_conf, selected["thickness"])
        print(f"Saved debug image -> {args.debug_image}")

    write_json(args.json_out, result)
    print(f"Saved {args.json_out}")

    if result["legs"]:
        for row in result["legs"]:
            value = row["total_mm"] if args.calibration else row["total_px"]
            unit = "mm" if args.calibration else "px"
            print(
                f"{row['leg']}: {value:.2f}{unit} "
                f"reliable={row['reliable']} min_conf={row['min_conf']:.2f} reason={row['reason']}"
            )

    if args.serial_port:
        if not result["legs"] or not result["measurement_ok"]:
            raise RuntimeError(f"No reliable leg measurements to send. reasons={result['measurement_reasons']}")
        weight_g = None if weight is None else weight["weight_g"]
        packet = build_rs485_packet(args.seq, result["legs"], use_mm=bool(args.calibration), weight_g=weight_g)
        send_rs485(args.serial_port, args.baud, packet)
        print(f"Sent {len(packet)} bytes to {args.serial_port}: {packet.hex(' ')}")


if __name__ == "__main__":
    main()
