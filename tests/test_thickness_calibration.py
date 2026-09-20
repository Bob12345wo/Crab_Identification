import json
from pathlib import Path
import tempfile
import unittest

from calibrate_thickness import read_reference
from onenet_mqtt_upload import build_properties
from test_depth_block import summarize_frame_results
from thickness_calibration import (
    CalibrationError,
    apply_thickness_calibration,
    attach_thickness_calibration,
    fit_linear_calibration,
)


class ThicknessCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.profile = fit_linear_calibration(
            [
                {
                    "actual_mm": 20.0,
                    "measured_mm": 18.562464754480683,
                    "source": "block_20mm_result.json",
                },
                {
                    "actual_mm": 30.0,
                    "measured_mm": 26.077023680015976,
                    "source": "block_30mm_result.json",
                },
            ]
        )

    def test_reference_points_are_corrected_to_known_heights(self):
        first = apply_thickness_calibration(18.562464754480683, self.profile)
        second = apply_thickness_calibration(26.077023680015976, self.profile)
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertAlmostEqual(first["calibrated_thickness_mm"], 20.0, places=8)
        self.assertAlmostEqual(second["calibrated_thickness_mm"], 30.0, places=8)

    def test_calibration_does_not_extrapolate_by_default(self):
        result = apply_thickness_calibration(10.0, self.profile)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "below_calibration_range")
        self.assertIsNone(result["reported_thickness_mm"])

    def test_out_of_range_calibration_invalidates_reported_measurement(self):
        result = attach_thickness_calibration(
            {"ok": True, "reason": "ok", "quality_reasons": [], "thickness_mm": 30.0},
            self.profile,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "thickness_calibration_out_of_range")
        self.assertAlmostEqual(result["raw_thickness_mm"], 30.0)
        self.assertIsNone(result["reported_thickness_mm"])

    def test_calibrated_reference_error_replaces_raw_bias_failure(self):
        result = attach_thickness_calibration(
            {
                "ok": False,
                "reason": "reference_error_too_high",
                "quality_reasons": ["reference_error_too_high"],
                "expected_height_mm": 30.0,
                "thickness_mm": 26.077023680015976,
            },
            self.profile,
            max_error_mm=2.0,
        )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["raw_ok"])
        self.assertEqual(result["raw_quality_reasons"], ["reference_error_too_high"])
        self.assertAlmostEqual(result["calibrated_absolute_error_mm"], 0.0, places=8)
        self.assertEqual(result["reason"], "ok")

    def test_onenet_uses_reported_calibrated_thickness(self):
        properties = build_properties(
            {
                "measurement_id": "test",
                "measurement_ok": True,
                "legs": [],
                "thickness": {
                    "ok": True,
                    "thickness_mm": 26.077,
                    "reported_thickness_mm": 30.0,
                },
            },
            "image.jpg",
            "",
            "raw",
        )
        self.assertEqual(properties["thickness_ok"], 1)
        self.assertEqual(properties["thickness_mm"], 30.0)

    def test_reference_error_warning_is_allowed_for_bias_fit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "block.json"
            path.write_text(
                json.dumps(
                    {
                        "expected_height_mm": 30,
                        "thickness_mm": 26,
                        "quality_reasons": ["reference_error_too_high"],
                        "valid_frame_ratio": 1.0,
                        "range_mm": 3.0,
                        "plane_rmse_mm": 1.0,
                        "valid_depth_ratio": 1.0,
                        "plane_inlier_ratio": 0.99,
                    }
                ),
                encoding="utf-8",
            )
            point = read_reference(path, 0.8, 5.0, 3.0, 0.6, 0.7)
            self.assertEqual(point["actual_mm"], 30.0)
            self.assertIn("warning", point)

    def test_calibration_rejects_duplicate_measured_values(self):
        with self.assertRaises(CalibrationError):
            fit_linear_calibration(
                [
                    {"actual_mm": 20, "measured_mm": 18},
                    {"actual_mm": 30, "measured_mm": 18},
                ]
            )

    def test_block_summary_exposes_depth_resolution_failure(self):
        result = summarize_frame_results(
            [
                {"ok": False, "thickness_mm": None, "reason": "thickness_below_depth_resolution"},
                {"ok": False, "thickness_mm": None, "reason": "thickness_below_depth_resolution"},
            ],
            expected_height_mm=10.0,
            min_valid_ratio=0.8,
            max_frame_spread_mm=5.0,
            max_error_mm=2.0,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "thickness_below_depth_resolution")


if __name__ == "__main__":
    unittest.main()
