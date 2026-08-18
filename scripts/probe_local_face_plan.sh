#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_image="${1:?usage: probe_local_face_plan.sh SOURCE_IMAGE [OUTPUT_PNG]}"
output_image="${2:-${project_root}/.runtime/preview-results/face-plan.png}"
app_url="${PERSONAWEAVE_APP_URL:-http://127.0.0.1:8100}"

mkdir -p "$(dirname "$output_image")"
curl -fsS --max-time 180 \
  -o "$output_image" \
  -F "source=@${source_image}" \
  "${app_url%/}/api/v1/previews/face-plan"
printf 'Saved local face-plan preview to %s\n' "$output_image"
