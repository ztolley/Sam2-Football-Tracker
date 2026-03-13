from fastapi.testclient import TestClient

from football_tracker.main import app


def test_healthcheck() -> None:
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
