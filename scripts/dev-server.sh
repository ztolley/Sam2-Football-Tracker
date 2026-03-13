#!/bin/bash

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
SERVER_DIR="$ROOT_DIR/server"
VENV_PYTHON="$SERVER_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Server virtual environment not found. Run scripts/bootstrap-server.sh first." >&2
  exit 1
fi

cd "$SERVER_DIR"
exec "$VENV_PYTHON" -m uvicorn football_tracker.main:app --app-dir src --host 127.0.0.1 --port 8000 --reload
