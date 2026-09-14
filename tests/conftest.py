"""Test-only environment defaults for configuration parsing; never real service endpoints."""

import os

for role, model in (("VISION", "openai/gpt-4o"), ("IMAGE", "openai/gpt-image-1")):
    os.environ.setdefault(f"AI_{role}_MODEL", model)
    os.environ.setdefault(f"AI_{role}_API_KEY", f"test-{role.lower()}-key")
    os.environ.setdefault(f"AI_{role}_API_BASE", f"https://{role.lower()}.test/v1")
