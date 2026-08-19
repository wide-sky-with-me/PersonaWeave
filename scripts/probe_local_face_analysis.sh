#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/use_wsl_uv_env.sh"

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ne 1 ]]; then
  printf 'usage: %s IMAGE_PATH\n' "$0" >&2
  exit 2
fi

export PERSONAWEAVE_COMFY_BACKEND_URL="${PERSONAWEAVE_COMFY_BACKEND_URL:-http://127.0.0.1:8188}"
export PERSONAWEAVE_COMFY_BACKEND_API_PREFIX="${PERSONAWEAVE_COMFY_BACKEND_API_PREFIX:-}"
cd "$project_root"
uv run python scripts/probe_comfy_face_analysis.py "$1"
