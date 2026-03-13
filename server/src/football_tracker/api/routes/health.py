"""Health endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from football_tracker.core.settings import get_settings

router = APIRouter()


@router.get("/health")
def healthcheck() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.environment,
    }
