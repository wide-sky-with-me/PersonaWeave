"""Test-only environment defaults for configuration parsing; never real service endpoints."""

import os
from io import BytesIO

import pytest
from PIL import Image

for role, model in (("VISION", "openai/gpt-4o"), ("IMAGE", "openai/gpt-image-1")):
    os.environ.setdefault(f"AI_{role}_MODEL", model)
    os.environ.setdefault(f"AI_{role}_API_KEY", f"test-{role.lower()}-key")
    os.environ.setdefault(f"AI_{role}_API_BASE", f"https://{role.lower()}.test/v1")


@pytest.fixture
def camera_mpo_bytes():
    """A JPEG-compatible camera file whose secondary image differs from the primary."""
    stream = BytesIO()
    Image.new("RGB", (48, 32), "red").save(
        stream, "MPO", save_all=True, append_images=[Image.new("RGB", (48, 32), "blue")]
    )
    return stream.getvalue()
