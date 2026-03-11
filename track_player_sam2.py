from __future__ import annotations

import argparse
import contextlib
import io
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import sam2.utils.misc as sam2_misc
import torch
from sam2.sam2_video_predictor import SAM2VideoPredictor


WINDOW_NAME = "Select Player Box"
REVIEW_WINDOW_NAME = "Review SAM2 Tracking"
TRANSPORT_HEIGHT = 56


class UserAbort(RuntimeError):
    pass


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
    parser.add_argument("--player-name", default="selected player", help="Label to draw for the tracked player")
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
    selection: dict[str, tuple[int, int] | bool | None] = {
        "start": None,
        "current": None,
        "dragging": False,
        "completed": False,
    }

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            selection["start"] = (x, y)
            selection["current"] = (x, y)
            selection["dragging"] = True
            selection["completed"] = False
        elif event == cv2.EVENT_MOUSEMOVE and selection["dragging"]:
            selection["current"] = (x, y)
        elif event == cv2.EVENT_LBUTTONUP and selection["dragging"]:
            selection["current"] = (x, y)
            selection["dragging"] = False
            selection["completed"] = True

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW_NAME, on_mouse)
    try:
        while True:
            display = frame.copy()
            start = selection["start"]
            current = selection["current"]
            if start is not None and current is not None:
                x1, y1 = start
                x2, y2 = current
                left, right = sorted((x1, x2))
                top, bottom = sorted((y1, y2))
                if right > left and bottom > top:
                    cv2.rectangle(display, (left, top), (right, bottom), (0, 215, 255), 2, cv2.LINE_AA)
            draw_text(display, "Drag to draw box. Release to accept. Esc cancel.", (16, 32), 0.65, (255, 255, 255), 1)
            cv2.imshow(WINDOW_NAME, display)
            if selection["completed"] and start is not None and current is not None:
                x1, y1 = start
                x2, y2 = current
                if abs(x2 - x1) > 1 and abs(y2 - y1) > 1:
                    break
            key = cv2.waitKey(16) & 0xFF
            if key == 27:
                raise RuntimeError("No selection made.")
    finally:
        cv2.destroyWindow(WINDOW_NAME)

    start = selection["start"]
    current = selection["current"]
    if start is None or current is None:
        raise RuntimeError("No selection made.")

    x1, y1 = start
    x2, y2 = current
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    if right <= left or bottom <= top:
        raise RuntimeError("No selection made.")
    return int(left), int(top), int(right), int(bottom)


def ensure_jpeg_frames(
    source_path: Path,
    frame_dir: Path,
    total_frames: int | None = None,
    progress_callback=None,
) -> Path:
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
        "-loglevel",
        "error",
        "-nostats",
        "-i",
        str(source_path),
        "-q:v",
        "2",
        "-start_number",
        "0",
        "-progress",
        "pipe:1",
        str(output_pattern),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    current_frame = 0
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "frame":
            try:
                current_frame = int(value)
            except ValueError:
                continue
            if progress_callback is not None and total_frames is not None:
                progress_callback("Extracting video frames", min(current_frame, total_frames), total_frames, source_path.name)
    stderr_output = process.stderr.read() if process.stderr is not None else ""
    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command, output=None, stderr=stderr_output)
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


def upsert_prompt(
    prompts: list[tuple[int, tuple[int, int, int, int]]],
    frame_idx: int,
    box: tuple[int, int, int, int],
) -> list[tuple[int, tuple[int, int, int, int]]]:
    updated = [(existing_frame_idx, existing_box) for existing_frame_idx, existing_box in prompts if existing_frame_idx != frame_idx]
    updated.append((frame_idx, box))
    updated.sort(key=lambda item: item[0])
    return updated


