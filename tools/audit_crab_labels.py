"""Check YOLO crab-pose labels and render a small GT contact sheet."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


LEGS = [
    ("L-Claw", list(range(4, 10))),
    ("L-Tleg", list(range(10, 14))),
    ("L-Aleg", list(range(14, 18))),
    ("L-Bleg", list(range(18, 22))),
    ("L-Cleg", list(range(22, 26))),
    ("R-Claw", list(range(26, 32))),
    ("R-Tleg", list(range(32, 36))),
    ("R-Aleg", list(range(36, 40))),
    ("R-Bleg", list(range(40, 44))),
    ("R-Cleg", list(range(44, 48))),
]


def load_label(path):
    rows = [line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 149:
        raise ValueError(f"Expected one 149-value label row: {path}")
    values = np.asarray(rows[0], dtype=float)
    if not np.isfinite(values).all() or values[0] != 0:
        raise ValueError(f"Invalid values: {path}")
    return values[1:5], values[5:].reshape(48, 3)


def render(image, box, points, name):
    height, width = image.shape[:2]
    canvas = image.copy()
    cx, cy, bw, bh = box * [width, height, width, height]
    cv2.rectangle(canvas, (int(cx - bw / 2), int(cy - bh / 2)),
                  (int(cx + bw / 2), int(cy + bh / 2)), (0, 255, 255), 3)
    for index, (_, indices) in enumerate(LEGS):
        color = (50 + (index * 45) % 190, 80 + (index * 73) % 170, 220 - (index * 33) % 170)
        coords = [(int(points[i, 0] * width), int(points[i, 1] * height)) for i in indices]
        for a, b in zip(coords, coords[1:]):
            cv2.line(canvas, a, b, color, 3)
        for i, xy in zip(indices, coords):
            cv2.circle(canvas, xy, 6, color, -1)
            cv2.putText(canvas, str(i), (xy[0] + 5, xy[1] - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 3)
            cv2.putText(canvas, str(i), (xy[0] + 5, xy[1] - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 0, 0), 1)
    cv2.putText(canvas, name, (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 3)
    return canvas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", nargs="*", default=["Img_00001", "Img_00239", "Img_00500", "Img_00737", "Img_00931"])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    labels = sorted((args.dataset / "labels").glob("*.txt"))
    stats = {"labels": len(labels), "malformed": [], "missing_points": 0,
             "out_of_image_points": 0, "outside_box_points": 0,
             "bbox_width_under_half": 0, "bbox_width_over_eight_tenths": 0}
    for path in labels:
        try:
            box, points = load_label(path)
        except ValueError:
            stats["malformed"].append(path.name)
            continue
        stats["missing_points"] += int(np.count_nonzero(points[:, 2] == 0))
        visible = points[:, 2] > 0
        xy = points[visible, :2]
        stats["out_of_image_points"] += int(np.count_nonzero(np.any((xy < 0) | (xy > 1), axis=1)))
        x1, y1 = box[:2] - box[2:] / 2
        x2, y2 = box[:2] + box[2:] / 2
        stats["outside_box_points"] += int(np.count_nonzero(np.any((xy < [x1, y1]) | (xy > [x2, y2]), axis=1)))
        stats["bbox_width_under_half"] += int(box[2] < .5)
        stats["bbox_width_over_eight_tenths"] += int(box[2] > .8)
    for stem in args.samples:
        image_path = args.dataset / "images" / f"{stem}.jpg"
        label_path = args.dataset / "labels" / f"{stem}.txt"
        image = cv2.imread(str(image_path))
        if image is None or not label_path.exists():
            continue
        box, points = load_label(label_path)
        cv2.imwrite(str(args.output / f"{stem}_gt.jpg"), render(image, box, points, stem))
    (args.output / "summary.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
