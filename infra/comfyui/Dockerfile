FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG COMFYUI_REF=b323a345bbbfb2f3a95b5b73b68eb7919a26515e
ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu130
ARG MEDIAPIPE_FACE_MODEL_URL=https://huggingface.co/Comfy-Org/mediapipe/resolve/main/detection/mediapipe_face_fp32.safetensors
ARG MEDIAPIPE_FACE_MODEL_SHA256=a98c4806081d40eba35102a0f6dc0000c2e1388b72cf24e691703d0605bd888a

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=600 \
    UV_HTTP_RETRIES=5 \
    UV_CONCURRENT_DOWNLOADS=2 \
    COMFYUI_ROOT=/opt/ComfyUI

# Use the HTTPS Debian mirror so Docker builds do not depend on the HTTP
# proxy path, which is frequently unavailable in restricted networks.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --filter=blob:none https://github.com/Comfy-Org/ComfyUI.git "${COMFYUI_ROOT}" \
    && git -C "${COMFYUI_ROOT}" fetch --depth 1 origin "${COMFYUI_REF}" \
    && git -C "${COMFYUI_ROOT}" checkout --detach "${COMFYUI_REF}" \
    && rm -rf "${COMFYUI_ROOT}/.git"

RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv "${COMFYUI_ROOT}/.venv" --python 3.12 \
    && uv pip install --python "${COMFYUI_ROOT}/.venv/bin/python" \
        torch torchvision torchaudio --index-url "${PYTORCH_INDEX_URL}" \
    && uv pip install --python "${COMFYUI_ROOT}/.venv/bin/python" \
        -r "${COMFYUI_ROOT}/requirements.txt"

RUN mkdir -p "${COMFYUI_ROOT}/models/detection" \
    && curl -fL --retry 3 \
        -o "${COMFYUI_ROOT}/models/detection/mediapipe_face_fp32.safetensors" \
        "${MEDIAPIPE_FACE_MODEL_URL}" \
    && echo "${MEDIAPIPE_FACE_MODEL_SHA256}  ${COMFYUI_ROOT}/models/detection/mediapipe_face_fp32.safetensors" \
        | sha256sum --check --strict

COPY comfy-nodes/PersonaWeave "${COMFYUI_ROOT}/custom_nodes/PersonaWeave"
COPY infra/comfyui/docker-entrypoint.sh /usr/local/bin/personaweave-comfy
RUN chmod 0755 /usr/local/bin/personaweave-comfy \
    && mkdir -p "${COMFYUI_ROOT}/input" "${COMFYUI_ROOT}/output" "${COMFYUI_ROOT}/temp"

WORKDIR ${COMFYUI_ROOT}
EXPOSE 8188

HEALTHCHECK --interval=10s --timeout=5s --start-period=90s --retries=12 \
    CMD curl --fail --silent http://127.0.0.1:8188/system_stats > /dev/null || exit 1

ENTRYPOINT ["personaweave-comfy"]
