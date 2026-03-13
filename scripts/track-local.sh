#!/bin/bash

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")" && pwd)
SERVER_DIR="$ROOT_DIR/../server"
PYTHON_BIN="$SERVER_DIR/.venv/bin/python"

show_wrapper_help() {
  cat <<'EOF'
Wrapper around the Football Tracker local review flow.

Examples:
  ./run_sam2_track.sh source=~/Movies/football/bm4.mp4 frame-idx=47
  ./run_sam2_track.sh source=~/Movies/football/bm4.mp4 frame-idx=47 player-name="QB 12"
  ./run_sam2_track.sh source=~/Movies/football/bm4.mp4 frame-idx=47 box=321,339,365,417
  ./run_sam2_track.sh source=~/Movies/football/bm4.mp4 frame-idx=47 select-frame=840

Notes:
  - Arguments can be passed as `key=value` or standard `--key value`.
  - Run with `--help` to see the Python CLI help text as well.
EOF
}

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Virtual environment not found at $PYTHON_BIN" >&2
  exit 1
fi

cd "$SERVER_DIR"

if [[ ${1:-} == "--wrapper-help" ]]; then
  show_wrapper_help
  exit 0
fi

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then
  show_wrapper_help
  echo
fi

exec "$PYTHON_BIN" -m football_tracker.tracking.interactive "$@"
