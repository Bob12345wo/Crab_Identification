import unittest

import numpy as np

from oak_crab_measure import assess_pose_quality, decode_pose, read_nn_output


def pose_tensor(scores=(0.9, 0.4)):
    output = np.zeros((149, len(scores)), dtype=np.float32)
    for column, score in enumerate(scores):
        output[0, column] = 320 + column
        output[1, column] = 320
        output[2, column] = 400
        output[3, column] = 400
        output[4, column] = score
        for index in range(48):
            output[5 + index * 3, column] = 320
            output[6 + index * 3, column] = 320
            output[7 + index * 3, column] = 0.9
    return output


class PoseQualityTests(unittest.TestCase):
    def test_decoder_accepts_batch_and_transposed_layouts(self):
        output = pose_tensor()
        for variant in (output, output.T, output[None, ...]):
            pose = decode_pose(variant, 0.25, 0.5, 640)
            self.assertTrue(pose["ok"], pose)
            self.assertEqual(len(pose["keypoints"]), 48)
            self.assertEqual(pose["detections_after_nms"], 1)

    def test_decoder_rejects_bad_tensor_without_throwing(self):
        result = decode_pose(np.zeros((100, 10), dtype=np.float32), 0.25, 0.5, 640)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unexpected_pose_tensor_channels")

    def test_decoder_rejects_nonfinite_detection(self):
        output = pose_tensor()
        output[4, 0] = np.nan
        result = decode_pose(output, 0.25, 0.5, 640)
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["score"], 0.4)

    def test_quality_ignores_low_confidence_outside_keypoint(self):
        pose = decode_pose(pose_tensor((0.9,)), 0.25, 0.5, 640)
        pose["keypoints"][0] = [-100, -100, 0.1]
        quality = assess_pose_quality(pose, 640, 0.3, 0.08, 0.9, 3.0, 2, 0.3)
        self.assertTrue(quality["ok"], quality)
        self.assertEqual(quality["keypoints_outside_bbox"], 0)

    def test_quality_rejects_high_confidence_outside_keypoint(self):
        pose = decode_pose(pose_tensor((0.9,)), 0.25, 0.5, 640)
        for index in range(3):
            pose["keypoints"][index] = [-100, -100, 0.9]
        quality = assess_pose_quality(pose, 640, 0.3, 0.08, 0.9, 3.0, 2, 0.3)
        self.assertFalse(quality["ok"])
        self.assertEqual(quality["keypoints_outside_bbox"], 3)

    def test_nn_output_shape_is_checked(self):
        class Message:
            def getAllLayerNames(self):
                return ["output"]

            def getTensor(self, name):
                return np.zeros((100, 10), dtype=np.float32)

        with self.assertRaisesRegex(RuntimeError, "Unexpected YOLO NN tensor shape"):
            read_nn_output(Message())


if __name__ == "__main__":
    unittest.main()
