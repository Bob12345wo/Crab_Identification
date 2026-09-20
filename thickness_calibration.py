"""Bounded calibration for OAK support-plane thickness measurements."""

import json
from pathlib import Path

import numpy as np


CALIBRATION_VERSION = 1


class CalibrationError(ValueError):
    """Raised when a thickness calibration profile is not usable."""


def _finite_float(value, name):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise CalibrationError(f"{name} must be a finite number") from exc
    if not np.isfinite(result):
        raise CalibrationError(f"{name} must be a finite number")
    return result


def _normalize_point(point, index):
    if not isinstance(point, dict):
        raise CalibrationError(f"Calibration point {index} must be an object")
    actual = _finite_float(point.get("actual_mm"), f"point {index} actual_mm")
    measured = _finite_float(point.get("measured_mm"), f"point {index} measured_mm")
    if actual <= 0 or measured <= 0:
        raise CalibrationError(f"Calibration point {index} must be positive")
    normalized = {"actual_mm": actual, "measured_mm": measured}
    source = point.get("source")
    if source:
        normalized["source"] = str(source)
    return normalized


def validate_calibration(data):
    """Validate and normalize a persisted calibration profile."""
    if not isinstance(data, dict):
        raise CalibrationError("Thickness calibration must be a JSON object")
    if int(data.get("version", -1)) != CALIBRATION_VERSION:
        raise CalibrationError(f"Unsupported thickness calibration version: {data.get('version')}")
    if data.get("model") != "linear":
        raise CalibrationError("Thickness calibration model must be 'linear'")
    if data.get("enabled", True) is not True:
        raise CalibrationError("Thickness calibration profile is disabled")

    scale = _finite_float(data.get("scale"), "scale")
    offset = _finite_float(data.get("offset_mm"), "offset_mm")
    if scale <= 0:
        raise CalibrationError("scale must be positive")

    raw_range = data.get("valid_raw_range_mm")
    if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
        raise CalibrationError("valid_raw_range_mm must contain two values")
    raw_min = _finite_float(raw_range[0], "valid_raw_range_mm[0]")
    raw_max = _finite_float(raw_range[1], "valid_raw_range_mm[1]")
    if raw_min <= 0 or raw_min >= raw_max:
        raise CalibrationError("valid_raw_range_mm must be positive and increasing")

    actual_range = data.get("valid_actual_range_mm", [scale * raw_min + offset, scale * raw_max + offset])
    if not isinstance(actual_range, (list, tuple)) or len(actual_range) != 2:
        raise CalibrationError("valid_actual_range_mm must contain two values")
    actual_min = _finite_float(actual_range[0], "valid_actual_range_mm[0]")
    actual_max = _finite_float(actual_range[1], "valid_actual_range_mm[1]")
    if actual_min <= 0 or actual_min >= actual_max:
        raise CalibrationError("valid_actual_range_mm must be positive and increasing")

    points = data.get("reference_points", [])
    if not isinstance(points, list) or len(points) < 2:
        raise CalibrationError("reference_points must contain at least two points")
    normalized_points = [_normalize_point(point, index) for index, point in enumerate(points)]

    result = dict(data)
    result.update(
        version=CALIBRATION_VERSION,
        enabled=True,
        model="linear",
        scale=scale,
        offset_mm=offset,
        valid_raw_range_mm=[raw_min, raw_max],
        valid_actual_range_mm=[actual_min, actual_max],
        allow_extrapolation=bool(data.get("allow_extrapolation", False)),
        reference_points=normalized_points,
    )
    return result


