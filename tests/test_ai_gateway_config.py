"""Configuration contract tests for the shared AI gateway."""

import pytest
import yaml

from core.ai_gateway import AIGateway


def _write_config(tmp_path, **overrides):
    config = {
        "model_list": [{"model_name": "vision", "litellm_params": {"model": "openai/vision"}}],
        "vision_roles": {
            "default": "vision",
            "identity_arbitration": "vision",
            "refinement": "vision",
        },
        "image_models": [
            {
                "model_name": "refine",
                "provider": "litellm",
                "params": {"model": "openai/image"},
                "generation": {
                    "max_side": 2048,
                    "min_side": 512,
                    "grid": 8,
                    "delimiter": "*",
                },
            }
        ],
        "default_image_alias": "refine",
        **overrides,
    }
    path = tmp_path / "gateway.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_gateway_requires_current_image_models_catalog(tmp_path):
    path = _write_config(
        tmp_path,
        image_models=None,
        image_edit={"model_name": "legacy", "litellm_params": {"model": "openai/image"}},
    )

    with pytest.raises(ValueError, match="image_models"):
        AIGateway(path)


def test_gateway_rejects_duplicate_image_aliases(tmp_path):
    image_model = {
        "model_name": "refine",
        "provider": "litellm",
        "params": {"model": "openai/image"},
        "generation": {"max_side": 2048, "min_side": 512, "grid": 8, "delimiter": "*"},
    }
    path = _write_config(tmp_path, image_models=[image_model, image_model])

    with pytest.raises(ValueError, match="Duplicate image model alias"):
        AIGateway(path)


def test_gateway_rejects_unknown_vision_role_alias(tmp_path):
    path = _write_config(
        tmp_path,
        vision_roles={"default": "missing", "identity_arbitration": "vision", "refinement": "vision"},
    )

    with pytest.raises(ValueError, match="unknown model aliases"):
        AIGateway(path)


def test_gateway_rejects_missing_generation_policy(tmp_path):
    path = _write_config(
        tmp_path,
        image_models=[{"model_name": "refine", "provider": "litellm", "params": {"model": "openai/image"}}],
    )

    with pytest.raises(ValueError, match="generation"):
        AIGateway(path)
