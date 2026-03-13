"""API route registration."""

from fastapi import APIRouter

from football_tracker.api.routes.health import router as health_router
from football_tracker.api.routes.jobs import router as jobs_router
from football_tracker.api.routes.videos import router as videos_router

router = APIRouter()
router.include_router(health_router, tags=["health"])
router.include_router(videos_router, prefix="/v1/videos", tags=["videos"])
router.include_router(jobs_router, prefix="/v1/jobs", tags=["jobs"])
