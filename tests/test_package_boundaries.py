"""Regression tests for lightweight, side-effect-free package boundaries."""

import subprocess
import sys


def _loaded_modules_after(package: str) -> set[str]:
    script = (
        f"import {package}, sys; "
        "print('\\n'.join(sorted(name for name in sys.modules if "
        f"name.startswith('{package}.'))))"
    )
    result = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)
    return set(result.stdout.splitlines())


def test_core_package_does_not_eagerly_load_inference_modules():
    loaded = _loaded_modules_after("core")

    assert "core.pipeline" not in loaded
    assert "core.detector" not in loaded


def test_api_package_does_not_eagerly_create_application():
    loaded = _loaded_modules_after("api")

    assert "api.app" not in loaded
