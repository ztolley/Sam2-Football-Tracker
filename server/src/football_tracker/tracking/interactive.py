"""Interactive single-player football tracker built on SAM2 video prompting.

The script wraps the SAM2 video predictor with a lightweight OpenCV editor:

- prompt the initial player with a drag box on a chosen frame
- run SAM2 propagation over extracted JPEG frames
- review the track in a custom transport UI
- add corrective boxes or mark the player off-screen
- export a box-only annotated MP4 once the track is accepted

The code is organized around three phases:
1. input preparation: argument parsing, frame extraction, prompt collection
2. model execution: initialize SAM2 state and re-run propagation after edits
3. review/output UI: modal progress, transport controls, and final rendering
"""

from __future__ import annotations

import argparse
import contextlib
import functools
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


APP_WINDOW_NAME = "Football Tracker"
TRANSPORT_HEIGHT = 56
WINDOW_RESIZE_TOLERANCE = 8


class UserAbort(RuntimeError):
    """Raised when the user cancels from the review window or a modal step."""

    pass


class WindowController:
    """Keep one OpenCV window alive across selection, review, and modal states."""

    def __init__(self, name: str):
        self.name = name
        self._last_canvas_size: tuple[int, int] | None = None
        self._last_window_position: tuple[int, int] | None = None

    def image_rect(self) -> tuple[int, int, int, int] | None:
        try:
            rect = cv2.getWindowImageRect(self.name)
        except cv2.error:
            return None
        if rect[2] <= 0 or rect[3] <= 0:
            return None
        self._last_window_position = (rect[0], rect[1])
        return rect

    def ensure(self, canvas_shape: tuple[int, int, int], *, allow_resize: bool = True) -> None:
        target_height, target_width = canvas_shape[:2]
        rect = self.image_rect()
        if rect is None:
            cv2.namedWindow(self.name, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(self.name, target_width, target_height)
            if self._last_window_position is not None:
                cv2.moveWindow(self.name, *self._last_window_position)
        elif allow_resize and self._last_canvas_size is not None:
            pos_x, pos_y, current_width, current_height = rect
            last_width, last_height = self._last_canvas_size
            size_is_unchanged = (
                abs(current_width - last_width) <= WINDOW_RESIZE_TOLERANCE
                and abs(current_height - last_height) <= WINDOW_RESIZE_TOLERANCE
            )
            if size_is_unchanged and (current_width != target_width or current_height != target_height):
                cv2.resizeWindow(self.name, target_width, target_height)
                cv2.moveWindow(self.name, pos_x, pos_y)
        self._last_canvas_size = (target_width, target_height)

    def is_visible(self) -> bool:
        try:
            return cv2.getWindowProperty(self.name, cv2.WND_PROP_VISIBLE) >= 1
        except cv2.error:
            return False


class VideoFrameStore:
    def __init__(self, source_path: Path):
        self.source_path = source_path
        self._capture = self._open_capture()
        self.fps = self._capture.get(cv2.CAP_PROP_FPS) or 30.0
        self.frame_count = int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        ok, first_frame = self._capture.read()
        if not ok:
            raise RuntimeError(f"Could not read first frame from {source_path}")
        self.frame_shape = first_frame.shape
        self._cache: dict[int, np.ndarray] = {0: first_frame.copy()}
        self._current_frame_idx = 0

    def _open_capture(self) -> cv2.VideoCapture:
        capture = cv2.VideoCapture(str(self.source_path))
        if not capture.isOpened():
            raise RuntimeError(f"Could not open video {self.source_path}")
        return capture

    def _reopen_at_frame(self, frame_idx: int) -> None:
        self._capture.release()
        self._capture = self._open_capture()
        if frame_idx > 0:
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)

    def __len__(self) -> int:
        return self.frame_count

    def get_frame(self, frame_idx: int) -> np.ndarray:
        if self.frame_count <= 0:
            raise RuntimeError(f"No frames available in {self.source_path}")
        clamped_frame_idx = max(0, min(frame_idx, self.frame_count - 1))
        cached = self._cache.get(clamped_frame_idx)
        if cached is not None:
            return cached.copy()

        for attempt in range(2):
            if clamped_frame_idx != self._current_frame_idx + 1 or attempt > 0:
                if attempt > 0:
                    self._reopen_at_frame(clamped_frame_idx)
                else:
                    self._capture.set(cv2.CAP_PROP_POS_FRAMES, clamped_frame_idx)
            ok, frame = self._capture.read()
            if ok:
                break
        else:
            raise RuntimeError(f"Could not read frame {clamped_frame_idx} from {self.source_path}")
        self._current_frame_idx = clamped_frame_idx
        self._cache[clamped_frame_idx] = frame.copy()
        if len(self._cache) > 32:
            oldest_frame_idx = next(iter(self._cache))
            if oldest_frame_idx != clamped_frame_idx:
                self._cache.pop(oldest_frame_idx, None)
        return frame

    def close(self) -> None:
        self._capture.release()


