"""Video upload and listing endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Response, UploadFile, status
from pydantic import BaseModel

from football_tracker.core.tracking_service import delete_tracking_state_for_video
from football_tracker.core.video_store import (
    VideoRecord,
    delete_video,
    list_videos,
    save_upload,
)

router = APIRouter()


class VideoAssetView(BaseModel):
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
    source_path: str
    media_url: str

    @classmethod
    def from_record(cls, record: VideoRecord) -> VideoAssetView:
        return cls(
            id=record.id,
            filename=record.filename,
            stored_name=record.stored_name,
            content_type=record.content_type,
            size_bytes=record.size_bytes,
            width=record.width,
            height=record.height,
            fps=record.fps,
            duration_seconds=record.duration_seconds,
            frame_count=record.frame_count,
            created_at=record.created_at,
            source_path=record.source_path,
            media_url=record.media_url,
        )


UploadVideoFile = Annotated[UploadFile, File(...)]


@router.get("")
def list_video_assets() -> list[VideoAssetView]:
    # Listing is enough for the UI to populate the source dropdown and preview
    # metadata because media files are served separately from /media/uploads.
    return [VideoAssetView.from_record(record) for record in list_videos()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_video(file: UploadVideoFile) -> VideoAssetView:
    record = await save_upload(file)
    return VideoAssetView.from_record(record)


@router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_video_asset(video_id: str) -> Response:
    # Delete tracker outputs first so no job artifacts are left behind for a
    # source the user has explicitly removed.
    delete_tracking_state_for_video(video_id)
    delete_video(video_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
