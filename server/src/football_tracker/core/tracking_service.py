"""Background tracking service for the web API."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Condition, Lock, Thread
from typing import Any
from uuid import uuid4

import cv2
import numpy as np
import torch
from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sam2.sam2_video_predictor import SAM2VideoPredictor

from football_tracker.core.settings import get_settings
from football_tracker.core.video_store import VideoRecord, get_video
from football_tracker.tracking.interactive import (
    ImageSequenceFrameStore,
    VideoFrameStore,
    add_prompt_to_state,
    build_box_cache,
    clear_tracking_from_frame,
    collect_masks,
    ensure_jpeg_frames,
    initialize_tracking_state,
    render_output,
    upsert_prompt,
)


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class TrackingActionKind(StrEnum):
    selection = "selection"
    offscreen = "offscreen"


class TrackingBox(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)


class TrackingActionView(BaseModel):
    kind: TrackingActionKind
    time_seconds: float = Field(..., ge=0)
    box: TrackingBox | None = None
    created_at: datetime


class TrackingJobCreate(BaseModel):
    video_id: str = Field(..., min_length=1)
    player_name: str = Field(default="selected player", min_length=1)


class TrackingSelectionCreate(BaseModel):
    video_id: str = Field(..., min_length=1)
    player_name: str = Field(default="selected player", min_length=1)
    time_seconds: float = Field(..., ge=0)
    box: TrackingBox


class TrackingOffscreenCreate(BaseModel):
    video_id: str = Field(..., min_length=1)
    time_seconds: float = Field(..., ge=0)


class TrackingJobView(BaseModel):
    id: str
    video_id: str
    video_filename: str
    status: JobStatus
    source_path: str
    player_name: str
    created_at: datetime
    updated_at: datetime
    progress_percent: int = 0
    processing_detail: str = "Awaiting selection"
    latest_time_seconds: float | None = None
    latest_box: TrackingBox | None = None
    player_visible: bool = False
    processed_media_url: str | None = None
    rendered_media_url: str | None = None
    actions: list[TrackingActionView] = Field(default_factory=list)


@dataclass(slots=True)
class TrackingJobRuntime:
    """In-memory runtime state that complements the persisted job snapshot."""

    prompts: list[tuple[int, tuple[int, int, int, int]]] = field(default_factory=list)
    offscreen_frames: set[int] = field(default_factory=set)
    predictor: SAM2VideoPredictor | None = None
    frame_source: Path | None = None
    frame_store: ImageSequenceFrameStore | None = None
    inference_state: dict[str, Any] | None = None
    video_masks: dict[int, np.ndarray] = field(default_factory=dict)
    box_cache: dict[int, tuple[int, int, int, int] | None] = field(default_factory=dict)


# The first web iteration keeps a single active job per video in memory and
# persists only the serialized job view plus rendered media on disk.
_jobs: dict[str, TrackingJobView] = {}
_job_ids_by_video: dict[str, str] = {}
_job_runtimes: dict[str, TrackingJobRuntime] = {}
_jobs_lock = Lock()
# SSE listeners block on this condition and wake up whenever job state changes.
_job_updates = Condition(_jobs_lock)


def _job_output_dir(job_id: str) -> Path:
    return get_settings().job_root / job_id


def _job_metadata_path(job_id: str) -> Path:
    return _job_output_dir(job_id) / "job.json"


def _job_media_url(job_id: str, filename: str, updated_at: datetime) -> str:
    version = int(updated_at.timestamp() * 1000)
    return f"/media/jobs/{job_id}/{filename}?v={version}"


def _prompt_box_from_selection(box: TrackingBox) -> tuple[int, int, int, int]:
    return box.x, box.y, box.x + box.width, box.y + box.height


def _detect_bright_blob_box(frame: np.ndarray) -> tuple[int, int, int, int] | None:
    grayscale = np.max(frame, axis=2)
    mask = (grayscale >= 200).astype(np.uint8) * 255
    coordinates = cv2.findNonZero(mask)
    if coordinates is None:
        return None
    x, y, width, height = cv2.boundingRect(coordinates)
    return x, y, x + width, y + height


def _persist_job_metadata(job: TrackingJobView) -> None:
    output_dir = _job_output_dir(job.id)
    output_dir.mkdir(parents=True, exist_ok=True)
    with _job_metadata_path(job.id).open("w", encoding="utf-8") as file_handle:
        json.dump(job.model_dump(mode="json"), file_handle, indent=2)


def _clone_job(job: TrackingJobView) -> TrackingJobView:
    return job.model_copy(deep=True)


def _sorted_jobs() -> list[TrackingJobView]:
    with _jobs_lock:
        jobs = sorted(_jobs.values(), key=lambda job: job.updated_at, reverse=True)
        return [_clone_job(job) for job in jobs]


def _load_job(job_id: str) -> TrackingJobView:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def _load_runtime(job_id: str) -> TrackingJobRuntime:
    with _jobs_lock:
        runtime = _job_runtimes.get(job_id)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tracking state not found",
        )
    return runtime


def _time_to_frame_index(video: VideoRecord, time_seconds: float) -> int:
    if video.frame_count <= 0:
        return 0
    if video.fps <= 0:
        return max(0, min(int(round(time_seconds * 30.0)), video.frame_count - 1))
    return max(0, min(int(round(time_seconds * video.fps)), video.frame_count - 1))


def _start_processing(job: TrackingJobView, detail: str) -> None:
    if job.status == JobStatus.running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tracking is already processing for this video.",
        )
    job.status = JobStatus.running
    job.progress_percent = 2
    job.processing_detail = detail
    job.updated_at = datetime.now(UTC)


def _set_job_progress(job_id: str, progress_percent: int, detail: str) -> None:
    with _job_updates:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.progress_percent = max(0, min(progress_percent, 100))
        job.processing_detail = detail
        job.updated_at = datetime.now(UTC)
        _job_updates.notify_all()


def _progress_callback(
    job_id: str,
    base_percent: int,
    span_percent: int,
):
    def report(stage: str, current: int, total: int, detail: str) -> None:
        total = max(total, 1)
        current = max(0, min(current, total))
        progress = base_percent + int((current / total) * span_percent)
        suffix = f" - {detail}" if detail else ""
        _set_job_progress(job_id, progress, f"{stage}{suffix}")

    return report


def _complete_job(
    job_id: str,
    *,
    kind: TrackingActionKind,
    time_seconds: float,
    box: TrackingBox | None,
    processed_media_url: str,
) -> None:
    completed_at = datetime.now(UTC)
    action = TrackingActionView(
        kind=kind,
        time_seconds=time_seconds,
        box=box,
        created_at=completed_at,
    )
    with _job_updates:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.actions.append(action)
        job.latest_time_seconds = time_seconds
        job.latest_box = box if kind == TrackingActionKind.selection else None
        job.player_visible = kind == TrackingActionKind.selection
        job.processed_media_url = processed_media_url
        job.progress_percent = 100
        job.processing_detail = "Ready"
        job.status = JobStatus.completed
        job.updated_at = completed_at
        snapshot = _clone_job(job)
        _job_updates.notify_all()
    _persist_job_metadata(snapshot)


def _finish_render(job_id: str, rendered_media_url: str) -> None:
    completed_at = datetime.now(UTC)
    with _job_updates:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.rendered_media_url = rendered_media_url
        job.progress_percent = 100
        job.processing_detail = "Rendered movie ready"
        job.status = JobStatus.completed
        job.updated_at = completed_at
        snapshot = _clone_job(job)
        _job_updates.notify_all()
    _persist_job_metadata(snapshot)


def _fail_job(job_id: str, message: str) -> None:
    with _job_updates:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.status = JobStatus.failed
        job.processing_detail = message
        job.progress_percent = 100
        job.updated_at = datetime.now(UTC)
        snapshot = _clone_job(job)
        _job_updates.notify_all()
    _persist_job_metadata(snapshot)


def _get_or_create_job(video_id: str, player_name: str) -> TrackingJobView:
    with _jobs_lock:
        existing_job_id = _job_ids_by_video.get(video_id)
        if existing_job_id is not None:
            job = _jobs[existing_job_id]
            if player_name.strip():
                job.player_name = player_name.strip()
            return job

    video = get_video(video_id)
    created_at = datetime.now(UTC)
    job = TrackingJobView(
        id=str(uuid4()),
        video_id=video.id,
        video_filename=video.filename,
        status=JobStatus.queued,
        source_path=video.source_path,
        player_name=player_name.strip() or "selected player",
        created_at=created_at,
        updated_at=created_at,
    )
    with _job_updates:
        _jobs[job.id] = job
        _job_ids_by_video[video.id] = job.id
        _job_runtimes[job.id] = TrackingJobRuntime()
        _job_updates.notify_all()
    _persist_job_metadata(job)
    return job


def _render_processed_video(
    *,
    job: TrackingJobView,
    runtime: TrackingJobRuntime,
    filename: str,
    progress_base: int,
    progress_span: int,
) -> str:
    settings = get_settings()
    output_dir = _job_output_dir(job.id)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename

    if settings.tracker_backend == "mock":
        frame_store = VideoFrameStore(Path(job.source_path))
        try:
            render_output(
                frame_store,
                frame_store.fps,
                output_path,
                runtime.video_masks,
                runtime.box_cache,
                runtime.prompts,
                runtime.offscreen_frames,
                job.player_name,
                0.35,
                settings.tracker_line_width,
                progress_callback=_progress_callback(job.id, progress_base, progress_span),
            )
        finally:
            frame_store.close()
    else:
        if runtime.frame_store is None:
            raise RuntimeError("Tracker frames are not available for rendering.")
        render_output(
            runtime.frame_store,
            runtime.frame_store.fps,
            output_path,
            runtime.video_masks,
            runtime.box_cache,
            runtime.prompts,
            runtime.offscreen_frames,
            job.player_name,
            0.35,
            settings.tracker_line_width,
            progress_callback=_progress_callback(job.id, progress_base, progress_span),
        )

    return _job_media_url(job.id, filename, datetime.now(UTC))


def _ensure_runtime_ready(job: TrackingJobView, runtime: TrackingJobRuntime) -> VideoRecord:
    settings = get_settings()
    video = get_video(job.video_id)
    output_dir = _job_output_dir(job.id)
    output_dir.mkdir(parents=True, exist_ok=True)

    if runtime.predictor is None:
        _set_job_progress(job.id, 8, "Loading tracker model")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            runtime.predictor = SAM2VideoPredictor.from_pretrained(
                settings.sam2_model_id,
                device=settings.sam2_device,
                fill_hole_area=0,
            )

    if runtime.frame_source is None:
        # Frames are extracted once per job and then reused across corrections
        # and final renders so later actions are much cheaper.
        runtime.frame_source = ensure_jpeg_frames(
            Path(job.source_path),
            output_dir / "frames",
            settings.sam2_device,
            total_frames=video.frame_count,
            progress_callback=_progress_callback(job.id, 12, 24),
        )

    if runtime.frame_store is None:
        runtime.frame_store = ImageSequenceFrameStore(runtime.frame_source, video.fps)

    if runtime.inference_state is None:
        if not runtime.prompts:
            raise RuntimeError("Create a player selection before starting tracker processing.")
        runtime.inference_state, runtime.video_masks = initialize_tracking_state(
            runtime.predictor,
            runtime.frame_source,
            runtime.prompts,
            progress_callback=_progress_callback(job.id, 36, 16),
        )
        runtime.box_cache = build_box_cache(runtime.video_masks)

    return video


def _build_mock_tracks(video: VideoRecord, runtime: TrackingJobRuntime, source_path: Path) -> None:
    # The mock backend follows a bright object deterministically so automated
    # tests can assert exact output without relying on ML inference.
    prompt_map = dict(runtime.prompts)
    offscreen_frames = runtime.offscreen_frames
    runtime.video_masks = {}
    runtime.box_cache = {}
    frame_store = VideoFrameStore(source_path)
    try:
        for frame_idx in range(max(video.frame_count, 0)):
            latest_prompt_frame = max(
                (prompt_frame for prompt_frame in prompt_map if prompt_frame <= frame_idx),
                default=None,
            )
            latest_offscreen_frame = max(
                (
                    offscreen_frame
                    for offscreen_frame in offscreen_frames
                    if offscreen_frame <= frame_idx
                ),
                default=None,
            )
            if latest_prompt_frame is None:
                runtime.box_cache[frame_idx] = None
                continue
            if latest_offscreen_frame is not None and latest_offscreen_frame > latest_prompt_frame:
                runtime.box_cache[frame_idx] = None
                continue

            detected_box = _detect_bright_blob_box(frame_store.get_frame(frame_idx))
            runtime.box_cache[frame_idx] = detected_box or prompt_map[latest_prompt_frame]
    finally:
        frame_store.close()


def _run_selection(job_id: str, time_seconds: float, box: TrackingBox) -> None:
    try:
        job = _load_job(job_id)
        runtime = _load_runtime(job_id)
        video = get_video(job.video_id)
        frame_idx = _time_to_frame_index(video, time_seconds)
        prompt_box = _prompt_box_from_selection(box)

        with _jobs_lock:
            runtime.prompts = upsert_prompt(runtime.prompts, frame_idx, prompt_box)
            runtime.offscreen_frames.discard(frame_idx)

        settings = get_settings()
        if settings.tracker_backend == "mock":
            _set_job_progress(job_id, 18, "Applying selection")
            _build_mock_tracks(video, runtime, Path(job.source_path))
            processed_media_url = _render_processed_video(
                job=job,
                runtime=runtime,
                filename="processed.mp4",
                progress_base=30,
                progress_span=70,
            )
        else:
            # Real tracking reuses existing inference state, clearing from the
            # updated frame onward before applying the new prompt.
            _ensure_runtime_ready(job, runtime)
            if runtime.inference_state is None or runtime.predictor is None:
                raise RuntimeError("Tracker state could not be initialized.")
            _set_job_progress(job_id, 52, "Updating tracking prompt")
            clear_tracking_from_frame(runtime.inference_state, frame_idx)
            runtime.video_masks[frame_idx] = add_prompt_to_state(
                runtime.predictor,
                runtime.inference_state,
                frame_idx,
                prompt_box,
            )
            with torch.inference_mode():
                runtime.video_masks = collect_masks(
                    runtime.predictor,
                    runtime.inference_state,
                    frame_idx,
                    runtime.video_masks,
                    progress_callback=_progress_callback(job.id, 56, 26),
                )
            runtime.box_cache = build_box_cache(runtime.video_masks)
            processed_media_url = _render_processed_video(
                job=job,
                runtime=runtime,
                filename="processed.mp4",
                progress_base=82,
                progress_span=18,
            )

        _complete_job(
            job_id,
            kind=TrackingActionKind.selection,
            time_seconds=time_seconds,
            box=box,
            processed_media_url=processed_media_url,
        )
    except Exception as exc:  # pragma: no cover - failure path exercised indirectly
        _fail_job(job_id, str(exc))


def _run_offscreen(job_id: str, time_seconds: float) -> None:
    try:
        job = _load_job(job_id)
        runtime = _load_runtime(job_id)
        video = get_video(job.video_id)
        frame_idx = _time_to_frame_index(video, time_seconds)

        with _jobs_lock:
            runtime.offscreen_frames.add(frame_idx)

        settings = get_settings()
        if settings.tracker_backend == "mock":
            _set_job_progress(job_id, 22, "Marking player off-screen")
            _build_mock_tracks(video, runtime, Path(job.source_path))
        else:
            _ensure_runtime_ready(job, runtime)
            _set_job_progress(job_id, 78, "Rendering updated output")

        processed_media_url = _render_processed_video(
            job=job,
            runtime=runtime,
            filename="processed.mp4",
            progress_base=24 if settings.tracker_backend == "mock" else 80,
            progress_span=76 if settings.tracker_backend == "mock" else 20,
        )
        _complete_job(
            job_id,
            kind=TrackingActionKind.offscreen,
            time_seconds=time_seconds,
            box=None,
            processed_media_url=processed_media_url,
        )
    except Exception as exc:  # pragma: no cover - failure path exercised indirectly
        _fail_job(job_id, str(exc))


def _run_render(job_id: str) -> None:
    try:
        job = _load_job(job_id)
        runtime = _load_runtime(job_id)
        if not runtime.prompts:
            raise RuntimeError("Create a player selection before rendering a movie.")

        settings = get_settings()
        if settings.tracker_backend == "mock":
            video = get_video(job.video_id)
            _build_mock_tracks(video, runtime, Path(job.source_path))
            rendered_media_url = _render_processed_video(
                job=job,
                runtime=runtime,
                filename="final.mp4",
                progress_base=18,
                progress_span=82,
            )
        else:
            _ensure_runtime_ready(job, runtime)
            rendered_media_url = _render_processed_video(
                job=job,
                runtime=runtime,
                filename="final.mp4",
                progress_base=18,
                progress_span=82,
            )

        _finish_render(job_id, rendered_media_url)
    except Exception as exc:  # pragma: no cover - failure path exercised indirectly
        _fail_job(job_id, str(exc))


def reset_tracking_registry() -> None:
    """Clear all in-memory tracker state, primarily for tests."""

    with _job_updates:
        runtimes = list(_job_runtimes.values())
        _jobs.clear()
        _job_ids_by_video.clear()
        _job_runtimes.clear()
        _job_updates.notify_all()
    for runtime in runtimes:
        if runtime.frame_store is not None:
            runtime.frame_store.close()


def delete_tracking_state_for_video(video_id: str) -> None:
    """Remove both live tracker state and persisted job artifacts for a video."""

    with _job_updates:
        job_id = _job_ids_by_video.pop(video_id, None)
        job = _jobs.pop(job_id, None) if job_id is not None else None
        runtime = _job_runtimes.pop(job_id, None) if job_id is not None else None
        _job_updates.notify_all()

    if runtime is not None and runtime.frame_store is not None:
        runtime.frame_store.close()
    if job is not None:
        shutil.rmtree(_job_output_dir(job.id), ignore_errors=True)

    for metadata_path in get_settings().job_root.glob("*/job.json"):
        try:
            with metadata_path.open("r", encoding="utf-8") as file_handle:
                payload = json.load(file_handle)
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("video_id") == video_id:
            shutil.rmtree(metadata_path.parent, ignore_errors=True)


def list_jobs() -> list[TrackingJobView]:
    return _sorted_jobs()


def get_job(job_id: str) -> TrackingJobView:
    return _clone_job(_load_job(job_id))


def wait_for_job_update(
    job_id: str,
    last_updated_at: datetime | None,
    timeout_seconds: float = 10.0,
) -> TrackingJobView | None:
    """Block until a job changes or the SSE keep-alive timeout expires."""

    with _job_updates:
        changed = _job_updates.wait_for(
            lambda: job_id not in _jobs
            or last_updated_at is None
            or _jobs[job_id].updated_at > last_updated_at,
            timeout=timeout_seconds,
        )
        job = _jobs.get(job_id)
        if job is None or not changed:
            return None
        return _clone_job(job)


def create_job(payload: TrackingJobCreate) -> TrackingJobView:
    job = _get_or_create_job(payload.video_id, payload.player_name)
    with _job_updates:
        job.updated_at = datetime.now(UTC)
        snapshot = _clone_job(job)
        _job_updates.notify_all()
    _persist_job_metadata(snapshot)
    return snapshot


def submit_selection(payload: TrackingSelectionCreate) -> TrackingJobView:
    job = _get_or_create_job(payload.video_id, payload.player_name)
    with _job_updates:
        _start_processing(job, "Starting tracker")
        snapshot = _clone_job(job)
        _job_updates.notify_all()
    _persist_job_metadata(snapshot)
    # Requests return immediately while the background thread updates the job
    # that the browser is subscribed to over SSE.
    Thread(
        target=_run_selection,
        kwargs={
            "job_id": job.id,
            "time_seconds": payload.time_seconds,
            "box": payload.box,
        },
        daemon=True,
    ).start()
    return snapshot


def mark_offscreen(payload: TrackingOffscreenCreate) -> TrackingJobView:
    with _jobs_lock:
        existing_job_id = _job_ids_by_video.get(payload.video_id)
        job = _jobs.get(existing_job_id) if existing_job_id else None
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Create a player selection before marking them off-screen.",
        )

    runtime = _load_runtime(job.id)
    if not runtime.prompts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Create a player selection before marking them off-screen.",
        )

    with _job_updates:
        _start_processing(job, "Marking player off-screen")
        snapshot = _clone_job(job)
        _job_updates.notify_all()
    _persist_job_metadata(snapshot)
    Thread(
        target=_run_offscreen,
        kwargs={
            "job_id": job.id,
            "time_seconds": payload.time_seconds,
        },
        daemon=True,
    ).start()
    return snapshot


def render_job_output(job_id: str) -> TrackingJobView:
    job = _load_job(job_id)
    runtime = _load_runtime(job_id)
    if not runtime.prompts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Create a player selection before rendering a movie.",
        )

    with _job_updates:
        _start_processing(job, "Rendering movie")
        snapshot = _clone_job(job)
        _job_updates.notify_all()
    _persist_job_metadata(snapshot)
    Thread(target=_run_render, kwargs={"job_id": job.id}, daemon=True).start()
    return snapshot