class ImageSequenceFrameStore:
    def __init__(self, frame_dir: Path, fps: float):
        self.frame_dir = frame_dir
        self.fps = fps
        self.frame_paths = sorted(frame_dir.glob("*.jpg"))
        self.frame_count = len(self.frame_paths)
        if self.frame_count <= 0:
            raise RuntimeError(f"No extracted frames found in {frame_dir}")
        first_frame = cv2.imread(str(self.frame_paths[0]))
        if first_frame is None:
            raise RuntimeError(f"Could not read extracted frame {self.frame_paths[0]}")
        self.frame_shape = first_frame.shape
        self._cache: dict[int, np.ndarray] = {0: first_frame}

    def __len__(self) -> int:
        return self.frame_count

    def get_frame(self, frame_idx: int) -> np.ndarray:
        clamped_frame_idx = max(0, min(frame_idx, self.frame_count - 1))
        cached = self._cache.get(clamped_frame_idx)
        if cached is not None:
            return cached.copy()
        frame = cv2.imread(str(self.frame_paths[clamped_frame_idx]))
        if frame is None:
            raise RuntimeError(f"Could not read extracted frame {self.frame_paths[clamped_frame_idx]}")
        self._cache[clamped_frame_idx] = frame
        if len(self._cache) > 32:
            oldest_frame_idx = next(iter(self._cache))
            if oldest_frame_idx != clamped_frame_idx:
                self._cache.pop(oldest_frame_idx, None)
        return frame.copy()

    def close(self) -> None:
        return None


def preprocess_argv(argv: list[str]) -> list[str]:
    """Accept `key=value` shell args and normalize them to argparse form."""

    normalized: list[str] = []
    for arg in argv:
        if arg.startswith("--") or "=" not in arg:
            normalized.append(arg)
            continue
        key, value = arg.split("=", 1)
        normalized.extend([f"--{key}", value])
    return normalized


def detect_default_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


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
    """Parse CLI flags while keeping the shell wrapper's `key=value` UX."""

    parser = argparse.ArgumentParser(description="Track one selected player through a video with SAM 2.")
    parser.add_argument("--source", required=True, help="Input video path")
    parser.add_argument("--name", default="football-tracker-selected", help="Output run name")
    parser.add_argument("--player-name", default="selected player", help="Label to draw for the tracked player")
    parser.add_argument("--project", default="runs", help="Output project directory")
    parser.add_argument("--model-id", default="facebook/sam2.1-hiera-base-plus", help="Hugging Face SAM 2 model id")
    parser.add_argument("--device", default=detect_default_device(), help="Inference device")
    parser.add_argument("--frame-idx", type=int, default=0, help="0-based frame index to place the initial prompt on")
    parser.add_argument("--box", default=None, help="Optional box prompt as x1,y1,x2,y2")
    parser.add_argument("--select-frame", action="append", type=int, default=[], help="Additional 0-based frames where an interactive correction box should be added")
    parser.add_argument("--prompt", action="append", default=[], help="Additional non-interactive correction prompt as FRAME:x1,y1,x2,y2")
    parser.add_argument("--mask-alpha", type=float, default=0.35, help="Accepted for compatibility; rendering is box-only and does not use the mask overlay.")
    parser.add_argument("--line-width", type=int, default=2, help="Bounding box line width")
    parser.add_argument("--fps", type=float, default=30.0, help="Review playback fps")
    parser.add_argument("--no-review", action="store_true", help="Skip the interactive review/correction loop")
    parser.add_argument("--exist_ok", default="True", help="Accepted for compatibility; output folders are reused here.")
    return parser.parse_args(preprocess_argv(sys.argv[1:]))


