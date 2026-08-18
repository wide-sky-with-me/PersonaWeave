#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_root="${PERSONAWEAVE_COMFY_RUNTIME_DIR:-${project_root}/.runtime/ComfyUI}"
versions_file="${project_root}/infra/comfyui/versions.env"

# shellcheck disable=SC1090
source "$versions_file"

if [[ ! -d "$runtime_root/.git" ]]; then
  mkdir -p "$(dirname "$runtime_root")"
  git clone https://github.com/Comfy-Org/ComfyUI.git "$runtime_root"
fi

git -C "$runtime_root" fetch --depth 1 origin "$COMFYUI_REF"
git -C "$runtime_root" checkout --detach "$COMFYUI_REF"

if [[ ! -x "$runtime_root/.venv/bin/python" ]]; then
  uv venv "$runtime_root/.venv" --python 3.12
fi

uv pip install \
  --python "$runtime_root/.venv/bin/python" \
  torch torchvision torchaudio \
  --index-url "$PYTORCH_INDEX_URL"
uv pip install \
  --python "$runtime_root/.venv/bin/python" \
  -r "$runtime_root/requirements.txt"

detection_dir="$runtime_root/models/detection"
detection_model="$detection_dir/mediapipe_face_fp32.safetensors"
mkdir -p "$detection_dir"
if [[ -f "$detection_model" ]]; then
  actual_sha256="$(sha256sum "$detection_model" | cut -d ' ' -f 1)"
  if [[ "$actual_sha256" != "$MEDIAPIPE_FACE_MODEL_SHA256" ]]; then
    printf 'Refusing to use MediaPipe model with unexpected SHA-256: %s\n' "$actual_sha256" >&2
    exit 1
  fi
else
  temporary_model="${detection_model}.download"
  curl -fL --retry 3 -o "$temporary_model" "$MEDIAPIPE_FACE_MODEL_URL"
  actual_sha256="$(sha256sum "$temporary_model" | cut -d ' ' -f 1)"
  if [[ "$actual_sha256" != "$MEDIAPIPE_FACE_MODEL_SHA256" ]]; then
    printf 'MediaPipe model SHA-256 mismatch: %s\n' "$actual_sha256" >&2
    exit 1
  fi
  mv "$temporary_model" "$detection_model"
fi

node_link="$runtime_root/custom_nodes/PersonaWeave"
if [[ -L "$node_link" ]]; then
  current_target="$(readlink -f "$node_link")"
  expected_target="$(readlink -f "${project_root}/comfy-nodes/PersonaWeave")"
  if [[ "$current_target" != "$expected_target" ]]; then
    printf 'Refusing to replace custom node link pointing to %s\n' "$current_target" >&2
    exit 1
  fi
elif [[ -e "$node_link" ]]; then
  printf 'Refusing to replace existing path: %s\n' "$node_link" >&2
  exit 1
else
  ln -s "${project_root}/comfy-nodes/PersonaWeave" "$node_link"
fi

printf 'ComfyUI ready at %s (ref %s)\n' "$runtime_root" "$COMFYUI_REF"
