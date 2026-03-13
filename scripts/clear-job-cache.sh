#!/bin/bash

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
JOB_DIR="$ROOT_DIR/server/data/jobs"

if [[ -d "$JOB_DIR" ]]; then
  find "$JOB_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
fi
