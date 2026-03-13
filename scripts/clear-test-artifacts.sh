#!/bin/bash

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)

rm -rf "$ROOT_DIR/output/playwright/backend" "$ROOT_DIR/output/playwright/fixtures"

UPLOAD_DIR="$ROOT_DIR/server/data/uploads"
if [[ -d "$UPLOAD_DIR" ]]; then
  while IFS= read -r metadata_path; do
    video_id=$(basename "$metadata_path" .json)
    rm -f "$metadata_path"
    rm -f "$UPLOAD_DIR/$video_id-moving-dot.mp4"
  done < <(grep -l '"filename": "moving-dot.mp4"' "$UPLOAD_DIR"/*.json 2>/dev/null || true)
fi
