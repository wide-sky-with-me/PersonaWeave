#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PERSONAWEAVE_COMFY_BACKEND_URL="${PERSONAWEAVE_COMFY_BACKEND_URL:-http://127.0.0.1:8188}"
export PERSONAWEAVE_COMFY_BACKEND_API_PREFIX="${PERSONAWEAVE_COMFY_BACKEND_API_PREFIX:-}"
export PERSONAWEAVE_COMFY_WORKFLOW_DIR="${PERSONAWEAVE_COMFY_WORKFLOW_DIR:-${project_root}/comfy-workflows}"
export PERSONAWEAVE_COMFY_DATABASE_PATH="${PERSONAWEAVE_COMFY_DATABASE_PATH:-${project_root}/data/comfy-gateway.sqlite3}"
listen_host="${PERSONAWEAVE_COMFY_GATEWAY_HOST:-127.0.0.1}"
listen_port="${PERSONAWEAVE_COMFY_GATEWAY_PORT:-8010}"

cd "$project_root"
exec uv run uvicorn personaweave.comfy_gateway:app --host "$listen_host" --port "$listen_port"
