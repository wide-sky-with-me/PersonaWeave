#!/usr/bin/env bash
set -euo pipefail

smoke_port="${PERSONAWEAVE_SMOKE_PORT:-8765}"
smoke_db="/tmp/personaweave-smoke-${smoke_port}.sqlite3"
smoke_assets="/tmp/personaweave-smoke-assets-${smoke_port}"
smoke_log="/tmp/personaweave-smoke-${smoke_port}.log"

PERSONAWEAVE_DATABASE_PATH="$smoke_db" \
PERSONAWEAVE_ASSET_ROOT="$smoke_assets" \
uv run uvicorn personaweave.main:app \
  --host 127.0.0.1 \
  --port "$smoke_port" >"$smoke_log" 2>&1 &
smoke_server_pid=$!
trap 'kill "$smoke_server_pid" 2>/dev/null || true' EXIT

for _ in {1..20}; do
  if curl -fsS "http://127.0.0.1:${smoke_port}/api/v1/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

curl -fsS "http://127.0.0.1:${smoke_port}/api/v1/health"
echo
curl -fsS -o /dev/null -w 'app=%{http_code} %{content_type}\n' \
  "http://127.0.0.1:${smoke_port}/app/"
curl -fsS -o /dev/null -w 'docs=%{http_code} %{content_type}\n' \
  "http://127.0.0.1:${smoke_port}/docs"
