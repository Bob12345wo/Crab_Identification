import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import oak_crab_measure as measure


class DepthIntegrationTests(unittest.TestCase):
    def capture(self, *args, depth_frames=None, capture_timeout=30.0):
        if depth_frames is not None:
            for value in (0, 615):
                depth = np.full((200, 200), 650, dtype=np.uint16)
                depth[55:146, 55:146] = value
                depth_frames.append({'depth': depth,
                                     'intrinsics': np.array([[220., 0, 100], [0, 220., 100], [0, 0, 1]]),
                                     'timestamp_delta_ms': 1.0})
        return [(np.zeros(1), np.zeros((200, 200, 3), dtype=np.uint8)) for _ in range(2)], {}

    def run_measure(self, directory, enabled):
        output = Path(directory) / 'result.json'
        archive = Path(directory) / 'depth.npz'
        argv = ['measure', '--json-out', str(output)]
        if enabled:
            argv += ['--depth', '--depth-out', str(archive)]
        pose = {'ok': True, 'score': 0.9, 'bbox_xyxy': [55, 55, 145, 145],
                'keypoints': [[80, 80, 0.9]] * 48}
        legs = [{'leg': str(i), 'reliable': True, 'total_mm': None, 'total_px': 20,
                 'min_conf': .9, 'reason': 'ok'} for i in range(10)]
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, 'argv', argv))
            stack.enter_context(patch.object(measure, 'run_oak_frames', side_effect=self.capture))
            stack.enter_context(patch.object(measure, 'decode_pose', return_value=pose))
            stack.enter_context(patch.object(measure, 'assess_pose_quality', return_value={'ok': True, 'reasons': []}))
            stack.enter_context(patch.object(measure, 'measure_legs', return_value=legs))
            stack.enter_context(patch.object(measure, 'assess_leg_geometry'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            measure.main()
        return json.loads(output.read_text()), archive

    def test_selects_valid_depth_and_saves_same_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            result, archive = self.run_measure(directory, True)
            self.assertTrue(result['measurement_ok'])
            self.assertEqual(result['capture']['selected_frame_index'], 1)
            self.assertAlmostEqual(result['thickness']['thickness_mm'], 35)
            with np.load(archive) as sample:
                self.assertEqual(sample['depth_mm'][100, 100], 615)
                self.assertEqual(sample['intrinsics'].shape, (3, 3))

    def test_disabled_depth_preserves_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            result, archive = self.run_measure(directory, False)
            self.assertTrue(result['measurement_ok'])
            self.assertIsNone(result['thickness'])
            self.assertFalse(archive.exists())

    def test_depth_failure_invalidates_measurement(self):
        failure = {'ok': False, 'reason': 'insufficient_shell_depth', 'thickness_mm': None}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(measure, 'measure_thickness', return_value=failure):
                result, _ = self.run_measure(directory, True)
            self.assertFalse(result['measurement_ok'])
            self.assertIn('thickness:insufficient_shell_depth', result['measurement_reasons'])
            self.assertIsNone(result['thickness']['thickness_mm'])


if __name__ == '__main__':
    unittest.main()
