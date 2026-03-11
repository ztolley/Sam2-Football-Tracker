from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import torch
from sam2.sam2_video_predictor import SAM2VideoPredictor


WINDOW_NAME = "Select Player Box"
REVIEW_WINDOW_NAME = "Review SAM2 Tracking"


def preprocess_argv(argv: list[str]) -> list[str]:
    normalized: list[str] = []
    for arg in argv:
        if arg.startswith("--") or "=" not in arg:
            normalized.append(arg)
            continue
        key, value = arg.split("=", 1)
        normalized.extend([f"--{key}", value])
    return normalized


def detect_default_device() -> str:
    return "mps" if torch.backends.mps.is_available() else "cpu"


def parse_box(value: str | None) -> tuple[int, int, int, int] | None:
    if not value:
        return None
    parts = [int(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("box must have four comma-separated integers: x1,y1,x2,y2")
    x1, y1, x2, y2 = parts
    return x1, y1, x2, y2


def parse_prompt(value: str) -> tuple[int, tuple[int, int, int, int]]:
    try:
        frame_text, box_text = value.split(":", 1)
    except ValueError as exc:
        raise ValueError("prompt must be FRAME:x1,y1,x2,y2") from exc
    return int(frame_text.strip()), parse_box(box_text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track one selected player through a video with SAM 2.")
    parser.add_argument("--source", required=True, help="Input video path")
    parser.add_argument("--name", default="sam2-selected", help="Output run name")
    parser.add_argument("--project", default="runs", help="Output project directory")
    parser.add_argument("--model-id", default="facebook/sam2.1-hiera-base-plus", help="Hugging Face SAM 2 model id")
    parser.add_argument("--device", default=detect_default_device(), help="Inference device")
    parser.add_argument("--frame-idx", type=int, default=0, help="0-based frame index to place the initial prompt on")
    parser.add_argument("--box", default=None, help="Optional box prompt as x1,y1,x2,y2")
    parser.add_argument("--select-frame", action="append", type=int, default=[], help="Additional 0-based frames where an interactive correction box should be added")
    parser.add_argument("--prompt", action="append", default=[], help="Additional non-interactive correction prompt as FRAME:x1,y1,x2,y2")
    parser.add_argument("--mask-alpha", type=float, default=0.35, help="Overlay alpha for the mask")
    parser.add_argument("--line-width", type=int, default=2, help="Bounding box line width")
    parser.add_argument("--fps", type=float, default=30.0, help="Review playback fps")
    parser.add_argument("--no-review", action="store_true", help="Skip the interactive review/correction loop")
    parser.add_argument("--exist_ok", default="True", help="Accepted for compatibility; output folders are reused here.")
    return parser.parse_args(preprocess_argv(sys.argv[1:]))


def select_box_interactively(video_path: Path, frame_idx: int) -> tuple[int, int, int, int]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video {video_path}")
    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise RuntimeError(f"Could not read frame {frame_idx} from {video_path}")

    return select_box_on_frame(frame)


def select_box_on_frame(frame: np.ndarray) -> tuple[int, int, int, int]:
    roi = cv2.selectROI(WINDOW_NAME, frame, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(WINDOW_NAME)
    x, y, w, h = roi
    if w <= 0 or h <= 0:
        raise RuntimeError("No selection made.")
    return int(x), int(y), int(x + w), int(y + h)


def ensure_jpeg_frames(source_path: Path, frame_dir: Path) -> Path:
    existing = sorted(frame_dir.glob("*.jpg"))
    if existing:
        return frame_dir

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg is required to extract frames for SAM 2 video prompting.")

    frame_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = frame_dir / "%05d.jpg"
    command = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(source_path),
        "-q:v",
        "2",
        "-start_number",
        "0",
        str(output_pattern),
    ]
    subprocess.run(command, check=True)
    return frame_dir


def build_prompts(args: argparse.Namespace, source_path: Path) -> list[tuple[int, tuple[int, int, int, int]]]:
    prompts: list[tuple[int, tuple[int, int, int, int]]] = []

    initial_box = parse_box(args.box)
    if initial_box is None:
        initial_box = select_box_interactively(source_path, args.frame_idx)
    prompts.append((args.frame_idx, initial_box))

    for frame_idx in args.select_frame:
        prompts.append((frame_idx, select_box_interactively(source_path, frame_idx)))

    for prompt_value in args.prompt:
        prompts.append(parse_prompt(prompt_value))

    prompts.sort(key=lambda item: item[0])
    return prompts


def collect_masks(
    predictor: SAM2VideoPredictor,
    frame_source: Path,
    prompts: list[tuple[int, tuple[int, int, int, int]]],
) -> dict[int, np.ndarray]:
    inference_state = predictor.init_state(
        video_path=str(frame_source),
        offload_video_to_cpu=True,
        offload_state_to_cpu=False,
    )

    video_masks: dict[int, np.ndarray] = {}
    for frame_idx, box in prompts:
        _, _, mask_logits = predictor.add_new_points_or_box(
            inference_state=inference_state,
            frame_idx=frame_idx,
            obj_id=1,
            box=np.array(box, dtype=np.float32),
        )
        video_masks[frame_idx] = (mask_logits[0] > 0.0).detach().cpu().numpy()

    for out_frame_idx, out_obj_ids, out_mask_logits in predictor.propagate_in_video(inference_state):
        for obj_offset, out_obj_id in enumerate(out_obj_ids):
            if int(out_obj_id) != 1:
                continue
            video_masks[int(out_frame_idx)] = (out_mask_logits[obj_offset] > 0.0).detach().cpu().numpy()
    return video_masks


def mask_to_box(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    mask = np.squeeze(mask).astype(np.uint8)
    ys, xs = np.where(mask > 0)
    if len(xs) == 0 or len(ys) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def read_all_frames(source_path: Path) -> tuple[list[np.ndarray], float]:
    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video {source_path}")

    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    frames: list[np.ndarray] = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(frame)
    finally:
        capture.release()
    return frames, fps


def render_frame(
    frame: np.ndarray,
    mask: np.ndarray | None,
    alpha: float,
    line_width: int,
    frame_index: int,
    fps: float,
) -> np.ndarray:
    rendered = frame.copy()
    if mask is not None:
        mask = np.squeeze(mask).astype(bool)
        overlay = rendered.copy()
        overlay[mask] = (0, 215, 255)
        rendered = cv2.addWeighted(overlay, alpha, rendered, 1 - alpha, 0)
        box = mask_to_box(mask)
        if box is not None:
            x1, y1, x2, y2 = box
            cv2.rectangle(rendered, (x1, y1), (x2, y2), (0, 215, 255), line_width, cv2.LINE_AA)
            cv2.putText(rendered, "selected player", (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 215, 255), 2, cv2.LINE_AA)
    seconds = frame_index / max(fps, 1.0)
    cv2.putText(rendered, f"t={seconds:0.2f}s frame={frame_index}", (20, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    return rendered


def review_and_collect_corrections(
    source_frames: list[np.ndarray],
    fps: float,
    prompts: list[tuple[int, tuple[int, int, int, int]]],
    video_masks: dict[int, np.ndarray],
    alpha: float,
    line_width: int,
    start_frame: int,
) -> tuple[list[tuple[int, tuple[int, int, int, int]]], bool, int]:
    current_frame = max(0, min(start_frame, len(source_frames) - 1))
    paused = False
    frame_delay = max(1, int(1000 / max(fps, 1.0)))

    cv2.namedWindow(REVIEW_WINDOW_NAME, cv2.WINDOW_NORMAL)
    try:
        while current_frame < len(source_frames):
            rendered = render_frame(
                source_frames[current_frame],
                video_masks.get(current_frame),
                alpha,
                line_width,
                current_frame,
                fps,
            )
            cv2.imshow(REVIEW_WINDOW_NAME, rendered)
            key = cv2.waitKey(0 if paused else frame_delay) & 0xFF

            if key == 255:
                if not paused:
                    current_frame += 1
                continue
            if key == ord(" "):
                paused = not paused
                continue
            if key in (ord("q"), 27):
                return prompts, True, current_frame
            if key == ord("c"):
                corrected_box = select_box_on_frame(source_frames[current_frame])
                prompts.append((current_frame, corrected_box))
                prompts.sort(key=lambda item: item[0])
                return prompts, False, max(0, current_frame - 15)
            if key in (ord("d"), 83):
                current_frame = min(len(source_frames) - 1, current_frame + 1)
                paused = True
                continue
            if key in (ord("a"), 81):
                current_frame = max(0, current_frame - 1)
                paused = True
                continue
            if not paused:
                current_frame += 1
    finally:
        cv2.destroyWindow(REVIEW_WINDOW_NAME)
    return prompts, True, len(source_frames) - 1


def render_output(
    source_frames: list[np.ndarray],
    fps: float,
    output_path: Path,
    video_masks: dict[int, np.ndarray],
    alpha: float,
    line_width: int,
) -> None:
    if not source_frames:
        raise RuntimeError("No source frames available for rendering.")
    height, width = source_frames[0].shape[:2]
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"Could not open output {output_path}")

    try:
        for frame_index, frame in enumerate(source_frames):
            writer.write(render_frame(frame, video_masks.get(frame_index), alpha, line_width, frame_index, fps))
            frame_index += 1
            print(f"rendered frame {frame_index}", end="\r", flush=True)
    finally:
        writer.release()
    print()


def main() -> int:
    args = parse_args()
    source_path = Path(args.source).expanduser().resolve()
    project_path = Path(args.project).expanduser().resolve()
    output_dir = project_path / args.name
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{source_path.stem}-sam2.mp4"
    frame_dir = output_dir / f"{source_path.stem}-frames"
    source_frames, source_fps = read_all_frames(source_path)
    review_fps = args.fps or source_fps

    prompts = build_prompts(args, source_path)
    for prompt_frame_idx, prompt_box in prompts:
        print(f"Using frame {prompt_frame_idx} box {prompt_box}")

    predictor = SAM2VideoPredictor.from_pretrained(args.model_id, device=args.device)
    frame_source = ensure_jpeg_frames(source_path, frame_dir)
    review_start_frame = min(frame_idx for frame_idx, _ in prompts)
    while True:
        with torch.inference_mode():
            video_masks = collect_masks(predictor, frame_source, prompts)
        if args.no_review:
            break
        prompts, accepted, review_start_frame = review_and_collect_corrections(
            source_frames,
            review_fps,
            prompts,
            video_masks,
            args.mask_alpha,
            args.line_width,
            review_start_frame,
        )
        if accepted:
            break
        print("Added correction prompt. Recomputing tracking...")
        for prompt_frame_idx, prompt_box in prompts:
            print(f"Using frame {prompt_frame_idx} box {prompt_box}")
    render_output(source_frames, source_fps, output_path, video_masks, args.mask_alpha, args.line_width)
    print(f"Saved SAM 2 output to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
