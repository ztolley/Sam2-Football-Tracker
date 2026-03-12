#!/bin/bash

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")" && pwd)
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Virtual environment not found at $PYTHON_BIN" >&2
  exit 1
fi

cd "$ROOT_DIR"
exec "$PYTHON_BIN" track_player_sam2.py "$@"
