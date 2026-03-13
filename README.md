# Football Tracker

This repository now has a split frontend/backend layout for evolving the current local OpenCV workflow into a browser-driven application.

## Layout

- `ui/`: Lit + TypeScript + Vite frontend with Tailwind, ESLint, and Prettier
- `server/`: FastAPI scaffold plus the existing interactive tracking CLI
- `scripts/`: shared bootstrap and development scripts
- `infra/`: local MediaMTX configuration
- `docs/`: architecture notes
- `samples/`: optional local video inputs

## Local development

Start the optional media service:

```bash
docker compose up -d mediamtx
```

Bootstrap the projects:

```bash
./scripts/bootstrap-server.sh
./scripts/bootstrap-ui.sh
```

Run the dev servers:

```bash
./scripts/dev-server.sh
./scripts/dev-ui.sh
```

The Vite dev server proxies `/api` and `/media` to `http://127.0.0.1:8000`, so local UI development stays free of CORS work.

## First browser iteration

The current web flow supports:

- uploading a source video into the backend
- listing stored uploads
- previewing the selected video in the browser
- pausing the selected video and drawing a player box directly on it
- submitting later paused box draws as corrections
- marking the player off-screen from the video overlay
- rendering a final output movie from the current tracked state

The next UI iteration is replacing the native video controls with a custom scrubber and connecting these actions to real background tracking work.

## Automated tests

The repo now includes a deterministic synthetic-video workflow test:

- `server/tests/test_render_workflow.py` generates a 480p moving-dot video, runs the tracking workflow, renders a final MP4, and checks the rendered highlight is where it should be
- `ui/e2e/player-workflow.spec.ts` drives the browser flow end to end with Playwright against isolated local test servers

Run them with:

```bash
cd server && ./.venv/bin/pytest
cd ui && npm run test:e2e
```

To clear synthetic browser-test uploads and generated movies:

```bash
./scripts/clear-test-artifacts.sh
```

There is also an optional real-SAM2 smoke test that uses the same synthetic video but only checks that the real backend can complete processing and render an output:

```bash
cd server && SAM2_RUN_REAL_TRACKER_SMOKE=1 ./.venv/bin/pytest tests/test_real_tracker_smoke.py
```

## Current backend CLI

The existing local tracker is still available:

```bash
./scripts/track-local.sh source=~/Movies/football/bm4.mp4 frame-idx=47
```

Requirements:

- Python `3.13`
- Node.js `22+`
- `ffmpeg` on the system path
- Docker if you want the optional `mediamtx` service locally

## Deployment direction

The current layout is meant to support:

- local macOS development on Apple Silicon
- Linux AMD64 deployment with NVIDIA GPUs under RKE2

If the MPS/CUDA divergence becomes a productivity problem, the next logical step is Tilt with the server and worker running in the cluster while the UI still hot-reloads locally.
