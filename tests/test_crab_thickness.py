import argparse
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np

from crab_thickness import add_depth_arguments, measure_thickness, validate_depth_arguments


class ThicknessTests(unittest.TestCase):
    def setUp(self):
        self.matrix = np.array([[220., 0, 100], [0, 220., 100], [0, 0, 1]])
        self.bbox = [55, 55, 145, 145]

    def scene(self, height=35., tilt=0.):
        yy, xx = np.indices((200, 200))
        rays = np.stack(((xx - 100) / 220, (yy - 100) / 220, np.ones_like(xx)), axis=-1)
        normal = np.array([tilt, 0, -1.])
        normal /= np.linalg.norm(normal)
        depth = -650 / (rays @ normal)
        body = (xx >= 55) & (xx <= 145) & (yy >= 55) & (yy <= 145)
        depth[body] = (height - 650) / (rays[body] @ normal)
        return depth

    def test_flat_and_tilted_plane_metric_height(self):
        for tilt in (0., 0.4, -0.4):
            with self.subTest(tilt=tilt):
                result = measure_thickness(self.scene(35, tilt), self.matrix, self.bbox)
                self.assertTrue(result['ok'], result)
                self.assertAlmostEqual(result['thickness_mm'], 35, places=5)

    def test_holes_and_sparse_outliers(self):
        depth = self.scene()
        rng = np.random.default_rng(3)
        depth[rng.random(depth.shape) < 0.1] = 0
        depth[rng.random(depth.shape) < 0.03] = 300
        result = measure_thickness(depth, self.matrix, self.bbox)
        self.assertTrue(result['ok'], result)
        self.assertAlmostEqual(result['thickness_mm'], 35, places=5)

    def test_missing_support(self):
        result = measure_thickness(np.zeros((200, 200)), self.matrix, self.bbox)
        self.assertFalse(result['ok'])
        self.assertIsNone(result['thickness_mm'])

    def test_missing_shell(self):
        depth = self.scene()
        depth[80:120, 80:120] = np.nan
        result = measure_thickness(depth, self.matrix, self.bbox)
        self.assertEqual(result['reason'], 'insufficient_shell_depth')

    def test_empty_plane_is_not_zero_thickness(self):
        result = measure_thickness(self.scene(0), self.matrix, self.bbox)
        self.assertEqual(result['reason'], 'thickness_below_depth_resolution')
        self.assertEqual(result['resolution_status'], 'below_depth_resolution')
        self.assertEqual(result['legacy_reason'], 'shell_not_separated_from_support')
        self.assertIsNone(result['thickness_mm'])

    def test_excessive_height_is_rejected(self):
        result = measure_thickness(self.scene(200), self.matrix, self.bbox)
        self.assertEqual(result['reason'], 'height_out_of_range')

    def test_nonplanar_background(self):
        depth = np.random.default_rng(2).uniform(400, 800, (200, 200))
        result = measure_thickness(depth, self.matrix, self.bbox)
        self.assertEqual(result['reason'], 'support_not_planar')

    def test_invalid_geometry(self):
        for box in ([0, 0, 0, 0], [0, 0, 201, 200], [0, 0, float('nan'), 20]):
            self.assertFalse(measure_thickness(self.scene(), self.matrix, box)['ok'])
        self.assertFalse(measure_thickness(self.scene(), np.zeros((3, 3)), self.bbox)['ok'])

    def test_default_arguments(self):
        parser = argparse.ArgumentParser()
        add_depth_arguments(parser)
        args = parser.parse_args(['--depth'])
        validate_depth_arguments(parser, args)
        self.assertTrue(args.depth)

    def test_thickness_calibration_requires_depth(self):
        with tempfile.TemporaryDirectory() as directory:
            calibration = Path(directory) / 'calibration.json'
            calibration.write_text('{}', encoding='utf-8')
            parser = argparse.ArgumentParser()
            add_depth_arguments(parser)
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    validate_depth_arguments(
                        parser,
                        parser.parse_args(['--thickness-calibration', str(calibration)]),
                    )
            self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