def add_prompt_to_state(
    predictor: SAM2VideoPredictor,
    inference_state: dict,
    frame_idx: int,
    box: tuple[int, int, int, int],
) -> np.ndarray:
    _, _, mask_logits = predictor.add_new_points_or_box(
        inference_state=inference_state,
        frame_idx=frame_idx,
        obj_id=1,
        box=np.array(box, dtype=np.float32),
    )
    return (mask_logits[0] > 0.0).detach().cpu().numpy()


def initialize_tracking_state(
    predictor: SAM2VideoPredictor,
    frame_source: Path,
    prompts: list[tuple[int, tuple[int, int, int, int]]],
    progress_callback=None,
) -> tuple[dict, dict[int, np.ndarray]]:
    original_tqdm = sam2_misc.tqdm

    def modal_tqdm(iterable, desc=None, **kwargs):
        total = len(iterable) if hasattr(iterable, "__len__") else None
        for index, item in enumerate(iterable, start=1):
            if progress_callback is not None and total is not None:
                progress_callback("Loading frames into tracker", index, total, frame_source.name)
            yield item

    sam2_misc.tqdm = modal_tqdm
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            inference_state = predictor.init_state(
                video_path=str(frame_source),
                offload_video_to_cpu=True,
                offload_state_to_cpu=False,
            )
    finally:
        sam2_misc.tqdm = original_tqdm

    video_masks: dict[int, np.ndarray] = {}
    for frame_idx, box in prompts:
        video_masks[frame_idx] = add_prompt_to_state(predictor, inference_state, frame_idx, box)
    return inference_state, video_masks


def clear_tracking_from_frame(inference_state: dict, start_frame_idx: int) -> None:
    for obj_output_dict in inference_state["output_dict_per_obj"].values():
        stale_non_cond_frames = [frame_idx for frame_idx in obj_output_dict["non_cond_frame_outputs"] if frame_idx >= start_frame_idx]
        for frame_idx in stale_non_cond_frames:
            obj_output_dict["non_cond_frame_outputs"].pop(frame_idx, None)

    for obj_temp_output_dict in inference_state["temp_output_dict_per_obj"].values():
        stale_temp_non_cond_frames = [frame_idx for frame_idx in obj_temp_output_dict["non_cond_frame_outputs"] if frame_idx >= start_frame_idx]
        for frame_idx in stale_temp_non_cond_frames:
            obj_temp_output_dict["non_cond_frame_outputs"].pop(frame_idx, None)

    for tracked_frames in inference_state["frames_tracked_per_obj"].values():
        stale_tracked_frames = [frame_idx for frame_idx in tracked_frames if frame_idx >= start_frame_idx]
        for frame_idx in stale_tracked_frames:
            tracked_frames.pop(frame_idx, None)


def collect_masks(
    predictor: SAM2VideoPredictor,
    inference_state: dict,
    start_frame_idx: int,
    video_masks: dict[int, np.ndarray],
    progress_callback=None,
) -> dict[int, np.ndarray]:
    stale_mask_frames = [frame_idx for frame_idx in video_masks if frame_idx >= start_frame_idx]
    for frame_idx in stale_mask_frames:
        video_masks.pop(frame_idx, None)

    max_frame_num_to_track = inference_state["num_frames"] - start_frame_idx
    total_frames = max_frame_num_to_track
    last_progress_update = 0.0
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        for out_frame_idx, out_obj_ids, out_mask_logits in predictor.propagate_in_video(
            inference_state,
            start_frame_idx=start_frame_idx,
            max_frame_num_to_track=max_frame_num_to_track,
        ):
            if progress_callback is not None:
                now = time.monotonic()
                current_step = int(out_frame_idx) - start_frame_idx + 1
                if current_step == 1 or current_step == total_frames or (now - last_progress_update) >= 0.2:
                    progress_callback(
                        "Processing tracker masks",
                        current_step,
                        total_frames,
                        f"starting at frame {start_frame_idx + 1}",
                    )
                    last_progress_update = now
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


