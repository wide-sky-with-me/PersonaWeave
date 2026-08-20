#!/bin/sh
set -eu

reserve_vram="${PERSONAWEAVE_COMFY_RESERVE_VRAM_GB:-0.8}"
listen_port="${PERSONAWEAVE_COMFY_LISTEN_PORT:-8188}"

set -- \
    "${COMFYUI_ROOT}/.venv/bin/python" \
    "${COMFYUI_ROOT}/main.py" \
    --listen 0.0.0.0 \
    --port "${listen_port}" \
    --reserve-vram "${reserve_vram}"

if [ "${PERSONAWEAVE_COMFY_DISABLE_CUDA_MALLOC:-1}" = "1" ]; then
    set -- "$@" --disable-cuda-malloc
fi

exec "$@"
