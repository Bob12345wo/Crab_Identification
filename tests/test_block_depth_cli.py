import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import test_depth_block


class DepthBlockTests(unittest.TestCase):
    def test_offline_known_height(self):
        with tempfile.TemporaryDirectory() as directory:
            depth_path = Path(directory) / 'depth.npz'
            report_path = Path(directory) / 'result.json'
            depth = np.full((200, 200), 650, dtype=np.uint16)
            depth[55:146, 55:146] = 625
            intrinsics = np.array([[220., 0, 100], [0, 220., 100], [0, 0, 1]])
            np.savez_compressed(depth_path, depth_mm=depth, intrinsics=intrinsics)
            argv = ['test_depth_block.py', '--depth', str(depth_path), '--output', str(report_path),
                    '--bbox', '55', '55', '145', '145', '--height-mm', '25']
            with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
                test_depth_block.main()
            result = json.loads(report_path.read_text(encoding='utf-8'))
            self.assertTrue(result['ok'])
            self.assertAlmostEqual(result['thickness_mm'], 25, delta=0.1)
            self.assertAlmostEqual(result['error_mm'], 0, delta=0.1)
            self.assertGreater(result['valid_depth_ratio'], .9)
            self.assertEqual(result['frame_count'], 1)

    def test_multiframe_requires_valid_majority(self):
        with tempfile.TemporaryDirectory() as directory:
            depth_path = Path(directory) / 'depth.npz'
            report_path = Path(directory) / 'result.json'
            good = np.full((200, 200), 650, dtype=np.uint16)
            good[55:146, 55:146] = 625
            bad = np.zeros_like(good)
            intrinsics = np.array([[220., 0, 100], [0, 220., 100], [0, 0, 1]])
            argv = ['test_depth_block.py', '--depth', str(depth_path), '--output', str(report_path),
                    '--bbox', '55', '55', '145', '145', '--height-mm', '25']
            np.savez_compressed(depth_path, depth_mm=np.stack([good, good, good, good, bad]),
                                intrinsics=np.repeat(intrinsics[None], 5, axis=0),
                                timestamp_delta_ms=np.arange(5, dtype=float))
            with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
                test_depth_block.main()
            result = json.loads(report_path.read_text(encoding='utf-8'))
            self.assertTrue(result['ok'])
            self.assertEqual(result['valid_frame_count'], 4)
            self.assertEqual(result['required_valid_frames'], 4)
            self.assertAlmostEqual(result['thickness_mm'], 25, delta=.1)
            self.assertEqual(result['frames'][-1]['reason'], 'insufficient_support_depth')

            np.savez_compressed(depth_path, depth_mm=np.stack([good, good, good, bad, bad]),
                                intrinsics=intrinsics)
            with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    test_depth_block.main()
            self.assertEqual(caught.exception.code, 2)
            result = json.loads(report_path.read_text(encoding='utf-8'))
            self.assertFalse(result['ok'])
            self.assertIsNone(result['thickness_mm'])


if __name__ == '__main__':
    unittest.main()