def load_calibration(path):
    """Load and validate a calibration profile from JSON."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"Could not read thickness calibration {path}: {exc}") from exc
    result = validate_calibration(data)
    result["source_file"] = str(path)
    return result


def fit_linear_calibration(points):
    """Fit actual_mm = scale * measured_mm + offset_mm."""
    if not isinstance(points, list) or len(points) < 2:
        raise CalibrationError("At least two reference points are required")
    normalized = [_normalize_point(point, index) for index, point in enumerate(points)]
    measured = np.asarray([point["measured_mm"] for point in normalized], dtype=np.float64)
    actual = np.asarray([point["actual_mm"] for point in normalized], dtype=np.float64)
    order = np.argsort(measured)
    sorted_measured = measured[order]
    sorted_actual = actual[order]
    if np.any(np.diff(sorted_measured) <= 1e-9):
        raise CalibrationError("Measured calibration values must be distinct")
    if np.any(np.diff(sorted_actual) <= 0):
        raise CalibrationError("Actual calibration values must increase with measured values")

    design = np.column_stack((measured, np.ones_like(measured)))
    scale, offset = np.linalg.lstsq(design, actual, rcond=None)[0]
    scale = float(scale)
    offset = float(offset)
    if not np.isfinite(scale) or not np.isfinite(offset) or scale <= 0:
        raise CalibrationError("Calibration fit must have a finite positive scale")

    predicted = scale * measured + offset
    residuals = predicted - actual
    return validate_calibration(
        {
            "version": CALIBRATION_VERSION,
            "enabled": True,
            "model": "linear",
            "scale": scale,
            "offset_mm": offset,
            "valid_raw_range_mm": [float(sorted_measured[0]), float(sorted_measured[-1])],
            "valid_actual_range_mm": [float(sorted_actual[0]), float(sorted_actual[-1])],
            "allow_extrapolation": False,
            "reference_points": normalized,
            "fit": {
                "point_count": len(normalized),
                "rmse_mm": float(np.sqrt(np.mean(residuals ** 2))),
                "max_abs_residual_mm": float(np.max(np.abs(residuals))),
            },
        }
    )


def apply_thickness_calibration(raw_thickness_mm, calibration=None):
    """Return calibration status and a bounded corrected thickness."""
    raw = None
    if raw_thickness_mm is not None:
        try:
            candidate = float(raw_thickness_mm)
        except (TypeError, ValueError):
            candidate = np.nan
        if np.isfinite(candidate):
            raw = candidate

    base = {
        "enabled": calibration is not None,
        "ok": calibration is None,
        "status": "disabled" if calibration is None else "no_raw_measurement",
        "raw_thickness_mm": raw,
        "calibrated_thickness_mm": None,
        "reported_thickness_mm": raw if calibration is None else None,
    }
    if calibration is None:
        return base

    config = validate_calibration(calibration)
    raw_min, raw_max = config["valid_raw_range_mm"]
    base.update(
        model=config["model"],
        scale=config["scale"],
        offset_mm=config["offset_mm"],
        valid_raw_range_mm=config["valid_raw_range_mm"],
        valid_actual_range_mm=config["valid_actual_range_mm"],
        allow_extrapolation=config["allow_extrapolation"],
    )
    if "source_file" in config:
        base["source_file"] = config["source_file"]
    if raw is None:
        return base

    in_range = raw_min <= raw <= raw_max
    if not in_range and not config["allow_extrapolation"]:
        base["status"] = "below_calibration_range" if raw < raw_min else "above_calibration_range"
        return base

    calibrated = config["scale"] * raw + config["offset_mm"]
    base.update(
        ok=True,
        status="ok" if in_range else "extrapolated",
        calibrated_thickness_mm=float(calibrated),
        reported_thickness_mm=float(calibrated),
    )
    return base


def attach_thickness_calibration(result, calibration=None, max_error_mm=None):
    """Add raw/corrected values to a thickness result without hiding raw quality."""
    raw = result.get("thickness_mm")
    raw_ok = bool(result.get("ok"))
    raw_quality_reasons = list(result.get("quality_reasons") or [])
    result["raw_ok"] = raw_ok
    result["raw_quality_reasons"] = raw_quality_reasons
    details = apply_thickness_calibration(raw, calibration)
    result["raw_thickness_mm"] = details["raw_thickness_mm"]
    result["calibrated_thickness_mm"] = details["calibrated_thickness_mm"]
    result["reported_thickness_mm"] = details["reported_thickness_mm"]
    result["thickness_calibration"] = details

    if calibration is not None and details["ok"] and details["calibrated_thickness_mm"] is not None:
        expected = result.get("expected_height_mm")
        if expected is not None:
            expected = _finite_float(expected, "expected_height_mm")
            calibrated_error = details["calibrated_thickness_mm"] - expected
            result["calibrated_error_mm"] = float(calibrated_error)
            result["calibrated_absolute_error_mm"] = float(abs(calibrated_error))
            if max_error_mm is not None:
                max_error = _finite_float(max_error_mm, "max_error_mm")
                result["calibration_reference_ok"] = abs(calibrated_error) <= max_error
                quality_reasons = [
                    reason for reason in raw_quality_reasons
                    if reason != "reference_error_too_high"
                ]
                if abs(calibrated_error) > max_error:
                    quality_reasons.append("calibrated_reference_error_too_high")
                result["quality_reasons"] = quality_reasons
                result["reason"] = "ok" if not quality_reasons else ";".join(quality_reasons)
                result["ok"] = not quality_reasons
        return result

    if calibration is not None and raw is not None and not details["ok"]:
        reason = "thickness_calibration_out_of_range"
        quality_reasons = list(result.get("quality_reasons") or [])
        if reason not in quality_reasons:
            quality_reasons.append(reason)
        result["quality_reasons"] = quality_reasons
        result["reason"] = ";".join(quality_reasons)
        result["ok"] = False
    return result
