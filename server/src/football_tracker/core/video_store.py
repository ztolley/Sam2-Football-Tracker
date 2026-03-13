"""Filesystem-backed video upload registry for the first browser iteration."""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
from fastapi import HTTPException, UploadFile, status

from football_tracker.core.settings import get_settings


@dataclass(slots=True)
class VideoRecord:
    """Metadata persisted alongside each uploaded source video."""

    id: str
    filename: str
    stored_name: str
    content_type: str
    size_bytes: int
    width: int
    height: int
    fps: float
    duration_seconds: float
    frame_count: int
    created_at: str
    playback_name: str | None = None

    @property
    def source_path(self) -> str:
        settings = get_settings()
        return str((settings.upload_root / self.stored_name).resolve())

    @property
    def media_url(self) -> str:
        filename = self.playback_name or self.stored_name
        return f"/media/uploads/{filename}"

    @property
    def playback_path(self) -> str:
        settings = get_settings()
        filename = self.playback_name or self.stored_name
        return str((settings.upload_root / filename).resolve())


def _record_path(video_id: str) -> Path:
    return get_settings().upload_root / f"{video_id}.json"


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem or "video"
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", stem).strip("-_")[:64] or "video"


def _playback_name_for(stored_name: str) -> str:
    return f"{Path(stored_name).stem}-playback.mp4"


def _persist_record(record: VideoRecord) -> None:
    with _record_path(record.id).open("w", encoding="utf-8") as file_handle:
        json.dump(asdict(record), file_handle, indent=2)


def _video_metadata(path: Path) -> tuple[int, int, float, float, int]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file could not be opened as a video.",
        )

    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        capture.release()

    duration_seconds = (frame_count / fps) if fps > 0 else 0.0
    return width, height, fps, duration_seconds, frame_count


def _transcode_playback_asset(source_path: Path, playback_path: Path) -> None:
    # Browsers are much pickier than OpenCV/ffmpeg, so every upload gets a
    # normalized H.264/AAC playback copy for the web UI.
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ffmpeg is required to prepare browser playback assets.",
        )

    playback_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(playback_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        with contextlib.suppress(FileNotFoundError):
            playback_path.unlink()
        detail = (
            exc.stderr.strip().splitlines()[-1]
            if exc.stderr.strip()
            else "Unknown ffmpeg error"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unable to create browser playback asset: {detail}",
        ) from exc


def _ensure_playback_asset(record: VideoRecord) -> VideoRecord:
    playback_path = Path(record.playback_path)
    if record.playback_name and playback_path.exists():
        return record

    playback_name = record.playback_name or _playback_name_for(record.stored_name)
    playback_path = get_settings().upload_root / playback_name
    _transcode_playback_asset(Path(record.source_path), playback_path)
    updated_record = replace(record, playback_name=playback_name)
    _persist_record(updated_record)
    return updated_record


def list_videos() -> list[VideoRecord]:
    # Older uploads may pre-date playback copies, so listing also acts as a
    # lazy migration step for existing local data.
    records: list[VideoRecord] = []
    for metadata_path in sorted(
        get_settings().upload_root.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ):
        with metadata_path.open("r", encoding="utf-8") as file_handle:
            records.append(_ensure_playback_asset(VideoRecord(**json.load(file_handle))))
    return records


def get_video(video_id: str) -> VideoRecord:
    metadata_path = _record_path(video_id)
    if not metadata_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")

    with metadata_path.open("r", encoding="utf-8") as file_handle:
        return _ensure_playback_asset(VideoRecord(**json.load(file_handle)))


def delete_video(video_id: str) -> VideoRecord:
    record = get_video(video_id)
    metadata_path = _record_path(video_id)
    source_path = Path(record.source_path)
    playback_path = Path(record.playback_path)

    if metadata_path.exists():
        metadata_path.unlink()
    if source_path.exists():
        source_path.unlink()
    if playback_path.exists() and playback_path != source_path:
        playback_path.unlink()

    return record


async def save_upload(file: UploadFile) -> VideoRecord:
    settings = get_settings()
    suffix = Path(file.filename or "video.mp4").suffix.lower() or ".mp4"
    video_id = str(uuid4())
    stored_name = f"{video_id}-{_safe_stem(file.filename or 'video')}{suffix}"
    target_path = settings.upload_root / stored_name
    playback_name = _playback_name_for(stored_name)
    playback_path = settings.upload_root / playback_name

    size_bytes = 0
    try:
        # Stream uploads to disk to avoid loading large local test videos into
        # memory before OpenCV and ffmpeg inspect them.
        with target_path.open("wb") as file_handle:
            while chunk := await file.read(1024 * 1024):
                size_bytes += len(chunk)
                file_handle.write(chunk)

        width, height, fps, duration_seconds, frame_count = _video_metadata(target_path)
        _transcode_playback_asset(target_path, playback_path)
        record = VideoRecord(
            id=video_id,
            filename=file.filename or stored_name,
            stored_name=stored_name,
            content_type=file.content_type or "application/octet-stream",
            size_bytes=size_bytes,
            width=width,
            height=height,
            fps=fps,
            duration_seconds=duration_seconds,
            frame_count=frame_count,
            created_at=datetime.now(UTC).isoformat(),
            playback_name=playback_name,
        )
        _persist_record(record)
        return record
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            target_path.unlink()
        with contextlib.suppress(FileNotFoundError):
            playback_path.unlink()
        raise
    finally:
        await file.close()
