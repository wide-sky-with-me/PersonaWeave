#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/use_wsl_uv_env.sh"

smoke_port="${PERSONAWEAVE_COMFY_SMOKE_PORT:-8766}"
smoke_db="/tmp/personaweave-comfy-smoke-${smoke_port}.sqlite3"
smoke_log="/tmp/personaweave-comfy-smoke-${smoke_port}.log"
smoke_workflows="/tmp/personaweave-comfy-workflows-${smoke_port}"

mkdir -p "$smoke_workflows"
PERSONAWEAVE_COMFY_DATABASE_PATH="$smoke_db" \
PERSONAWEAVE_COMFY_WORKFLOW_DIR="$smoke_workflows" \
uv run uvicorn personaweave.comfy_gateway:app \
  --host 127.0.0.1 \
  --port "$smoke_port" >"$smoke_log" 2>&1 &
smoke_server_pid=$!
trap 'kill "$smoke_server_pid" 2>/dev/null || true' EXIT

for _ in {1..20}; do
  if curl -fsS "http://127.0.0.1:${smoke_port}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

curl -fsS "http://127.0.0.1:${smoke_port}/health"
echo
ready_status="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${smoke_port}/ready")"
test "$ready_status" = "503"
echo "ready=${ready_status} (expected until backend and workflows are configured)"
