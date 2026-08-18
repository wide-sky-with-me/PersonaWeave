#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_root="${PERSONAWEAVE_COMFY_RUNTIME_DIR:-${project_root}/.runtime/ComfyUI}"
listen_host="${PERSONAWEAVE_COMFY_LISTEN_HOST:-127.0.0.1}"
listen_port="${PERSONAWEAVE_COMFY_LISTEN_PORT:-8188}"
reserve_vram="${PERSONAWEAVE_COMFY_RESERVE_VRAM_GB:-0.8}"

if [[ -f "${project_root}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${project_root}/.env"
  set +a
fi

if [[ ! -x "$runtime_root/.venv/bin/python" ]]; then
  printf 'ComfyUI is not installed. Run scripts/setup_local_comfy.sh first.\n' >&2
  exit 1
fi

cd "$runtime_root"
exec .venv/bin/python main.py \
  --listen "$listen_host" \
  --port "$listen_port" \
  --reserve-vram "$reserve_vram" \
  --disable-cuda-malloc
