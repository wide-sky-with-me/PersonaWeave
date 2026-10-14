"""CPU container requirements must preserve the development lock's non-CUDA pins."""

from pathlib import Path

import pytest
import yaml

from scripts.container_requirements import cpu_requirements

LOCK = """# uv export
onnxruntime-gpu==1.20.2
onnxruntime==1.29.0 ; sys_platform == 'never'
nvidia-cublas-cu12==12.9.2.10
nvidia-cuda-nvrtc-cu12==12.9.86
opencv-python==5.0.0.93
opencv-python-headless==5.0.0.93
numpy==2.4.6 ; python_full_version < '3.12'
numpy==2.5.2 ; python_full_version >= '3.12'
insightface==1.0.1
"""
ROOT = Path(__file__).resolve().parents[1]


def test_cpu_export_keeps_pins_and_markers_without_cuda_or_duplicate_cv2():
    exported = cpu_requirements(LOCK)
    assert "onnxruntime==1.23.2\n" in exported
    assert "onnxruntime-gpu" not in exported
    assert "nvidia-" not in exported
    assert "sys_platform == 'never'" not in exported
    assert "opencv-python==" not in exported
    assert "opencv-python-headless==5.0.0.93\n" in exported
    assert "numpy==2.4.6 ; python_full_version < '3.12'\n" in exported
    assert "numpy==2.5.2 ; python_full_version >= '3.12'\n" in exported
    assert "insightface==1.0.1\n" in exported


def test_cpu_runtime_pin_is_independent_of_gpu_only_releases():
    assert "onnxruntime==1.23.2\n" in cpu_requirements(LOCK.replace("gpu==1.20.2", "gpu==1.24.0"))


@pytest.mark.parametrize(
    "exported",
    [
        LOCK.replace("onnxruntime-gpu==1.20.2\n", ""),
        LOCK + "onnxruntime-gpu==1.23.2\n",
        LOCK + "requests>=2.0\n",
        LOCK.replace("opencv-python-headless==5.0.0.93\n", ""),
    ],
)
def test_invalid_exports_fail_before_installation(exported):
    with pytest.raises(ValueError):
        cpu_requirements(exported)


def test_cpu_compose_selects_cpu_and_preserves_default_cuda_target():
    override = yaml.safe_load((ROOT / "docker-compose.cpu.yml").read_text())
    assert override["services"]["personaweave"]["build"]["target"] == "cpu"
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "FROM runtime AS cpu" in dockerfile
    assert dockerfile.rfind("FROM runtime AS backend") > dockerfile.index("FROM runtime AS cpu")
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "container_requirements.py" in dockerfile
    assert dockerfile.count("uv --no-config pip install") == 2
    assert dockerfile.index("ENV NODE_USE_ENV_PROXY=1") < dockerfile.index("FROM python:3.11-slim AS runtime")


def test_lan_compose_only_appends_an_explicit_required_address():
    overlay = yaml.safe_load((ROOT / "docker-compose.lan.yml").read_text())
    assert set(overlay) == {"services"}
    service = overlay["services"]["personaweave"]
    assert set(service) == {"ports"}
    assert len(service["ports"]) == 1
    assert service["ports"][0].startswith("${PERSONAWEAVE_LAN_ADDRESS:?")
    assert service["ports"][0].endswith(":${PERSONAWEAVE_PORT:-8000}:8000")
    assert "0.0.0.0" not in service["ports"][0]
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert "PERSONAWEAVE_BIND_ADDRESS" in base["services"]["personaweave"]["ports"][0]
