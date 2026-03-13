#!/usr/bin/env python3
"""Generate a deterministic synthetic video for tracker workflow tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Path to the MP4 file to create")
    parser.add_argument("--manifest", type=Path, default=None, help="Optional JSON manifest path")
    parser.add_argument("--width", type=int, default=854)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=float, default=12.0)
    parser.add_argument("--frames", type=int, default=24)
    parser.add_argument("--radius", type=int, default=18)
    parser.add_argument("--margin", type=int, default=32)
    return parser


def expected_box_for_frame(
    frame_index: int,
    total_frames: int,
    width: int,
    height: int,
    radius: int,
    margin: int,
) -> dict[str, int]:
    if total_frames <= 1:
        center_x = margin + radius
    else:
        travel_width = width - (margin * 2) - (radius * 2)
        center_x = margin + radius + round((frame_index / (total_frames - 1)) * travel_width)
    center_y = height // 2
    return {
        "x": center_x - radius,
        "y": center_y - radius,
        "width": radius * 2,
        "height": radius * 2,
    }


def create_video(
    output_path: Path,
    *,
    width: int,
    height: int,
    fps: float,
    frames: int,
    radius: int,
    margin: int,
) -> list[dict[str, int]]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Unable to create video at {output_path}")

    expected_boxes: list[dict[str, int]] = []
    try:
        for frame_index in range(frames):
            box = expected_box_for_frame(frame_index, frames, width, height, radius, margin)
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            center = (
                box["x"] + radius,
                box["y"] + radius,
            )
            cv2.circle(frame, center, radius, (255, 255, 255), -1, cv2.LINE_AA)
            writer.write(frame)
            expected_boxes.append(box)
    finally:
        writer.release()

    return expected_boxes


def main() -> int:
    args = build_parser().parse_args()
    boxes = create_video(
        args.output,
        width=args.width,
        height=args.height,
        fps=args.fps,
        frames=args.frames,
        radius=args.radius,
        margin=args.margin,
    )

    if args.manifest is not None:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        with args.manifest.open("w", encoding="utf-8") as file_handle:
            json.dump(
                {
                    "width": args.width,
                    "height": args.height,
                    "fps": args.fps,
                    "frames": args.frames,
                    "boxes": boxes,
                },
                file_handle,
                indent=2,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
