"""Estimate support-plane-to-shell height from RGB-aligned metric depth."""

from pathlib import Path

import numpy as np


def add_depth_arguments(parser):
    parser.add_argument("--depth", action="store_true", help="Measure shell height using OAK stereo depth")
    parser.add_argument("--depth-body-scale", type=float, default=0.35,
                        help="Central fraction of detection bbox used as shell ROI (0, 1]")
    parser.add_argument("--depth-max-height-mm", type=float, default=150.0)
    parser.add_argument("--depth-plane-tolerance-mm", type=float, default=3.0)
    parser.add_argument(
        "--thickness-calibration",
        default=None,
        help="Bounded linear calibration JSON for OAK thickness (raw depth remains in the report)",
    )


def validate_depth_arguments(parser, args):
    if not 0 < args.depth_body_scale <= 1:
        parser.error("--depth-body-scale must be in (0, 1]")
    for name in ("depth_max_height_mm", "depth_plane_tolerance_mm"):
        value = getattr(args, name)
        if not np.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    if args.thickness_calibration and not Path(args.thickness_calibration).is_file():
        parser.error(f"Thickness calibration file does not exist: {args.thickness_calibration}")


def measure_thickness(depth, intrinsics, bbox, body_scale=0.35,
                      max_height_mm=150.0, plane_tolerance_mm=3.0):
    result = {"ok": False, "thickness_mm": None, "reason": "invalid_depth",
              "method": "support_plane_to_shell_p90", "unit": "mm"}
    depth = np.asarray(depth, dtype=np.float64)
    matrix = np.asarray(intrinsics, dtype=np.float64)
    if (depth.ndim != 2 or matrix.shape != (3, 3) or not np.isfinite(matrix).all()
            or abs(np.linalg.det(matrix)) < 1e-9 or matrix[0, 0] <= 0 or matrix[1, 1] <= 0):
        return result
    if (not 0 < body_scale <= 1 or not np.isfinite(max_height_mm) or max_height_mm <= 0
            or not np.isfinite(plane_tolerance_mm) or plane_tolerance_mm <= 0):
        raise ValueError("Invalid thickness measurement settings")
    height, width = depth.shape
    box = np.asarray(bbox, dtype=float)
    if box.shape != (4,) or not np.isfinite(box).all():
        result["reason"] = "invalid_bbox"
        return result
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        result["reason"] = "invalid_bbox"
        return result
    yy, xx = np.indices(depth.shape)
    valid = np.isfinite(depth) & (depth >= 100) & (depth <= 5000)
    # Exclude the whole crab, with a margin, from support-plane estimation.
    margin = max(5, int(0.03 * max(width, height)))
    background = ((xx < x1 - margin) | (xx > x2 + margin)
                  | (yy < y1 - margin) | (yy > y2 + margin))
    background &= (xx >= margin) & (xx < width - margin) & (yy >= margin) & (yy < height - margin)
    if np.count_nonzero(background & valid) < 200:
        result["reason"] = "insufficient_support_depth"
        return result

    def project(mask):
        pixels = np.column_stack((xx[mask], yy[mask], np.ones(np.count_nonzero(mask))))
        rays = pixels @ np.linalg.inv(matrix).T
        return rays * (depth[mask] / rays[:, 2])[:, None]

    points = project(background & valid)
    rng = np.random.default_rng(0)
    if len(points) > 4000:
        points = points[rng.choice(len(points), 4000, replace=False)]
    best = np.zeros(len(points), dtype=bool)
    for _ in range(100):
        a, b, c = points[rng.choice(len(points), 3, replace=False)]
        normal = np.cross(b - a, c - a)
        length = np.linalg.norm(normal)
        if length < 1e-6:
            continue
        normal /= length
        inliers = np.abs((points - a) @ normal) <= plane_tolerance_mm
        if np.count_nonzero(inliers) > np.count_nonzero(best):
            best = inliers
    ratio = float(np.mean(best))
    result["plane_inlier_ratio"] = ratio
    if ratio < 0.7:
        result["reason"] = "support_not_planar"
        return result
    support = points[best]
    center = support.mean(axis=0)
    _, singular, vectors = np.linalg.svd(support - center, full_matrices=False)
    normal = vectors[-1]
    if normal[2] > 0:
        normal = -normal
    residual = float(np.sqrt(np.mean(((support - center) @ normal) ** 2)))
    result.update(plane_normal=normal.tolist(), plane_offset_mm=float(-center @ normal),
                  plane_rmse_mm=residual)
    if singular[1] < 20 or abs(normal[2]) < 0.5 or residual > plane_tolerance_mm:
        result["reason"] = "unstable_support_plane"
        return result
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    half_w, half_h = (x2 - x1) * body_scale / 2, (y2 - y1) * body_scale / 2
    body = (np.abs(xx - cx) <= half_w) & (np.abs(yy - cy) <= half_h)
    count = int(np.count_nonzero(body & valid))
    valid_ratio = count / max(1, np.count_nonzero(body))
    result.update(body_roi_xyxy=[cx - half_w, cy - half_h, cx + half_w, cy + half_h],
                  valid_depth_ratio=float(valid_ratio), valid_depth_pixels=count)
    if count < 100 or valid_ratio < 0.6:
        result["reason"] = "insufficient_shell_depth"
        return result
    elevations = (project(body & valid) - center) @ normal
    # Do not discard out-of-range points before calculating the upper quantile.
    value = float(np.percentile(elevations, 90))
    resolution_limit = plane_tolerance_mm * 2
    separation_ratio = float(np.mean(elevations > resolution_limit))
    result["surface_depth_mm"] = float(np.median(depth[body & valid]))
    result["surface_elevation_p90_mm"] = value
    result["resolution_limit_mm"] = float(resolution_limit)
    result["shell_separation_ratio"] = separation_ratio
    if separation_ratio < 0.5:
        result.update(
            reason="thickness_below_depth_resolution",
            resolution_status="below_depth_resolution",
            legacy_reason="shell_not_separated_from_support",
        )
    elif not plane_tolerance_mm * 2 < value <= max_height_mm:
        result["reason"] = "height_out_of_range"
        result["resolution_status"] = "resolved_but_out_of_range"
    else:
        result.update(ok=True, thickness_mm=value, reason="ok", resolution_status="resolved")
    return result
