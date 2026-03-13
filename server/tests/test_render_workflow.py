from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from time import sleep
from urllib.parse import urlparse

import cv2
from fastapi.testclient import TestClient

from football_tracker.core.settings import get_settings
from football_tracker.core.tracking_service import reset_tracking_registry
from football_tracker.main import create_app


def _wait_for_completion(client: TestClient, job_id: str) -> dict:
    for _ in range(80):
        response = client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] != "running":
            return payload
        sleep(0.1)
    raise AssertionError("Job did not complete in time")


def _generate_fixture(output_dir: Path) -> tuple[Path, dict]:
    video_path = output_dir / "moving-dot.mp4"
    manifest_path = output_dir / "moving-dot.json"
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "generate_moving_dot_video.py"
    subprocess.run(
        [
            sys.executable,
            str(script_path),
            str(video_path),
            "--manifest",
            str(manifest_path),
            "--width",
            "854",
            "--height",
            "480",
            "--frames",
            "24",
        ],
        check=True,
    )
    with manifest_path.open("r", encoding="utf-8") as file_handle:
        return video_path, json.load(file_handle)


def _cyan_pixel_near(frame, x: int, y: int, radius: int = 6) -> bool:
    height, width = frame.shape[:2]
    x1 = max(0, x - radius)
    y1 = max(0, y - radius)
    x2 = min(width, x + radius + 1)
    y2 = min(height, y + radius + 1)
    region = frame[y1:y2, x1:x2]
    if region.size == 0:
        return False
    blue = region[:, :, 0]
    green = region[:, :, 1]
    red = region[:, :, 2]
    return bool(((blue < 100) & (green > 120) & (red > 170)).any())


def _assert_box_drawn(video_path: Path, expected_box: dict[str, int], frame_index: int) -> None:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise AssertionError(f"Unable to open rendered video {video_path}")
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = capture.read()
    finally:
        capture.release()

    if not ok:
        raise AssertionError(f"Unable to read frame {frame_index} from {video_path}")

    center_x = expected_box["x"] + (expected_box["width"] // 2)
    center_y = expected_box["y"] + (expected_box["height"] // 2)
    checks = [
        (center_x, expected_box["y"]),
        (center_x, expected_box["y"] + expected_box["height"]),
        (expected_box["x"], center_y),
        (expected_box["x"] + expected_box["width"], center_y),
    ]
    assert any(_cyan_pixel_near(frame, x, y) for x, y in checks)


def test_rendered_movie_follows_moving_dot(monkeypatch, tmp_path: Path) -> None:
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

    fixture_dir = tmp_path / "fixture"
    video_path, manifest = _generate_fixture(fixture_dir)
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
            "box": {
                "x": first_box["x"] - 6,
                "y": first_box["y"] - 6,
                "width": first_box["width"] + 12,
                "height": first_box["height"] + 12,
            },
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

    rendered_url = rendered_job["rendered_media_url"]
    rendered_path = job_root / tracked_job["id"] / Path(urlparse(rendered_url).path).name
    assert rendered_path.exists()

    frame_indexes = [0, manifest["frames"] // 2, manifest["frames"] - 1]
    for frame_index in frame_indexes:
        _assert_box_drawn(rendered_path, manifest["boxes"][frame_index], frame_index)

    reset_tracking_registry()
    get_settings.cache_clear()