def build_box_cache(video_masks: dict[int, np.ndarray]) -> dict[int, tuple[int, int, int, int] | None]:
    return {frame_idx: mask_to_box(mask) for frame_idx, mask in video_masks.items()}


def review_canvas_shape(frame_shape: tuple[int, int, int], show_transport: bool) -> tuple[int, int, int]:
    height, width = frame_shape[:2]
    return (height + (TRANSPORT_HEIGHT if show_transport else 0), width, 3)


def timeline_geometry(frame_shape: tuple[int, int, int]) -> tuple[int, int, int, int]:
    height, width = frame_shape[:2]
    left = 92
    right = width - 20
    top = height - 30
    bottom = height - 12
    return left, top, right, bottom


def frame_from_timeline_x(x: int, frame_shape: tuple[int, int, int], total_frames: int) -> int:
    left, _, right, _ = timeline_geometry(frame_shape)
    clamped_x = min(max(x, left), right)
    progress = (clamped_x - left) / max(right - left, 1)
    return int(round(progress * max(total_frames - 1, 0)))


def play_button_geometry(frame_shape: tuple[int, int, int]) -> tuple[int, int, int, int]:
    height, _ = frame_shape[:2]
    left = 20
    right = 72
    top = height - 40
    bottom = height - 8
    return left, top, right, bottom


def point_in_rect(x: int, y: int, rect: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = rect
    return left <= x <= right and top <= y <= bottom


def draw_text(
    frame: np.ndarray,
    text: str,
    origin: tuple[int, int],
    scale: float,
    color: tuple[int, int, int],
    thickness: int,
    shadow_color: tuple[int, int, int] = (0, 0, 0),
) -> None:
    x, y = origin
    cv2.putText(frame, text, (x + 2, y + 2), cv2.FONT_HERSHEY_SIMPLEX, scale, shadow_color, thickness + 2, cv2.LINE_AA)
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def draw_processing_modal(frame: np.ndarray, stage: str, current: int, total: int, detail: str = "") -> None:
    total = max(total, 1)
    progress = min(max(current / total, 0.0), 1.0)
    overlay = frame.copy()
    height, width = frame.shape[:2]
    panel_width = min(760, width - 60)
    panel_height = 210
    x1 = (width - panel_width) // 2
    y1 = max(40, (height - panel_height) // 2)
    x2 = x1 + panel_width
    y2 = y1 + panel_height

    cv2.rectangle(overlay, (x1, y1), (x2, y2), (18, 18, 18), -1)
    cv2.addWeighted(overlay, 0.88, frame, 0.12, 0, frame)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (96, 96, 96), 2)

    draw_text(frame, "SAM2 Tracker", (x1 + 30, y1 + 44), 1.0, (245, 245, 245), 2)
    draw_text(frame, stage, (x1 + 30, y1 + 88), 0.92, (0, 215, 255), 2)

    bar_left = x1 + 30
    bar_top = y1 + 112
    bar_width = panel_width - 60
    bar_height = 20
    cv2.rectangle(frame, (bar_left, bar_top), (bar_left + bar_width, bar_top + bar_height), (70, 70, 70), -1)
    cv2.rectangle(frame, (bar_left, bar_top), (bar_left + int(bar_width * progress), bar_top + bar_height), (0, 215, 255), -1)
    cv2.rectangle(frame, (bar_left, bar_top), (bar_left + bar_width, bar_top + bar_height), (120, 120, 120), 1)

    draw_text(
        frame,
        f"{current}/{total} ({progress * 100:0.1f}%)",
        (x1 + 30, y1 + 162),
        0.78,
        (232, 232, 232),
        2,
    )
    if detail:
        draw_text(frame, detail, (x1 + 30, y1 + 192), 0.62, (190, 190, 190), 1)


def is_player_visible(
    frame_idx: int,
    prompts: list[tuple[int, tuple[int, int, int, int]]],
    offscreen_frames: set[int],
) -> bool:
    latest_box_frame = max((prompt_frame_idx for prompt_frame_idx, _ in prompts if prompt_frame_idx <= frame_idx), default=None)
    latest_offscreen_frame = max((offscreen_frame for offscreen_frame in offscreen_frames if offscreen_frame <= frame_idx), default=None)
    if latest_box_frame is None:
        return False
    if latest_offscreen_frame is None:
        return True
    return latest_box_frame >= latest_offscreen_frame


def draw_help_panel(frame: np.ndarray, paused: bool) -> None:
    lines = [
        "Controls",
        "Space  play/pause",
        "A / D  step",
        "C      draw box",
        "X      off-screen",
        "H      hide help",
        "Q      save and exit",
        "Esc    discard and quit",
    ]
    line_height = 28
    panel_width = 285
    footer_height = 30
    panel_height = 22 + line_height * len(lines) + footer_height
    x1 = frame.shape[1] - panel_width - 20
    y1 = 20
    x2 = frame.shape[1] - 20
    y2 = y1 + panel_height

    overlay = frame.copy()
    cv2.rectangle(overlay, (x1, y1), (x2, y2), (18, 18, 18), -1)
    cv2.addWeighted(overlay, 0.72, frame, 0.28, 0, frame)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (80, 80, 80), 1)

    for index, line in enumerate(lines):
        color = (255, 255, 255) if index == 0 else (225, 225, 225)
        scale = 0.7 if index == 0 else 0.6
        thickness = 2 if index == 0 else 1
        cv2.putText(
            frame,
            line,
            (x1 + 16, y1 + 28 + line_height * index),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            color,
            thickness,
            cv2.LINE_AA,
        )

    status = "Edit Mode" if paused else "Playback"
    cv2.line(frame, (x1 + 12, y2 - footer_height), (x2 - 12, y2 - footer_height), (64, 64, 64), 1, cv2.LINE_AA)
    cv2.putText(frame, status, (x1 + 16, y2 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 215, 255), 1, cv2.LINE_AA)


