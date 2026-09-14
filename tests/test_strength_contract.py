"""Keep API, direct pipeline, Studio and documented blend defaults aligned."""

import inspect
import re
from pathlib import Path

from api.schemas import SwapParameters
from core.pipeline import PersonaWeavePipeline

ROOT = Path(__file__).resolve().parents[1]


def test_strength_defaults_and_ui_upper_bound_match_api():
    """Fail when an entry point silently reverts to the weaker identity blend."""
    default = SwapParameters().identity_blend_strength
    assert default == 0.9
    for method in ("swap_single_face_facefusion", "process_image", "process_image_multi_mapping"):
        value = inspect.signature(getattr(PersonaWeavePipeline, method)).parameters["identity_blend_strength"].default
        assert value == default
    store = (ROOT / "web/src/stores/useStudioStore.ts").read_text()
    assert float(re.search(r"identity_blend_strength: ([0-9.]+)", store)[1]) == default
    panel = (ROOT / "web/src/components/studio/ParameterPanel.tsx").read_text()
    slider = panel.split('label="换入特征强度"', 1)[1].split("/>", 1)[0]
    maximum = SwapParameters.model_json_schema()["properties"]["identity_blend_strength"]["maximum"]
    assert "max={" + str(maximum) + "}" in slider
    documentation = (ROOT / "docs/facefusion-local-swap.md").read_text()
    assert "its default is " + chr(96) + str(default) + chr(96) in documentation
    assert SwapParameters(identity_blend_strength=0.72).identity_blend_strength == 0.72
