"""Build a bounded OAK thickness calibration from standard-block reports."""

import argparse
import json
from pathlib import Path

import numpy as np

from thickness_calibration import CalibrationError, fit_linear_calibration, validate_calibration


ALLOWED_REFERENCE_WARNINGS = {"reference_error_too_high"}


def read_reference(path, min_valid_ratio, max_frame_spread_mm, max_plane_rmse_mm,
                   min_valid_depth_ratio, min_plane_inlier_ratio):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"Could not read reference report {path}: {exc}") from exc

    actual = data.get("expected_height_mm")
    measured = data.get("thickness_mm")
    try:
        actual = float(actual)
        measured = float(measured)
    except (TypeError, ValueError) as exc:
        raise CalibrationError(f"{path} must contain finite expected_height_mm and thickness_mm") from exc
    if not np.isfinite(actual) or not np.isfinite(measured) or actual <= 0 or measured <= 0:
        raise CalibrationError(f"{path} must contain positive expected_height_mm and thickness_mm")

    checks = (
        ("valid_frame_ratio", data.get("valid_frame_ratio"), min_valid_ratio),
        ("range_mm", data.get("range_mm"), max_frame_spread_mm),
        ("plane_rmse_mm", data.get("plane_rmse_mm"), max_plane_rmse_mm),
        ("valid_depth_ratio", data.get("valid_depth_ratio"), min_valid_depth_ratio),
        ("plane_inlier_ratio", data.get("plane_inlier_ratio"), min_plane_inlier_ratio),
    )
    failures = []
    for name, value, limit in checks:
        try:
            value = float(value)
        except (TypeError, ValueError):
            failures.append(f"{name}=missing")
            continue
        if not np.isfinite(value):
            failures.append(f"{name}=nonfinite")
        elif name in {"range_mm", "plane_rmse_mm"} and value > limit:
            failures.append(f"{name}={value:.3f}>{limit:.3f}")
        elif name in {"valid_frame_ratio", "valid_depth_ratio", "plane_inlier_ratio"} and value < limit:
            failures.append(f"{name}={value:.3f}<{limit:.3f}")
    if failures:
        raise CalibrationError(f"{path} does not meet structural quality limits: {', '.join(failures)}")

    reasons = set(data.get("quality_reasons") or [])
    unexpected = sorted(reasons - ALLOWED_REFERENCE_WARNINGS)
    if unexpected:
        raise CalibrationError(
            f"{path} has unusable quality reasons: {', '.join(unexpected)}; "
            "collect a better standard-block sample before calibrating"
        )

    point = {
        "actual_mm": actual,
        "measured_mm": measured,
        "source": str(path),
    }
    if "reference_error_too_high" in reasons:
        point["warning"] = "reference_error_too_high_accepted_for_systematic_bias_fit"
    return point


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", action="append", required=True,
                        help="Standard-block JSON report; repeat for each known height")
    parser.add_argument("--output", type=Path, default=Path("depth_thickness_calibration.json"))
    parser.add_argument("--min-valid-ratio", type=float, default=0.8)
    parser.add_argument("--max-frame-spread-mm", type=float, default=5.0)
    parser.add_argument("--max-plane-rmse-mm", type=float, default=3.0)
    parser.add_argument("--min-valid-depth-ratio", type=float, default=0.6)
    parser.add_argument("--min-plane-inlier-ratio", type=float, default=0.7)
    parser.add_argument("--allow-extrapolation", action="store_true",
                        help="Permit values outside the measured raw range; disabled by default")
    args = parser.parse_args()

    if len(args.result) < 2:
        parser.error("At least two --result reports are required")
    if not 0 < args.min_valid_ratio <= 1:
        parser.error("--min-valid-ratio must be in (0, 1]")
    if args.max_frame_spread_mm <= 0 or args.max_plane_rmse_mm <= 0:
        parser.error("Frame spread and plane RMSE limits must be positive")
    if not 0 < args.min_valid_depth_ratio <= 1 or not 0 < args.min_plane_inlier_ratio <= 1:
        parser.error("Depth and plane ratios must be in (0, 1]")

    try:
        points = [
            read_reference(
                path,
                args.min_valid_ratio,
                args.max_frame_spread_mm,
                args.max_plane_rmse_mm,
                args.min_valid_depth_ratio,
                args.min_plane_inlier_ratio,
            )
            for path in args.result
        ]
        profile = fit_linear_calibration(points)
        profile["allow_extrapolation"] = bool(args.allow_extrapolation)
        profile["calibration_scope"] = (
            "Valid only for the same OAK device, mounting position, RGB/depth ROI, "
            "resolution, depth settings, and measurement plane used for the reference blocks."
        )
        profile["quality_warnings"] = [
            point["warning"] for point in points if point.get("warning")
        ]
        profile = validate_calibration(profile)
    except CalibrationError as exc:
        parser.error(str(exc))

    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    if len(points) == 2:
        print("warning: two reference points fit a line exactly; add a third height before claiming nonlinearity is calibrated")


if __name__ == "__main__":
    main()
