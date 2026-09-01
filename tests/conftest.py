"""Test-only environment defaults for configuration parsing."""

import os

os.environ.setdefault("DMXAPI_API_KEY", "test-dmx-key")
os.environ.setdefault("DMXAPI_API_BASE", "https://dmx.test/v1")
