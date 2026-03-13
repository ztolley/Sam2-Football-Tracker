"""FastAPI entrypoint for the Football Tracker service."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from football_tracker import __version__
from football_tracker.api.routes import router as api_router
from football_tracker.core.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.include_router(api_router, prefix=settings.api_prefix)
    # The first iteration serves uploaded videos and rendered outputs directly
    # from local disk so the UI can preview them without another media service.
    app.mount("/media/uploads", StaticFiles(directory=settings.upload_root), name="uploaded-media")
    app.mount("/media/jobs", StaticFiles(directory=settings.job_root), name="job-media")
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    # Development keeps reload enabled; production callers can still import the
    # app object directly under another ASGI server if needed.
    uvicorn.run(
        "football_tracker.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.environment == "development",
        app_dir="src",
    )