def select_box_interactively(
    frame_store: VideoFrameStore,
    frame_idx: int,
    window: WindowController,
) -> tuple[int, int, int, int]:
    """Open the requested source frame and collect a drag-box prompt."""

    return select_box_on_frame(frame_store.get_frame(frame_idx), window)


def select_box_on_frame(
    frame: np.ndarray,
    window: WindowController,
    *,
    allow_resize: bool = True,
) -> tuple[int, int, int, int]:
    """Collect a drag-box selection on the same canvas footprint used in review."""

    selection: dict[str, tuple[int, int] | bool | None] = {
        "start": None,
        "current": None,
        "dragging": False,
        "completed": False,
    }
    frame_height, frame_width = frame.shape[:2]
    selection_canvas_shape = review_canvas_shape(frame.shape, True)

    def clamp_to_frame(x: int, y: int) -> tuple[int, int]:
        return (
            min(max(x, 0), frame_width - 1),
            min(max(y, 0), frame_height - 1),
        )

    def render_selection_canvas() -> np.ndarray:
        canvas = np.full(selection_canvas_shape, 20, dtype=np.uint8)
        canvas[:frame_height, :frame_width, :] = frame
        cv2.rectangle(canvas, (0, frame_height), (canvas.shape[1], canvas.shape[0]), (18, 18, 18), -1)

        start = selection["start"]
        current = selection["current"]
        if start is not None and current is not None:
            x1, y1 = start
            x2, y2 = current
            left, right = sorted((x1, x2))
            top, bottom = sorted((y1, y2))
            if right > left and bottom > top:
                cv2.rectangle(canvas, (left, top), (right, bottom), (0, 215, 255), 2, cv2.LINE_AA)

        draw_text(
            canvas,
            "Drag over the player. Release to accept. Esc cancel.",
            (16, 32),
            0.65,
            (255, 255, 255),
            1,
        )
        draw_text(
            canvas,
            "Selection Mode",
            (20, canvas.shape[0] - 20),
            0.6,
            (0, 215, 255),
            1,
        )
        return canvas

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        inside_frame = 0 <= x < frame_width and 0 <= y < frame_height
        if event == cv2.EVENT_LBUTTONDOWN and inside_frame:
            clamped_point = clamp_to_frame(x, y)
            selection["start"] = clamped_point
            selection["current"] = clamped_point
            selection["dragging"] = True
            selection["completed"] = False
        elif event == cv2.EVENT_MOUSEMOVE and selection["dragging"]:
            selection["current"] = clamp_to_frame(x, y)
        elif event == cv2.EVENT_LBUTTONUP and selection["dragging"]:
            selection["current"] = clamp_to_frame(x, y)
            selection["dragging"] = False
            selection["completed"] = True

    window.ensure(selection_canvas_shape, allow_resize=allow_resize)
    cv2.setMouseCallback(window.name, on_mouse)
    try:
        while True:
            if not window.is_visible():
                raise RuntimeError("No selection made.")
            display = render_selection_canvas()
            cv2.imshow(window.name, display)
            start = selection["start"]
            current = selection["current"]
            if selection["completed"] and start is not None and current is not None:
                x1, y1 = start
                x2, y2 = current
                if abs(x2 - x1) > 1 and abs(y2 - y1) > 1:
                    break
            key = cv2.waitKey(16) & 0xFF
            if key == 27:
                raise RuntimeError("No selection made.")
    finally:
        cv2.setMouseCallback(window.name, lambda *_args: None)

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


