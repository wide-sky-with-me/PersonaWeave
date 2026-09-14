#!/usr/bin/env bash
# Ensures required model weights are present before the API starts serving inference
# requests, then hands off to the container's CMD (normally uvicorn).
set -euo pipefail

REQUIRED_MODELS=(
    "models/buffalo_l/det_10g.onnx"
    "models/buffalo_l/w600k_r50.onnx"
    "models/buffalo_l/2d106det.onnx"
    "models/inswapper_128.onnx"
)

missing=0
for f in "${REQUIRED_MODELS[@]}"; do
    if [ ! -s "$f" ]; then
        missing=1
        break
    fi
done

if [ "$missing" = "1" ] && [ "${PERSONAWEAVE_SKIP_MODEL_DOWNLOAD:-0}" != "1" ]; then
    echo "[entrypoint] Required model weights are missing under ./models; downloading (~2.5GB, first run only)..."
    python scripts/download_models.py
fi

mkdir -p data outputs

exec "$@"
