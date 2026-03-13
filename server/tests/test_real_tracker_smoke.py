from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from time import sleep
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from football_tracker.core.settings import get_settings
from football_tracker.core.tracking_service import reset_tracking_registry
from football_tracker.main import create_app


def _wait_for_completion(client: TestClient, job_id: str) -> dict:
    for _ in range(240):
        response = client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] != "running":
            return payload
        sleep(0.25)
    raise AssertionError("Job did not complete in time")


@pytest.mark.skipif(
    os.environ.get("SAM2_RUN_REAL_TRACKER_SMOKE") != "1",
    reason="Set SAM2_RUN_REAL_TRACKER_SMOKE=1 to run the real SAM2 smoke test.",
)
def test_real_tracker_can_process_and_render_moving_dot_video(monkeypatch, tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    media_root = tmp_path / "media"
    job_root = tmp_path / "jobs"
    monkeypatch.setenv("SAM2_UPLOAD_ROOT", str(upload_root))
    monkeypatch.setenv("SAM2_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("SAM2_JOB_ROOT", str(job_root))
    monkeypatch.setenv("SAM2_TRACKER_BACKEND", "real")
    get_settings.cache_clear()
    reset_tracking_registry()
    client = TestClient(create_app())

    fixture_dir = tmp_path / "fixture"
    video_path = fixture_dir / "moving-dot.mp4"
    manifest_path = fixture_dir / "moving-dot.json"
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "generate_moving_dot_video.py"
    subprocess.run(
        [
            sys.executable,
            str(script_path),
            str(video_path),
            "--manifest",
            str(manifest_path),
        ],
        check=True,
    )
    with manifest_path.open("r", encoding="utf-8") as file_handle:
        manifest = json.load(file_handle)
    first_box = manifest["boxes"][0]

    with video_path.open("rb") as file_handle:
        upload_response = client.post(
            "/api/v1/videos",
            files={"file": ("moving-dot.mp4", file_handle, "video/mp4")},
        )
    upload_response.raise_for_status()
    video = upload_response.json()

    selection_response = client.post(
        "/api/v1/jobs/selection",
        json={
            "video_id": video["id"],
            "player_name": "Dot runner",
            "time_seconds": 0.0,
            "box": first_box,
        },
    )
    assert selection_response.status_code == 202
    tracked_job = _wait_for_completion(client, selection_response.json()["id"])
    assert tracked_job["status"] == "completed"
    assert tracked_job["processed_media_url"]

    render_response = client.post(f"/api/v1/jobs/{tracked_job['id']}/render")
    assert render_response.status_code == 202
    rendered_job = _wait_for_completion(client, tracked_job["id"])
    assert rendered_job["status"] == "completed"
    assert rendered_job["rendered_media_url"]

    rendered_filename = Path(urlparse(rendered_job["rendered_media_url"]).path).name
    rendered_path = job_root / tracked_job["id"] / rendered_filename
    assert rendered_path.exists()

    reset_tracking_registry()
    get_settings.cache_clear()