@functools.lru_cache(maxsize=1)
def ffmpeg_capabilities(ffmpeg_bin: str) -> tuple[set[str], set[str]]:
    """Return the ffmpeg hardware accelerators and encoders available locally."""

    hwaccels = set()
    encoders = set()

    hwaccel_result = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-hwaccels"],
        capture_output=True,
        text=True,
        check=False,
    )
    if hwaccel_result.returncode == 0:
        for line in hwaccel_result.stdout.splitlines():
            candidate = line.strip()
            if candidate and not candidate.endswith(":"):
                hwaccels.add(candidate)

    encoder_result = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=False,
    )
    if encoder_result.returncode == 0:
        for line in encoder_result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0][0] in {"V", "A", "S"}:
                encoders.add(parts[1])

    return hwaccels, encoders


def choose_extraction_hwaccel(ffmpeg_bin: str, preferred_device: str) -> list[str]:
    """Pick a safe ffmpeg decode accelerator for frame extraction when available."""

    hwaccels, _ = ffmpeg_capabilities(ffmpeg_bin)
    if preferred_device == "cuda" and "cuda" in hwaccels:
        return ["-hwaccel", "cuda"]
    if "cuda" in hwaccels and torch.cuda.is_available():
        return ["-hwaccel", "cuda"]
    if "auto" in hwaccels:
        return ["-hwaccel", "auto"]
    return []


