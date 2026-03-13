# Architecture

This repository is split into:

- `ui/`: Lit + TypeScript + Vite frontend
- `server/`: FastAPI application, Python CLI, and tracking code
- `scripts/`: shared bootstrap and dev helpers
- `infra/`: local infrastructure configuration

## Local development

For local-first development on macOS Apple Silicon:

- run the frontend directly with Vite
- run the API directly from `server/.venv`
- optionally use `docker compose up -d mediamtx` for local media streaming experiments

This keeps the edit loop fast and avoids introducing infrastructure that the app does not yet use.

## Deployment target

The server package is structured so it can later run:

- as a local FastAPI app on macOS using MPS where supported
- in Linux AMD64 containers with NVIDIA GPUs under RKE2

The tracking code remains in Python because the SAM2 and PyTorch integration is the critical runtime boundary.

## Streaming approach

Use browser-native HTTP playback for the main UI:

- local review and export playback: MP4 or HLS
- low-latency live preview: WebRTC if needed later
- external integrations: optional RTSP from MediaMTX

The browser should not depend on RTSP directly.

## Next backend steps

- keep the current file-backed and in-memory approach until persistence or queueing is actually needed
- introduce Postgres later if persisted users, videos, jobs, or audit history become necessary
- introduce a queue later only when background processing requirements justify it
- move frame extraction and render artifacts into object storage
- add WebSocket progress streaming for long-running tracking jobs
