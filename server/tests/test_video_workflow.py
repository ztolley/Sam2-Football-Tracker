from __future__ import annotations

from pathlib import Path
from time import sleep

import cv2
import numpy as np
from fastapi.testclient import TestClient

from football_tracker.core.settings import get_settings
from football_tracker.core.tracking_service import reset_tracking_registry
from football_tracker.main import create_app


def _write_sample_video(path: Path) -> None:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        10.0,
        (64, 48),
    )
    if not writer.isOpened():
        raise RuntimeError("Unable to create test video")

    try:
        for index in range(8):
            frame = np.full((48, 64, 3), index * 20, dtype=np.uint8)
            writer.write(frame)
    finally:
        writer.release()


def _wait_for_completion(client: TestClient, job_id: str) -> dict:
    for _ in range(20):
        response = client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] != "running":
            return payload
        sleep(0.1)
    raise AssertionError("Job did not complete in time")


def test_upload_select_and_mark_offscreen(monkeypatch, tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    media_root = tmp_path / "media"
    job_root = tmp_path / "jobs"
    monkeypatch.setenv("SAM2_UPLOAD_ROOT", str(upload_root))
    monkeypatch.setenv("SAM2_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("SAM2_JOB_ROOT", str(job_root))
    monkeypatch.setenv("SAM2_TRACKER_BACKEND", "mock")
    get_settings.cache_clear()
    reset_tracking_registry()
    client = TestClient(create_app())

    sample_path = tmp_path / "sample.mp4"
    _write_sample_video(sample_path)

    with sample_path.open("rb") as file_handle:
        upload_response = client.post(
            "/api/v1/videos",
            files={"file": ("sample.mp4", file_handle, "video/mp4")},
        )

    assert upload_response.status_code == 201
    video = upload_response.json()
    assert video["media_url"].endswith("-playback.mp4")
    assert Path(video["source_path"]).exists()
    assert (upload_root / Path(video["media_url"]).name).exists()

    selection_response = client.post(
        "/api/v1/jobs/selection",
        json={
            "video_id": video["id"],
            "player_name": "QB 12",
            "time_seconds": 1.2,
            "box": {"x": 12, "y": 10, "width": 22, "height": 18},
        },
    )
    assert selection_response.status_code == 202
    queued_job = selection_response.json()
    assert queued_job["status"] == "running"
    completed_job = _wait_for_completion(client, queued_job["id"])
    assert completed_job["video_id"] == video["id"]
    assert completed_job["video_filename"] == "sample.mp4"
    assert completed_job["player_name"] == "QB 12"
    assert completed_job["player_visible"] is True
    assert completed_job["latest_box"]["x"] == 12
    assert completed_job["processed_media_url"].startswith("/media/jobs/")
    assert completed_job["progress_percent"] == 100
    assert len(completed_job["actions"]) == 1
    assert completed_job["actions"][0]["kind"] == "selection"

    offscreen_response = client.post(
        "/api/v1/jobs/offscreen",
        json={
            "video_id": video["id"],
            "time_seconds": 2.4,
        },
    )
    assert offscreen_response.status_code == 202
    running_offscreen_job = offscreen_response.json()
    assert running_offscreen_job["status"] == "running"
    updated_job = _wait_for_completion(client, running_offscreen_job["id"])
    assert updated_job["player_visible"] is False
    assert updated_job["latest_box"] is None
    assert updated_job["processed_media_url"].startswith("/media/jobs/")
    assert updated_job["progress_percent"] == 100
    assert len(updated_job["actions"]) == 2
    assert updated_job["actions"][-1]["kind"] == "offscreen"

    list_response = client.get("/api/v1/jobs")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    reset_tracking_registry()
    get_settings.cache_clear()


def test_delete_video_cleans_upload_and_related_job(monkeypatch, tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    media_root = tmp_path / "media"
    job_root = tmp_path / "jobs"
    monkeypatch.setenv("SAM2_UPLOAD_ROOT", str(upload_root))
    monkeypatch.setenv("SAM2_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("SAM2_JOB_ROOT", str(job_root))
    monkeypatch.setenv("SAM2_TRACKER_BACKEND", "mock")
    get_settings.cache_clear()
    reset_tracking_registry()
    client = TestClient(create_app())

    sample_path = tmp_path / "sample.mp4"
    _write_sample_video(sample_path)

    with sample_path.open("rb") as file_handle:
        upload_response = client.post(
            "/api/v1/videos",
            files={"file": ("sample.mp4", file_handle, "video/mp4")},
        )
    upload_response.raise_for_status()
    video = upload_response.json()
    playback_path = upload_root / Path(video["media_url"]).name
    assert playback_path.exists()

    selection_response = client.post(
        "/api/v1/jobs/selection",
        json={
            "video_id": video["id"],
            "player_name": "QB 12",
            "time_seconds": 0.0,
            "box": {"x": 12, "y": 10, "width": 22, "height": 18},
        },
    )
    selection_response.raise_for_status()
    completed_job = _wait_for_completion(client, selection_response.json()["id"])
    assert (job_root / completed_job["id"] / "processed.mp4").exists()
    assert (job_root / completed_job["id"] / "job.json").exists()

    # Simulate an API restart so cleanup cannot rely on in-memory job state.
    reset_tracking_registry()

    delete_response = client.delete(f"/api/v1/videos/{video['id']}")
    assert delete_response.status_code == 204

    list_videos_response = client.get("/api/v1/videos")
    list_videos_response.raise_for_status()
    assert list_videos_response.json() == []

    list_jobs_response = client.get("/api/v1/jobs")
    list_jobs_response.raise_for_status()
    assert list_jobs_response.json() == []

    assert not Path(video["source_path"]).exists()
    assert not playback_path.exists()
    assert not (upload_root / f"{video['id']}.json").exists()
    assert not (job_root / completed_job["id"]).exists()

    missing_response = client.delete(f"/api/v1/videos/{video['id']}")
    assert missing_response.status_code == 404

    reset_tracking_registry()
    get_settings.cache_clear()


def test_job_events_stream_progress(monkeypatch, tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    media_root = tmp_path / "media"
    job_root = tmp_path / "jobs"
    monkeypatch.setenv("SAM2_UPLOAD_ROOT", str(upload_root))
    monkeypatch.setenv("SAM2_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("SAM2_JOB_ROOT", str(job_root))
    monkeypatch.setenv("SAM2_TRACKER_BACKEND", "mock")
    get_settings.cache_clear()
    reset_tracking_registry()
    client = TestClient(create_app())

    sample_path = tmp_path / "sample.mp4"
    _write_sample_video(sample_path)

    with sample_path.open("rb") as file_handle:
        upload_response = client.post(
            "/api/v1/videos",
            files={"file": ("sample.mp4", file_handle, "video/mp4")},
        )
    upload_response.raise_for_status()
    video = upload_response.json()

    selection_response = client.post(
        "/api/v1/jobs/selection",
        json={
            "video_id": video["id"],
            "player_name": "QB 12",
            "time_seconds": 0.4,
            "box": {"x": 12, "y": 10, "width": 22, "height": 18},
        },
    )
    selection_response.raise_for_status()
    job_id = selection_response.json()["id"]

    with client.stream("GET", f"/api/v1/jobs/{job_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        payload = ""
        for chunk in response.iter_text():
            payload += chunk
            if '"status":"completed"' in payload:
                break

    assert "event: job" in payload
    assert '"progress_percent":100' in payload
    assert '"processing_detail":"Ready"' in payload

    reset_tracking_registry()
    get_settings.cache_clear()
