import argparse
import json
from pathlib import Path

import cv2


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a metric ChArUco board image.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--spec-output", required=True, type=Path)
    args = parser.parse_args()

    squares_x = 5
    squares_y = 7
    square_length_mm = 30.0
    marker_length_mm = 22.0
    pixels_per_mm = 12

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    board = cv2.aruco.CharucoBoard(
        (squares_x, squares_y),
        square_length_mm,
        marker_length_mm,
        dictionary,
    )

    width_px = int(squares_x * square_length_mm * pixels_per_mm)
    height_px = int(squares_y * square_length_mm * pixels_per_mm)
    image = board.generateImage(
        (width_px, height_px),
        marginSize=0,
        borderBits=1,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(args.output), image):
        raise RuntimeError(f"Failed to write {args.output}")

    spec = {
        "type": "charuco",
        "dictionary": "DICT_4X4_50",
        "squares_x": squares_x,
        "squares_y": squares_y,
        "square_length_mm": square_length_mm,
        "marker_length_mm": marker_length_mm,
        "board_width_mm": squares_x * square_length_mm,
        "board_height_mm": squares_y * square_length_mm,
        "image_width_px": width_px,
        "image_height_px": height_px,
        "pixels_per_mm": pixels_per_mm,
        "print_scale_percent": 100,
    }
    args.spec_output.parent.mkdir(parents=True, exist_ok=True)
    args.spec_output.write_text(json.dumps(spec, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
