"""Analyze repeated crab measurement JSON files without external dependencies."""

import argparse
import csv
import json
import statistics
from pathlib import Path


LEG_ORDER = [
    "L-Claw",
    "L-Tleg",
    "L-Aleg",
    "L-Bleg",
    "L-Cleg",
    "R-Claw",
    "R-Tleg",
    "R-Aleg",
    "R-Bleg",
    "R-Cleg",
]


def describe(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": None, "std": None, "cv_pct": None, "min": None, "max": None, "range_pct": None}
    mean = statistics.fmean(values)
    std = statistics.stdev(values) if len(values) > 1 else 0.0
    value_range = max(values) - min(values)
    return {
        "count": len(values),
        "mean": mean,
        "std": std,
        "cv_pct": 100.0 * std / mean if mean else None,
        "min": min(values),
        "max": max(values),
        "range_pct": 100.0 * value_range / mean if mean else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--csv-out", default=None)
    parser.add_argument("--max-cv-pct", type=float, default=2.0)
    parser.add_argument("--max-range-pct", type=float, default=5.0)
    parser.add_argument("--min-reliable-rate", type=float, default=0.95)
    parser.add_argument("--min-samples", type=int, default=5)
    parser.add_argument("--max-thickness-cv-pct", type=float, default=3.0)
    parser.add_argument("--max-thickness-range-pct", type=float, default=8.0)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    files = sorted(input_dir.glob("measurement_*.json"))
    if not files:
        raise SystemExit(f"No measurement_*.json files found in {input_dir}")

    measurements = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    if args.min_samples < 2:
        raise SystemExit("--min-samples must be at least 2")
    unit = measurements[0].get("unit", "px")
    value_key = "total_mm" if unit == "mm" else "total_px"
    run_count = len(measurements)
    measurement_ok_count = sum(bool(row.get("measurement_ok")) for row in measurements)

    report = {
        "input_dir": str(input_dir),
        "run_count": run_count,
        "unit": unit,
        "measurement_ok_count": measurement_ok_count,
        "measurement_ok_rate": measurement_ok_count / run_count,
        "thresholds": {
            "max_cv_pct": args.max_cv_pct,
            "max_range_pct": args.max_range_pct,
            "min_reliable_rate": args.min_reliable_rate,
            "min_samples": args.min_samples,
            "max_thickness_cv_pct": args.max_thickness_cv_pct,
            "max_thickness_range_pct": args.max_thickness_range_pct,
        },
        "legs": [],
    }

    for leg_name in LEG_ORDER:
        all_rows = []
        for measurement in measurements:
            row = next((item for item in measurement.get("legs", []) if item.get("leg") == leg_name), None)
            if row is not None and row.get(value_key) is not None:
                all_rows.append(row)
        reliable_rows = [row for row in all_rows if row.get("reliable")]
        stats = describe([float(row[value_key]) for row in reliable_rows])
        reliable_rate = len(reliable_rows) / run_count
        passed = (
            reliable_rate >= args.min_reliable_rate
            and stats["cv_pct"] is not None
            and stats["cv_pct"] <= args.max_cv_pct
            and stats["range_pct"] is not None
            and stats["range_pct"] <= args.max_range_pct
        )
        report["legs"].append(
            {
                "leg": leg_name,
                "reliable_count": len(reliable_rows),
                "reliable_rate": reliable_rate,
                **stats,
                "passed": passed,
            }
        )

    weights = [
        float(row["weight"]["weight_g"])
        for row in measurements
        if row.get("weight") and row["weight"].get("weight_g") is not None
    ]
    report["weight"] = describe(weights)
    report["weight"]["usable_rate"] = len(weights) / run_count
    thicknesses = [float((row.get("thickness") or {})["thickness_mm"]) for row in measurements if (row.get("thickness") or {}).get("ok") and (row.get("thickness") or {}).get("thickness_mm") is not None]
    report["thickness"] = describe(thicknesses)
    report["thickness"]["usable_rate"] = len(thicknesses) / run_count
    has_thickness = any(row.get("thickness") is not None for row in measurements)
    thickness_passed = (not has_thickness or (len(thicknesses) >= args.min_samples and report["thickness"]["cv_pct"] is not None and report["thickness"]["cv_pct"] <= args.max_thickness_cv_pct and report["thickness"]["range_pct"] is not None and report["thickness"]["range_pct"] <= args.max_thickness_range_pct))
    report["passed"] = (run_count >= args.min_samples and report["measurement_ok_rate"] >= args.min_reliable_rate and all(row["passed"] for row in report["legs"]) and thickness_passed)

    json_out = Path(args.json_out) if args.json_out else input_dir / "repeatability_report.json"
    csv_out = Path(args.csv_out) if args.csv_out else input_dir / "repeatability_report.csv"
    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["leg", "reliable_count", "reliable_rate", "count", "mean", "std", "cv_pct", "min", "max", "range_pct", "passed"],
        )
        writer.writeheader()
        writer.writerows(report["legs"])

    print(f"runs={run_count} measurement_ok={measurement_ok_count}/{run_count} ({report['measurement_ok_rate']:.1%}) unit={unit}")
    for row in report["legs"]:
        mean = "-" if row["mean"] is None else f"{row['mean']:.2f}"
        cv = "-" if row["cv_pct"] is None else f"{row['cv_pct']:.2f}%"
        span = "-" if row["range_pct"] is None else f"{row['range_pct']:.2f}%"
        print(
            f"{row['leg']:7s} reliable={row['reliable_count']:2d}/{run_count} "
            f"mean={mean:>8s}{unit} cv={cv:>7s} range={span:>7s} pass={row['passed']}"
        )
    print(f"overall_pass={report['passed']}")
    if report["thickness"]["count"]:
        print(f"thickness usable={report['thickness']['count']}/{run_count} mean={report['thickness']['mean']:.2f}mm cv={report['thickness']['cv_pct']:.2f}% range={report['thickness']['range_pct']:.2f}% pass={thickness_passed}")
    print(f"saved {json_out}")
    print(f"saved {csv_out}")


if __name__ == "__main__":
    main()