def ensure_jpeg_frames(
    source_path: Path,
    frame_dir: Path,
    preferred_device: str,
    total_frames: int | None = None,
    progress_callback=None,
) -> Path:
    """Extract source video frames to JPEG once and report progress via callback."""

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
        *choose_extraction_hwaccel(ffmpeg_bin, preferred_device),
        "-i",
        str(source_path),
        "-q:v",
        "2",
        "-threads",
        "0",
        "-start_number",
        "0",
        "-progress",
        "pipe:1",
        str(output_pattern),
    ]
    extraction_error: subprocess.CalledProcessError | None = None
    for attempt, attempt_command in enumerate((command, command[:5] + command[7:]) if "-hwaccel" in command else (command,), start=1):
        for existing_frame in frame_dir.glob("*.jpg"):
            existing_frame.unlink()
        process = subprocess.Popen(
            attempt_command,
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
        if return_code == 0:
            return frame_dir
        extraction_error = subprocess.CalledProcessError(return_code, attempt_command, output=None, stderr=stderr_output)
        if attempt == 1 and "-hwaccel" in attempt_command:
            continue
        break
    if extraction_error is not None:
        raise extraction_error
    return frame_dir


def build_prompts(
    args: argparse.Namespace,
    frame_store: VideoFrameStore,
    window: WindowController,
) -> list[tuple[int, tuple[int, int, int, int]]]:
    """Resolve the initial prompt plus any seeded interactive/manual corrections."""

    prompts: list[tuple[int, tuple[int, int, int, int]]] = []

    initial_box = parse_box(args.box)
    if initial_box is None:
        initial_box = select_box_interactively(frame_store, args.frame_idx, window)
    prompts.append((args.frame_idx, initial_box))

    for frame_idx in args.select_frame:
        prompts.append((frame_idx, select_box_interactively(frame_store, frame_idx, window)))

    for prompt_value in args.prompt:
        prompts.append(parse_prompt(prompt_value))

    prompts.sort(key=lambda item: item[0])
    return prompts


def upsert_prompt(
    prompts: list[tuple[int, tuple[int, int, int, int]]],
    frame_idx: int,
    box: tuple[int, int, int, int],
) -> list[tuple[int, tuple[int, int, int, int]]]:
    """Replace or append a prompt for a frame while keeping frame order stable."""

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
    """Apply a single box prompt to the current SAM2 inference state."""

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
    """Create the reusable SAM2 state object and seed it with initial prompts.

    SAM2 loads and preprocesses all JPEG frames during `init_state`. We temporarily
    replace its internal `tqdm` call so that frame-loading progress is surfaced in
    the OpenCV modal rather than the terminal.
    """

    original_tqdm = sam2_misc.tqdm

    def modal_tqdm(iterable, desc=None, **kwargs):
        total = len(iterable) if hasattr(iterable, "__len__") else None
        for index, item in enumerate(iterable, start=1):
            if progress_callback is not None and total is not None:
                progress_callback("Loading frames into tracker", index, total, frame_source.name)
            yield item

    # SAM2's async JPEG loader is unstable on macOS/MPS here:
    # it can surface float64 tensors later in the session and it also
    # interacts badly with AppKit/OpenCV window updates from background threads.
    sam2_misc.tqdm = modal_tqdm
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            inference_state = predictor.init_state(
                video_path=str(frame_source),
                offload_video_to_cpu=True,
                offload_state_to_cpu=False,
                async_loading_frames=False,
            )
    finally:
        sam2_misc.tqdm = original_tqdm

    video_masks: dict[int, np.ndarray] = {}
    for frame_idx, box in prompts:
        video_masks[frame_idx] = add_prompt_to_state(predictor, inference_state, frame_idx, box)
    return inference_state, video_masks


def clear_tracking_from_frame(inference_state: dict, start_frame_idx: int) -> None:
    """Drop cached propagation results from a frame onward before re-tracking."""

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
    """Propagate the current prompts through the video from `start_frame_idx`.

    This reuses the existing SAM2 inference state and only recomputes the section
    of the video affected by the latest correction, which is much faster than
    rebuilding the full track from frame 0 every time.
    """

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


def build_box_cache(video_masks: dict[int, np.ndarray]) -> dict[int, tuple[int, int, int, int] | None]:
    """Convert binary masks to boxes once so the review UI can redraw cheaply."""

    return {frame_idx: mask_to_box(mask) for frame_idx, mask in video_masks.items()}


def review_canvas_shape(frame_shape: tuple[int, int, int], show_transport: bool) -> tuple[int, int, int]:
    """Return the composite canvas size used by the review window."""

    height, width = frame_shape[:2]
    return (height + (TRANSPORT_HEIGHT if show_transport else 0), width, 3)


def timeline_geometry(frame_shape: tuple[int, int, int]) -> tuple[int, int, int, int]:
    """Coordinates for the scrub bar inside the composite review canvas."""

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
    """Coordinates for the transport play/pause button."""

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
    """Draw outlined text so overlays stay readable on bright video frames."""

    x, y = origin
    cv2.putText(frame, text, (x + 2, y + 2), cv2.FONT_HERSHEY_SIMPLEX, scale, shadow_color, thickness + 2, cv2.LINE_AA)
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def draw_processing_modal(frame: np.ndarray, stage: str, current: int, total: int, detail: str = "") -> None:
    """Draw a centered progress modal over the current review frame."""

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

    draw_text(frame, "Football Tracker", (x1 + 30, y1 + 44), 1.0, (245, 245, 245), 2)
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
    """Resolve whether the latest prompt state says the player is on-screen."""

    latest_box_frame = max((prompt_frame_idx for prompt_frame_idx, _ in prompts if prompt_frame_idx <= frame_idx), default=None)
    latest_offscreen_frame = max((offscreen_frame for offscreen_frame in offscreen_frames if offscreen_frame <= frame_idx), default=None)
    if latest_box_frame is None:
        return False
    if latest_offscreen_frame is None:
        return True
    return latest_box_frame >= latest_offscreen_frame


def draw_help_panel(frame: np.ndarray, paused: bool) -> None:
    """Render the right-side control legend used during review."""

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
    """Compose a review or export frame.

    The review window uses a taller canvas with a dedicated transport strip below
    the video. The exported MP4 disables that transport strip and keeps only the
    video content plus the player box label.
    """

    rendered = frame.copy()
    if player_visible and box is not None:
        x1, y1, x2, y2 = box
        cv2.rectangle(rendered, (x1, y1), (x2, y2), (0, 215, 255), line_width, cv2.LINE_AA)
        cv2.putText(
            rendered,
            player_name,
            (x1, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 215, 255),
            2,
            cv2.LINE_AA,
        )
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
    window: WindowController,
    frame_store: VideoFrameStore,
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
    """Interactive review loop.

    Returns the updated prompts/off-screen markers, whether the track was accepted,
    the frame to restart propagation from (or `None` if no re-track is needed),
    and the frame to reopen the review window at.
    """

    current_frame = max(0, min(start_frame, len(frame_store) - 1))
    paused = False
    frame_delay = max(1, int(1000 / max(fps, 1.0)))
    paused_poll_delay = 16
    last_frame_idx = len(frame_store) - 1
    show_help = True
    scrub_state = {"requested_frame": None, "dragging": False, "toggle_pause": False}
    canvas_shape = review_canvas_shape(frame_store.frame_shape, True)

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        left, top, right, bottom = timeline_geometry(canvas_shape)
        on_timeline = left <= x <= right and (top - 20) <= y <= (bottom + 20)
        play_rect = play_button_geometry(canvas_shape)
        if event == cv2.EVENT_LBUTTONDOWN and point_in_rect(x, y, play_rect):
            scrub_state["toggle_pause"] = True
        elif event == cv2.EVENT_LBUTTONDOWN and on_timeline:
            scrub_state["dragging"] = True
            scrub_state["requested_frame"] = frame_from_timeline_x(x, canvas_shape, len(frame_store))
        elif event == cv2.EVENT_MOUSEMOVE and scrub_state["dragging"]:
            scrub_state["requested_frame"] = frame_from_timeline_x(x, canvas_shape, len(frame_store))
        elif event == cv2.EVENT_LBUTTONUP:
            if scrub_state["dragging"]:
                scrub_state["requested_frame"] = frame_from_timeline_x(x, canvas_shape, len(frame_store))
            scrub_state["dragging"] = False

    window.ensure(canvas_shape)
    cv2.setMouseCallback(window.name, on_mouse)
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

            frame = frame_store.get_frame(current_frame)
            rendered = render_frame(
                frame,
                video_masks.get(current_frame),
                box_cache.get(current_frame),
                is_player_visible(current_frame, prompts, offscreen_frames),
                player_name,
                alpha,
                line_width,
                current_frame,
                fps,
                len(frame_store),
                show_help,
                paused,
            )
            cv2.imshow(window.name, rendered)
            key = cv2.waitKey(paused_poll_delay if paused else frame_delay) & 0xFF

            if not window.is_visible():
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
                corrected_box = select_box_on_frame(frame, window, allow_resize=False)
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
        cv2.setMouseCallback(window.name, lambda *_args: None)
    return prompts, offscreen_frames, True, None, last_frame_idx


class FFmpegRenderWriter:
    """Stream rendered frames into ffmpeg so encoding can use faster codecs."""

    def __init__(self, output_path: Path, width: int, height: int, fps: float, encoder: str):
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise RuntimeError("ffmpeg is required for accelerated rendering.")
        self.output_path = output_path
        self._process = subprocess.Popen(
            [
                ffmpeg_bin,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-s",
                f"{width}x{height}",
                "-r",
                f"{fps:.6f}",
                "-i",
                "pipe:0",
                "-an",
                "-c:v",
                encoder,
                *([] if encoder != "h264_nvenc" else ["-preset", "p4"]),
                *([] if encoder != "libx264" else ["-preset", "veryfast", "-crf", "18"]),
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def write(self, frame: np.ndarray) -> None:
        if self._process.stdin is None:
            raise RuntimeError("ffmpeg render pipe is not available.")
        try:
            self._process.stdin.write(frame.tobytes())
        except BrokenPipeError as exc:
            stderr_output = self._process.stderr.read().decode("utf-8", errors="replace") if self._process.stderr else ""
            raise RuntimeError(f"ffmpeg encoder exited early: {stderr_output.strip()}") from exc

    def close(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        stderr_output = self._process.stderr.read().decode("utf-8", errors="replace") if self._process.stderr else ""
        return_code = self._process.wait()
        if return_code != 0:
            raise RuntimeError(stderr_output.strip() or f"ffmpeg exited with code {return_code}")


def choose_render_writer(width: int, height: int, fps: float, output_path: Path):
    """Prefer ffmpeg with NVENC when available and fall back to OpenCV otherwise."""

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        _, encoders = ffmpeg_capabilities(ffmpeg_bin)
        for encoder in ("h264_nvenc", "libx264"):
            if encoder in encoders:
                return FFmpegRenderWriter(output_path, width, height, fps, encoder)

    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"Could not open output {output_path}")
    return writer


def render_output(
    frame_store: VideoFrameStore,
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
    """Write the final accepted track to MP4 without review controls."""

    if len(frame_store) <= 0:
        raise RuntimeError("No source frames available for rendering.")
    height, width = frame_store.frame_shape[:2]
    writer = choose_render_writer(width, height, fps, output_path)

    try:
        for frame_index in range(len(frame_store)):
            if progress_callback is not None:
                progress_callback("Rendering output video", frame_index + 1, len(frame_store), output_path.name)
            frame = frame_store.get_frame(frame_index)
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
                    len(frame_store),
                    False,
                    False,
                    False,
                    None,
                )
            )
    finally:
        if hasattr(writer, "release"):
            writer.release()
        else:
            writer.close()


def show_review_modal(
    window: WindowController,
    frame_store: VideoFrameStore,
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
    """Paint a progress modal over the review canvas during long-running work."""

    frame = frame_store.get_frame(frame_idx)
    rendered = render_frame(
        frame,
        video_masks.get(frame_idx),
        box_cache.get(frame_idx),
        is_player_visible(frame_idx, prompts, offscreen_frames),
        player_name,
        alpha,
        line_width,
        frame_idx,
        fps,
        len(frame_store),
        False,
        True,
        True,
        (stage, current, total, detail),
    )
    window.ensure(rendered.shape)
    cv2.imshow(window.name, rendered)
    key = cv2.waitKey(1) & 0xFF
    if key in (27, ord("q")):
        raise UserAbort("Aborted during processing.")
    if not window.is_visible():
        raise UserAbort("Window closed during processing.")


def main() -> int:
    """Run the full prompt, track, review, and export workflow."""

    output_path: Path | None = None
    frame_store: VideoFrameStore | ImageSequenceFrameStore | None = None
    try:
        args = parse_args()
        window = WindowController(APP_WINDOW_NAME)
        source_path = Path(args.source).expanduser().resolve()
        project_path = Path(args.project).expanduser().resolve()
        output_dir = project_path / args.name
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{source_path.stem}-tracked.mp4"
        frame_dir = output_dir / f"{source_path.stem}-frames"
        frame_store = VideoFrameStore(source_path)
        review_fps = args.fps or frame_store.fps

        prompts = build_prompts(args, frame_store, window)
        offscreen_frames: set[int] = set()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            predictor = SAM2VideoPredictor.from_pretrained(
                args.model_id,
                device=args.device,
                fill_hole_area=0,
            )
        extraction_preview_frame_idx = min(max(args.frame_idx, 0), len(frame_store) - 1)
        extraction_progress_callback = lambda stage, current, total, detail: show_review_modal(
            window,
            frame_store,
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
            args.device,
            total_frames=len(frame_store),
            progress_callback=extraction_progress_callback,
        )
        frame_store.close()
        frame_store = ImageSequenceFrameStore(frame_source, frame_store.fps)
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
                preview_frame_idx = max(0, min(review_start_frame, len(frame_store) - 1))
                progress_callback = lambda stage, current, total, detail: show_review_modal(
                    window,
                    frame_store,
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
                window,
                frame_store,
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
            window,
            frame_store,
            min(review_start_frame, len(frame_store) - 1),
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
            frame_store,
            frame_store.fps,
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
        if output_path is not None and output_path.exists():
            output_path.unlink()
        cv2.destroyAllWindows()
        return 0
    finally:
        if frame_store is not None:
            frame_store.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