def render_frame(
    frame: np.ndarray,
    mask: np.ndarray | None,
    box: tuple[int, int, int, int] | None,
    player_visible: bool,
    player_name: str,
    alpha: float,
    line_width: int,
    frame_index: int,
    fps: float,
    total_frames: int,
    show_help: bool,
    paused: bool,
    show_transport: bool = True,
    modal_state: tuple[str, int, int, str] | None = None,
) -> np.ndarray:
    rendered = frame.copy()
    if player_visible and mask is not None:
        if box is not None:
            x1, y1, x2, y2 = box
            cv2.rectangle(rendered, (x1, y1), (x2, y2), (0, 215, 255), line_width, cv2.LINE_AA)
            cv2.putText(rendered, player_name, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 215, 255), 2, cv2.LINE_AA)
    total_frames = max(total_frames, 1)
    seconds = frame_index / max(fps, 1.0)
    total_seconds = (total_frames - 1) / max(fps, 1.0)
    progress = frame_index / max(total_frames - 1, 1)
    canvas = rendered
    if show_transport:
        canvas = np.full(review_canvas_shape(rendered.shape, True), 20, dtype=np.uint8)
        canvas[: rendered.shape[0], :, :] = rendered
        transport_top = rendered.shape[0]
        cv2.rectangle(canvas, (0, transport_top), (canvas.shape[1], canvas.shape[0]), (18, 18, 18), -1)
        play_left, play_top, play_right, play_bottom = play_button_geometry(canvas.shape)
        overlay = canvas.copy()
        cv2.rectangle(overlay, (play_left, play_top), (play_right, play_bottom), (18, 18, 18), -1)
        cv2.addWeighted(overlay, 0.78, canvas, 0.22, 0, canvas)
        cv2.rectangle(canvas, (play_left, play_top), (play_right, play_bottom), (96, 96, 96), 1)
        center_y = (play_top + play_bottom) // 2
        if paused:
            triangle = np.array(
                [
                    [play_left + 20, play_top + 7],
                    [play_left + 20, play_bottom - 7],
                    [play_right - 16, center_y],
                ],
                dtype=np.int32,
            )
            cv2.fillConvexPoly(canvas, triangle, (245, 245, 245), cv2.LINE_AA)
        else:
            cv2.rectangle(canvas, (play_left + 16, play_top + 7), (play_left + 24, play_bottom - 7), (245, 245, 245), -1)
            cv2.rectangle(canvas, (play_left + 34, play_top + 7), (play_left + 42, play_bottom - 7), (245, 245, 245), -1)
        bar_left, bar_top, bar_right, bar_bottom = timeline_geometry(canvas.shape)
        time_text = f"{seconds:0.2f}s / {total_seconds:0.2f}s"
        draw_text(canvas, time_text, (bar_left, bar_top - 10), 0.55, (235, 235, 235), 1)
        cv2.rectangle(canvas, (bar_left, bar_top), (bar_right, bar_bottom), (70, 70, 70), -1)
        knob_x = bar_left + int((bar_right - bar_left) * progress)
        cv2.rectangle(canvas, (bar_left, bar_top), (knob_x, bar_bottom), (0, 215, 255), -1)
        cv2.circle(canvas, (knob_x, (bar_top + bar_bottom) // 2), 8, (245, 245, 245), -1, cv2.LINE_AA)
    if show_help:
        draw_help_panel(canvas, paused)
    else:
        if show_transport:
            cv2.putText(canvas, "H for help", (20, canvas.shape[0] - 34), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    if modal_state is not None:
        draw_processing_modal(canvas, *modal_state)
    return canvas


def review_and_collect_corrections(
    source_frames: list[np.ndarray],
    fps: float,
    prompts: list[tuple[int, tuple[int, int, int, int]]],
    offscreen_frames: set[int],
    video_masks: dict[int, np.ndarray],
    box_cache: dict[int, tuple[int, int, int, int] | None],
    player_name: str,
    alpha: float,
    line_width: int,
    start_frame: int,
) -> tuple[list[tuple[int, tuple[int, int, int, int]]], set[int], bool, int | None, int]:
    current_frame = max(0, min(start_frame, len(source_frames) - 1))
    paused = False
    frame_delay = max(1, int(1000 / max(fps, 1.0)))
    paused_poll_delay = 16
    last_frame_idx = len(source_frames) - 1
    show_help = True
    scrub_state = {"requested_frame": None, "dragging": False, "toggle_pause": False}
    canvas_shape = review_canvas_shape(source_frames[0].shape, True)

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        left, top, right, bottom = timeline_geometry(canvas_shape)
        on_timeline = left <= x <= right and (top - 20) <= y <= (bottom + 20)
        play_rect = play_button_geometry(canvas_shape)
        if event == cv2.EVENT_LBUTTONDOWN and point_in_rect(x, y, play_rect):
            scrub_state["toggle_pause"] = True
        elif event == cv2.EVENT_LBUTTONDOWN and on_timeline:
            scrub_state["dragging"] = True
            scrub_state["requested_frame"] = frame_from_timeline_x(x, canvas_shape, len(source_frames))
        elif event == cv2.EVENT_MOUSEMOVE and scrub_state["dragging"]:
            scrub_state["requested_frame"] = frame_from_timeline_x(x, canvas_shape, len(source_frames))
        elif event == cv2.EVENT_LBUTTONUP:
            if scrub_state["dragging"]:
                scrub_state["requested_frame"] = frame_from_timeline_x(x, canvas_shape, len(source_frames))
            scrub_state["dragging"] = False

    cv2.namedWindow(REVIEW_WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(REVIEW_WINDOW_NAME, on_mouse)
    try:
        while True:
            if scrub_state["toggle_pause"]:
                paused = not paused
                scrub_state["toggle_pause"] = False
            requested_frame = scrub_state["requested_frame"]
            if requested_frame is not None:
                current_frame = max(0, min(requested_frame, last_frame_idx))
                paused = True
                scrub_state["requested_frame"] = None

            rendered = render_frame(
                source_frames[current_frame],
                video_masks.get(current_frame),
                box_cache.get(current_frame),
                is_player_visible(current_frame, prompts, offscreen_frames),
                player_name,
                alpha,
                line_width,
                current_frame,
                fps,
                len(source_frames),
                show_help,
                paused,
            )
            cv2.imshow(REVIEW_WINDOW_NAME, rendered)
            key = cv2.waitKey(paused_poll_delay if paused else frame_delay) & 0xFF

            if cv2.getWindowProperty(REVIEW_WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                raise UserAbort("Window closed during review.")
            if key == 255:
                if not paused:
                    if current_frame < last_frame_idx:
                        current_frame += 1
                    else:
                        paused = True
                continue
            if key == ord(" "):
                paused = not paused
                continue
            if key == ord("h"):
                show_help = not show_help
                continue
            if key == ord("q"):
                return prompts, offscreen_frames, True, None, current_frame
            if key == 27:
                raise UserAbort("Discarded during review.")
            if key == ord("c"):
                corrected_box = select_box_on_frame(source_frames[current_frame])
                prompts = upsert_prompt(prompts, current_frame, corrected_box)
                offscreen_frames.discard(current_frame)
                return prompts, offscreen_frames, False, current_frame, max(0, current_frame - 15)
            if key == ord("x"):
                offscreen_frames.add(current_frame)
                return prompts, offscreen_frames, False, None, max(0, current_frame - 15)
            if key in (ord("d"), 83):
                current_frame = min(last_frame_idx, current_frame + 1)
                paused = True
                continue
            if key in (ord("a"), 81):
                current_frame = max(0, current_frame - 1)
                paused = True
                continue
            if not paused:
                if current_frame < last_frame_idx:
                    current_frame += 1
                else:
                    paused = True
    finally:
        cv2.destroyWindow(REVIEW_WINDOW_NAME)
    return prompts, offscreen_frames, True, None, last_frame_idx


def render_output(
    source_frames: list[np.ndarray],
    fps: float,
    output_path: Path,
    video_masks: dict[int, np.ndarray],
    box_cache: dict[int, tuple[int, int, int, int] | None],
    prompts: list[tuple[int, tuple[int, int, int, int]]],
    offscreen_frames: set[int],
    player_name: str,
    alpha: float,
    line_width: int,
    progress_callback=None,
) -> None:
    if not source_frames:
        raise RuntimeError("No source frames available for rendering.")
    height, width = source_frames[0].shape[:2]
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"Could not open output {output_path}")

    try:
        for frame_index, frame in enumerate(source_frames):
            if progress_callback is not None:
                progress_callback("Rendering output video", frame_index + 1, len(source_frames), output_path.name)
            writer.write(
                render_frame(
                    frame,
                    video_masks.get(frame_index),
                    box_cache.get(frame_index),
                    is_player_visible(frame_index, prompts, offscreen_frames),
                    player_name,
                    alpha,
                    line_width,
                    frame_index,
                    fps,
                    len(source_frames),
                    False,
                    False,
                    False,
                    None,
                )
            )
    finally:
        writer.release()


def show_review_modal(
    source_frames: list[np.ndarray],
    frame_idx: int,
    video_masks: dict[int, np.ndarray],
    box_cache: dict[int, tuple[int, int, int, int] | None],
    prompts: list[tuple[int, tuple[int, int, int, int]]],
    offscreen_frames: set[int],
    player_name: str,
    fps: float,
    alpha: float,
    line_width: int,
    stage: str,
    current: int,
    total: int,
    detail: str,
) -> None:
    rendered = render_frame(
        source_frames[frame_idx],
        video_masks.get(frame_idx),
        box_cache.get(frame_idx),
        is_player_visible(frame_idx, prompts, offscreen_frames),
        player_name,
        alpha,
        line_width,
        frame_idx,
        fps,
        len(source_frames),
        False,
        True,
        True,
        (stage, current, total, detail),
    )
    cv2.imshow(REVIEW_WINDOW_NAME, rendered)
    key = cv2.waitKey(1) & 0xFF
    if key in (27, ord("q")):
        raise UserAbort("Aborted during processing.")
    if cv2.getWindowProperty(REVIEW_WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
        raise UserAbort("Window closed during processing.")


def main() -> int:
    try:
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
        offscreen_frames: set[int] = set()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            predictor = SAM2VideoPredictor.from_pretrained(
                args.model_id,
                device=args.device,
                fill_hole_area=0,
            )
        extraction_preview_frame_idx = min(max(args.frame_idx, 0), len(source_frames) - 1)
        extraction_progress_callback = lambda stage, current, total, detail: show_review_modal(
            source_frames,
            extraction_preview_frame_idx,
            {},
            {},
            prompts,
            offscreen_frames,
            args.player_name,
            review_fps,
            args.mask_alpha,
            args.line_width,
            stage,
            current,
            total,
            detail,
        )
        frame_source = ensure_jpeg_frames(
            source_path,
            frame_dir,
            total_frames=len(source_frames),
            progress_callback=extraction_progress_callback,
        )
        review_start_frame = min(frame_idx for frame_idx, _ in prompts)
        track_start_frame: int | None = review_start_frame
        inference_state, video_masks = initialize_tracking_state(
            predictor,
            frame_source,
            prompts,
            progress_callback=extraction_progress_callback,
        )
        box_cache = build_box_cache(video_masks)
        while True:
            if track_start_frame is not None:
                preview_frame_idx = max(0, min(review_start_frame, len(source_frames) - 1))
                progress_callback = lambda stage, current, total, detail: show_review_modal(
                    source_frames,
                    preview_frame_idx,
                    video_masks,
                    box_cache,
                    prompts,
                    offscreen_frames,
                    args.player_name,
                    review_fps,
                    args.mask_alpha,
                    args.line_width,
                    stage,
                    current,
                    total,
                    detail,
                )
                with torch.inference_mode():
                    video_masks = collect_masks(
                        predictor,
                        inference_state,
                        track_start_frame,
                        video_masks,
                        progress_callback=progress_callback,
                    )
            box_cache = build_box_cache(video_masks)
            if args.no_review:
                break
            prompts, offscreen_frames, accepted, track_start_frame, review_start_frame = review_and_collect_corrections(
                source_frames,
                review_fps,
                prompts,
                offscreen_frames,
                video_masks,
                box_cache,
                args.player_name,
                args.mask_alpha,
                args.line_width,
                review_start_frame,
            )
            if accepted:
                break
            if track_start_frame is None:
                continue
            clear_tracking_from_frame(inference_state, track_start_frame)
            if track_start_frame in dict(prompts):
                latest_box = dict(prompts)[track_start_frame]
                video_masks[track_start_frame] = add_prompt_to_state(predictor, inference_state, track_start_frame, latest_box)
        render_progress_callback = lambda stage, current, total, detail: show_review_modal(
            source_frames,
            min(review_start_frame, len(source_frames) - 1),
            video_masks,
            box_cache,
            prompts,
            offscreen_frames,
            args.player_name,
            review_fps,
            args.mask_alpha,
            args.line_width,
            stage,
            current,
            total,
            detail,
        )
        render_output(
            source_frames,
            source_fps,
            output_path,
            video_masks,
            box_cache,
            prompts,
            offscreen_frames,
            args.player_name,
            args.mask_alpha,
            args.line_width,
            progress_callback=render_progress_callback,
        )
        return 0
    except UserAbort:
        cv2.destroyAllWindows()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
