"""Application configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import torch
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def detect_default_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class Settings(BaseSettings):
    """Runtime configuration for the local-first Football Tracker service."""

    app_name: str = "Football Tracker API"
    environment: Literal["development", "staging", "production"] = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_prefix: str = "/api"
    media_root: Path = Field(default=Path("./data/media"))
    upload_root: Path = Field(default=Path("./data/uploads"))
    job_root: Path = Field(default=Path("./data/jobs"))
    mediamtx_rtsp_url: str = "rtsp://localhost:8554"
    mediamtx_hls_url: str = "http://localhost:8888"
    tracker_backend: Literal["real", "mock"] = "real"
    sam2_model_id: str = "facebook/sam2.1-hiera-base-plus"
    sam2_device: str = Field(default_factory=detect_default_device)
    tracker_line_width: int = 2

    model_config = SettingsConfigDict(
        env_prefix="SAM2_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    # The API is file-backed today, so startup is responsible for ensuring the
    # working directories exist before any request handlers touch them.
    settings.media_root.mkdir(parents=True, exist_ok=True)
    settings.upload_root.mkdir(parents=True, exist_ok=True)
    settings.job_root.mkdir(parents=True, exist_ok=True)
    return settings
