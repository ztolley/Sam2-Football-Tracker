"""Tracking job and prompt action routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse

from football_tracker.core.tracking_service import (
    TrackingJobCreate,
    TrackingJobView,
    TrackingOffscreenCreate,
    TrackingSelectionCreate,
    create_job,
    get_job,
    list_jobs,
    mark_offscreen,
    render_job_output,
    submit_selection,
    wait_for_job_update,
)

router = APIRouter()


@router.get("")
def list_tracking_jobs() -> list[TrackingJobView]:
    return list_jobs()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_tracking_job(payload: TrackingJobCreate) -> TrackingJobView:
    return create_job(payload)


@router.post("/selection", status_code=status.HTTP_202_ACCEPTED)
def submit_tracking_selection(payload: TrackingSelectionCreate) -> TrackingJobView:
    return submit_selection(payload)


@router.post("/offscreen", status_code=status.HTTP_202_ACCEPTED)
def mark_tracking_offscreen(payload: TrackingOffscreenCreate) -> TrackingJobView:
    return mark_offscreen(payload)


@router.get("/{job_id}")
def get_tracking_job(job_id: str) -> TrackingJobView:
    return get_job(job_id)


@router.get("/{job_id}/events")
async def stream_tracking_job(job_id: str, request: Request) -> StreamingResponse:
    get_job(job_id)

    async def event_stream():
        # Each client keeps a single SSE stream open while a job is active.
        # The service wakes this generator only when job state actually changes.
        last_updated_at = None
        while True:
            if await request.is_disconnected():
                break

            job = await asyncio.to_thread(
                wait_for_job_update,
                job_id,
                last_updated_at,
                10.0,
            )
            if job is None:
                yield ": keep-alive\n\n"
                continue

            last_updated_at = job.updated_at
            yield f"event: job\ndata: {job.model_dump_json()}\n\n"

            if job.status in {"completed", "failed"}:
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{job_id}/render", status_code=status.HTTP_202_ACCEPTED)
def render_tracking_job_output(job_id: str) -> TrackingJobView:
    return render_job_output(job_id)
