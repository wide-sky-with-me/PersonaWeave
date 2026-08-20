from pathlib import Path


ROOT = Path(__file__).parents[1]


def pinned_versions() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / "infra/comfyui/versions.env").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def test_comfy_container_uses_repository_pins_and_verified_model() -> None:
    versions = pinned_versions()
    dockerfile = (ROOT / "infra/comfyui/Dockerfile").read_text(encoding="utf-8")

    assert f"ARG COMFYUI_REF={versions['COMFYUI_REF']}" in dockerfile
    assert f"ARG PYTORCH_INDEX_URL={versions['PYTORCH_INDEX_URL']}" in dockerfile
    assert f"ARG MEDIAPIPE_FACE_MODEL_URL={versions['MEDIAPIPE_FACE_MODEL_URL']}" in dockerfile
    assert (
        f"ARG MEDIAPIPE_FACE_MODEL_SHA256={versions['MEDIAPIPE_FACE_MODEL_SHA256']}"
        in dockerfile
    )
    assert "sha256sum --check --strict" in dockerfile
    assert "UV_HTTP_TIMEOUT=600" in dockerfile
    assert "UV_HTTP_RETRIES=5" in dockerfile
    assert "UV_CONCURRENT_DOWNLOADS=2" in dockerfile
    assert "--mount=type=cache,target=/root/.cache/uv" in dockerfile
    assert 'COPY comfy-nodes/PersonaWeave "${COMFYUI_ROOT}/custom_nodes/PersonaWeave"' in dockerfile


def test_compose_supports_bounded_local_gpu_and_external_backend_modes() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert 'profiles: ["local-gpu"]' in compose
    assert "dockerfile: infra/comfyui/Dockerfile" in compose
    assert "gpus: all" in compose
    assert "mem_limit: ${PERSONAWEAVE_COMFY_MEMORY_LIMIT:-12g}" in compose
    assert "shm_size: ${PERSONAWEAVE_COMFY_SHM_SIZE:-1gb}" in compose
    assert "required: false" in compose
    assert (
        "PERSONAWEAVE_COMFY_BACKEND_URL: "
        "${PERSONAWEAVE_COMFY_BACKEND_URL:-http://comfy-backend:8188}"
        in compose
    )
    assert "comfy-models:/opt/ComfyUI/models" in compose
    assert "comfy-input:/opt/ComfyUI/input" in compose
    assert "comfy-output:/opt/ComfyUI/output" in compose


def test_container_entrypoint_does_not_start_or_depend_on_wsl() -> None:
    entrypoint = (ROOT / "infra/comfyui/docker-entrypoint.sh").read_text(encoding="utf-8")

    assert "wsl" not in entrypoint.lower()
    assert 'exec "$@"' in entrypoint
    assert "--listen 0.0.0.0" in entrypoint
