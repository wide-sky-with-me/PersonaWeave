#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/use_wsl_uv_env.sh"

export PERSONAWEAVE_COMFY_BACKEND_URL="${PERSONAWEAVE_COMFY_BACKEND_URL:-http://127.0.0.1:8188}"
export PERSONAWEAVE_COMFY_BACKEND_API_PREFIX="${PERSONAWEAVE_COMFY_BACKEND_API_PREFIX:-}"

uv run python scripts/probe_comfy_mask_guard.py
