#!/bin/bash

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
SERVER_DIR="$ROOT_DIR/server"
VENV_PYTHON="$SERVER_DIR/.venv/bin/python"
E2E_ROOT="$ROOT_DIR/output/playwright/backend"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Server virtual environment not found. Run scripts/bootstrap-server.sh first." >&2
  exit 1
fi

rm -rf "$E2E_ROOT"
mkdir -p "$E2E_ROOT/uploads" "$E2E_ROOT/media" "$E2E_ROOT/jobs"

export SAM2_UPLOAD_ROOT="$E2E_ROOT/uploads"
export SAM2_MEDIA_ROOT="$E2E_ROOT/media"
export SAM2_JOB_ROOT="$E2E_ROOT/jobs"
export SAM2_TRACKER_BACKEND="${SAM2_TRACKER_BACKEND:-mock}"

cd "$SERVER_DIR"
exec "$VENV_PYTHON" -m uvicorn football_tracker.main:app --app-dir src --host 127.0.0.1 --port 8010
