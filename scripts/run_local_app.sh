#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/use_wsl_uv_env.sh"

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PERSONAWEAVE_INFERENCE_BASE_URL="${PERSONAWEAVE_INFERENCE_BASE_URL:-http://127.0.0.1:8010}"
listen_host="${PERSONAWEAVE_APP_HOST:-127.0.0.1}"
listen_port="${PERSONAWEAVE_APP_PORT:-8100}"

cd "$project_root"
exec uv run uvicorn personaweave.main:app --host "$listen_host" --port "$listen_port"
