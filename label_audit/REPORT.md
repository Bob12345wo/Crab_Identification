# Crab pose label audit

Source: `F:/yolo/CrabKey/crab`; read-only scan of 1000 YOLO pose labels. The original dataset was not modified.

## Findings

- `crab_clean.yaml`, the exported ONNX model, `convert_labels.py`, `predict.py`, and `oak_crab_measure.py` all agree on 48 keypoints. The ten leg index groups match exactly. A simple index-offset error in deployment is unlikely.
- The deployed `.pt` is byte-identical to `runs/pose/runs/crab_pose_clean_noflip/weights/best.pt`.
- All 1000 TXT labels have the expected 149 values and zero missing/hidden keypoints. There are 49 visible keypoints outside their annotation box across the dataset. No label puts a visible keypoint outside the image.
- The annotations contain different scales: 265/1000 boxes are narrower than half the image, and 364/1000 are wider than 80%. The printed example is not evidence that the dataset contains only large crabs.
- In sampled GT overlays, individual leg polylines sometimes connect points on different physical limbs through empty background. In `Img_00239_gt.jpg`, the red L-Claw chain goes from a right-side claw to upper-left legs. `Img_00001_gt.jpg` and `Img_00931_gt.jpg` show similar long cross-background links. This is a material training-label/topology problem for leg-length measurement, regardless of prediction confidence.
- Every keypoint is labeled visible (`v=2`), even in poses where some joints appear occluded. This encourages confident guesses for unseen points.
- Training args record `imgsz: 1280`; the OAK deployment uses 640. This can reduce localization precision but does not explain wrong GT polylines.

## What to do next

1. Define one anatomical meaning for each of the 48 names in `convert_labels.py`; for each leg, list points from body attachment to tip in physical order. Decide how hidden joints should be marked.
2. Correct a small representative batch first, including `Img_00239`, `Img_00001`, `Img_00931`, and varied sizes/poses. Re-render these GT overlays and require each polyline to stay on its own limb. Do not change model weights yet.
3. Audit the remaining labels systematically, then retrain and evaluate endpoint error and leg-length error on an untouched validation split. Compare both 640 and 1280 inference. Only accept the new model after inspecting real camera debug images.

Generated with `python tools/audit_crab_labels.py --dataset F:/yolo/CrabKey/crab --output label_audit`.
