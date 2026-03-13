#!/bin/bash

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
SERVER_DIR="$ROOT_DIR/server"
PYTHON_BIN="${PYTHON_BIN:-python3.13}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python interpreter '$PYTHON_BIN' was not found." >&2
  echo "Install Python 3.13 or set PYTHON_BIN to a compatible interpreter." >&2
  exit 1
fi

"$PYTHON_BIN" -m venv "$SERVER_DIR/.venv"
"$SERVER_DIR/.venv/bin/pip" install --upgrade pip
"$SERVER_DIR/.venv/bin/pip" install -e "$SERVER_DIR[dev]"
