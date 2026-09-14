"""Offline contracts for native Router deployments and application-only metadata."""

from copy import deepcopy

import pytest
import yaml

from core.ai.configuration import load_gateway_config, resolve_env

DYNAMIC = {"max_side": 2048, "min_side": 512, "grid": 8, "max_pixels": 2048 * 2048, "delimiter": "x"}


def _config():
    return {
        "model_list": [
            {"model_name": "vision", "litellm_params": {"model": "openai/vision"}},
            {"model_name": "refine", "litellm_params": {"model": "openai/image"}},
        ],
        "vision_roles": {"default": "vision", "identity_arbitration": "vision", "refinement": "vision"},
        "image_models": [{"model_name": "refine", "generation": {"size": "auto"}}],
        "default_image_alias": "refine",
        "router_settings": {"num_retries": 0, "fallbacks": []},
    }


def _write_config(tmp_path, config=None, **overrides):
    config = deepcopy(config if config is not None else _config())
    config.update(overrides)
    path = tmp_path / "gateway.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_native_catalog_keeps_deployments_and_metadata_separate(tmp_path):
    source = _config()
    source["model_list"][1]["litellm_params"].update(
        api_base="os.environ/TEST_GATEWAY_BASE",
        api_key="os.environ/TEST_GATEWAY_KEY",
        extra_headers={"X-Tenant": "os.environ/TEST_TENANT"},
    )
    source["model_list"][1]["model_info"] = {"id": "image-deployment"}
    config = load_gateway_config(_write_config(tmp_path, source))
    for deployment in source["model_list"]:
        deployment["litellm_params"].setdefault("max_retries", 0)
    assert config.model_list == source["model_list"]
    assert config.default_image_alias == "refine"
    metadata = config.image_edits["refine"]
    assert metadata.model_name == "refine"
    assert metadata.display_name == "refine"
    assert metadata.generation == {"size": "auto"}
    assert not hasattr(metadata, "params")
    assert not hasattr(metadata, "provider")


def test_duplicate_router_deployments_are_legal_but_metadata_is_unique(tmp_path):
    source = _config()
    source["model_list"].append({"model_name": "refine", "litellm_params": {"model": "azure/image"}})
    config = load_gateway_config(_write_config(tmp_path, source))
    assert len(config.model_list) == 3
    assert list(config.image_edits) == ["refine"]
    source["image_models"].append(deepcopy(source["image_models"][0]))
    with pytest.raises(ValueError, match="Duplicate image"):
        load_gateway_config(_write_config(tmp_path, source))


@pytest.mark.parametrize("field", ["provider", "params", "litellm_params", "unknown"])
def test_image_metadata_rejects_legacy_or_unknown_fields(tmp_path, field):
    source = _config()
    source["image_models"][0][field] = "must-not-appear-in-errors"
    with pytest.raises(ValueError, match="Image metadata") as error:
        load_gateway_config(_write_config(tmp_path, source))
    assert "must-not-appear-in-errors" not in str(error.value)


@pytest.mark.parametrize("field", ["provider", "params", "generation"])
def test_deployments_reject_legacy_app_fields(tmp_path, field):
    source = _config()
    source["model_list"][1][field] = {}
    with pytest.raises(ValueError, match="native litellm_params"):
        load_gateway_config(_write_config(tmp_path, source))


def test_legacy_top_level_image_edit_is_not_silently_accepted(tmp_path):
    with pytest.raises(ValueError, match="unsupported or retired"):
        load_gateway_config(_write_config(tmp_path, image_edit={"model_name": "old"}))


@pytest.mark.parametrize("value", [None, [], {}, "invalid"])
def test_image_metadata_list_is_required(tmp_path, value):
    with pytest.raises(ValueError, match="image_models"):
        load_gateway_config(_write_config(tmp_path, image_models=value))


@pytest.mark.parametrize("alias", ["unknown", None, [], ""])
def test_image_metadata_requires_known_alias(tmp_path, alias):
    with pytest.raises(ValueError, match="unknown model alias"):
        load_gateway_config(
            _write_config(tmp_path, image_models=[{"model_name": alias, "generation": {"size": "auto"}}])
        )


@pytest.mark.parametrize("alias", ["unknown", "vision", [], None, ""])
def test_default_image_alias_must_be_explicit_and_image_capable(tmp_path, alias):
    with pytest.raises(ValueError, match="default_image_alias"):
        load_gateway_config(_write_config(tmp_path, default_image_alias=alias))


@pytest.mark.parametrize("alias", ["unknown", None, [], "refine"])
def test_vision_roles_require_known_non_image_aliases(tmp_path, alias):
    source = _config()
    source["vision_roles"]["refinement"] = alias
    with pytest.raises(ValueError, match="vision_roles"):
        load_gateway_config(_write_config(tmp_path, source))


@pytest.mark.parametrize("roles", [None, [], {}, {"default": "vision"}])
def test_vision_roles_require_all_business_roles(tmp_path, roles):
    with pytest.raises(ValueError, match="vision_roles"):
        load_gateway_config(_write_config(tmp_path, vision_roles=roles))


@pytest.mark.parametrize(
    "generation",
    [
        {"size": "auto"},
        {"sizes": ["1024x1024", "1536x1024"]},
        DYNAMIC,
        {key: value for key, value in DYNAMIC.items() if key != "max_pixels"},
    ],
)
def test_valid_generation_policies(tmp_path, generation):
    config = load_gateway_config(
        _write_config(tmp_path, image_models=[{"model_name": "refine", "generation": generation}])
    )
    assert config.image_edits["refine"].generation == generation


@pytest.mark.parametrize(
    "generation",
    [
        None,
        {},
        [],
        {"image_size": "4K"},
        {"size": "1024x1024"},
        {"size": "auto", "sizes": ["1024x1024"]},
        {"sizes": []},
        {"sizes": "1024x1024"},
        {"sizes": ["1024*1024"]},
        {"sizes": ["0x1024"]},
        {"sizes": ["-1x1024"]},
        {"sizes": ["1024x1024", "1024x1024"]},
        {"sizes": [[]]},
        {**DYNAMIC, "delimiter": "*"},
        {**DYNAMIC, "grid": 0},
        {**DYNAMIC, "grid": True},
        {**DYNAMIC, "min_side": 0},
        {**DYNAMIC, "min_side": 513},
        {**DYNAMIC, "max_side": 511},
        {**DYNAMIC, "max_side": 4096.0},
        {**DYNAMIC, "max_pixels": 512 * 512 - 1},
        {**DYNAMIC, "extra": True},
        {**DYNAMIC, "max_pixels": None},
    ],
)
def test_invalid_generation_policies_are_rejected(tmp_path, generation):
    with pytest.raises(ValueError, match="generation"):
        load_gateway_config(_write_config(tmp_path, image_models=[{"model_name": "refine", "generation": generation}]))


@pytest.mark.parametrize("field", ["fallbacks", "context_window_fallbacks", "content_policy_fallbacks"])
def test_same_modality_compatible_fallbacks_are_native_and_unchanged(tmp_path, field):
    source = _config()
    source["model_list"].extend(
        [
            {"model_name": "other-image", "litellm_params": {"model": "azure/image"}},
            {"model_name": "other-vision", "litellm_params": {"model": "anthropic/vision"}},
        ]
    )
    source["image_models"].append({"model_name": "other-image", "generation": {"size": "auto"}})
    source["router_settings"][field] = [{"refine": ["other-image"]}, {"vision": ["other-vision"]}]
    config = load_gateway_config(_write_config(tmp_path, source))
    assert config.router_settings == source["router_settings"]


@pytest.mark.parametrize("field", ["fallbacks", "context_window_fallbacks", "content_policy_fallbacks"])
@pytest.mark.parametrize(
    "fallbacks",
    [
        None,
        {},
        [None],
        [{}],
        [{"vision": "refine"}],
        [{"missing": []}],
        [{"vision": ["missing"]}],
        [{"vision": ["refine"]}],
        [{"refine": ["vision"]}],
        [{"vision": [[]]}],
    ],
)
def test_fallbacks_reject_unknown_aliases_cross_modality_and_bad_shapes(tmp_path, field, fallbacks):
    with pytest.raises(ValueError, match=field):
        load_gateway_config(_write_config(tmp_path, router_settings={field: fallbacks}))


def test_image_fallback_cannot_change_size_policy(tmp_path):
    source = _config()
    source["model_list"].append({"model_name": "other-image", "litellm_params": {"model": "openai/other-image"}})
    source["image_models"].append({"model_name": "other-image", "generation": {"sizes": ["1024x1024"]}})
    source["router_settings"]["fallbacks"] = [{"refine": ["other-image"]}]
    with pytest.raises(ValueError, match="generation policies must match"):
        load_gateway_config(_write_config(tmp_path, source))


@pytest.mark.parametrize("field", ["image", "images", "mask", "prompt", "messages", "input", "attachments", "role"])
def test_image_deployment_cannot_override_current_image_request(tmp_path, field):
    source = _config()
    source["model_list"][1]["litellm_params"][field] = "hidden-input"
    with pytest.raises(ValueError, match="business image"):
        load_gateway_config(_write_config(tmp_path, source))


@pytest.mark.parametrize("n", [0, 2, True, "1", None])
def test_image_deployment_cannot_request_multiple_outputs(tmp_path, n):
    source = _config()
    source["model_list"][1]["litellm_params"]["n"] = n
    with pytest.raises(ValueError, match="exactly 1"):
        load_gateway_config(_write_config(tmp_path, source))


@pytest.mark.parametrize(
    "field",
    ["images", "input_images", "reference_images", "role", "model", "api_key", "api_base", "url", "headers", "size"],
)
def test_nested_provider_extensions_cannot_hide_inputs_or_connection_overrides(tmp_path, field):
    source = _config()
    source["model_list"][1]["litellm_params"]["extra_body"] = {"options": [{field: "hidden-value"}]}
    with pytest.raises(ValueError, match="reserved input or request fields") as error:
        load_gateway_config(_write_config(tmp_path, source))
    assert "hidden-value" not in str(error.value)


@pytest.mark.parametrize("extra_body", [None, [], "os.environ/OPAQUE_BODY", {"options": {"n": 2}}])
def test_provider_extensions_require_inspectable_mapping_and_one_output(tmp_path, extra_body):
    source = _config()
    source["model_list"][1]["litellm_params"]["extra_body"] = extra_body
    with pytest.raises(ValueError, match="extra_body"):
        load_gateway_config(_write_config(tmp_path, source))


def test_native_provider_extensions_and_single_output_remain_supported(tmp_path):
    source = _config()
    params = source["model_list"][1]["litellm_params"]
    params.update(n=1, extra_body={"quality": "high", "rendering": {"background": "opaque", "n": 1}})
    result = load_gateway_config(_write_config(tmp_path, source))
    for deployment in source["model_list"]:
        deployment["litellm_params"].setdefault("max_retries", 0)
    assert result.model_list == source["model_list"]


@pytest.mark.parametrize("source", [None, [], "config", {}])
def test_config_root_is_validated(tmp_path, source):
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(source))
    with pytest.raises(ValueError):
        load_gateway_config(path)


@pytest.mark.parametrize("deployments", [None, [], {}, [None], [{"model_name": [], "litellm_params": {}}]])
def test_deployments_are_validated(tmp_path, deployments):
    with pytest.raises(ValueError, match=r"deployment|model_list"):
        load_gateway_config(_write_config(tmp_path, model_list=deployments))


def test_yaml_parse_errors_do_not_echo_source_values(tmp_path):
    path = tmp_path / "invalid.yaml"
    path.write_text("api_key: [do-not-print-this-secret")
    with pytest.raises(ValueError, match="not valid YAML") as error:
        load_gateway_config(path)
    assert "do-not-print-this-secret" not in str(error.value)


def test_resolve_env_recurses_without_mutation(monkeypatch):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "private-test-value")
    source = {"nested": [{"api_key": "os.environ/TEST_GATEWAY_KEY"}, 7, None]}
    assert resolve_env(source) == {"nested": [{"api_key": "private-test-value"}, 7, None]}
    assert source["nested"][0]["api_key"] == "os.environ/TEST_GATEWAY_KEY"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_environment_values_are_rejected(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TEST_GATEWAY_KEY", raising=False)
    else:
        monkeypatch.setenv("TEST_GATEWAY_KEY", value)
    with pytest.raises(ValueError, match="TEST_GATEWAY_KEY"):
        resolve_env({"headers": [{"authorization": "os.environ/TEST_GATEWAY_KEY"}]})


def test_invalid_environment_reference_never_echoes_its_contents():
    with pytest.raises(ValueError, match="Invalid AI gateway environment") as error:
        resolve_env("os.environ/private-value/invalid")
    assert "private-value" not in str(error.value)


def test_default_catalog_resolves_independent_service_settings(monkeypatch):
    for role in ("VISION", "IMAGE"):
        monkeypatch.setenv(f"AI_{role}_API_BASE", f"https://custom-{role.lower()}.test/v1")
        monkeypatch.setenv(f"AI_{role}_API_KEY", f"custom-{role.lower()}-key")
        monkeypatch.setenv(f"AI_{role}_MODEL", f"openai/custom-{role.lower()}")
    config = load_gateway_config()
    deployments = resolve_env(config.model_list)
    assert len(deployments) == 2
    for deployment, role in zip(deployments, ("vision", "image"), strict=True):
        params = deployment["litellm_params"]
        assert params["model"] == f"openai/custom-{role}"
        assert params["api_base"] == f"https://custom-{role}.test/v1"
        assert params["api_key"] == f"custom-{role}-key"
    assert config.image_edits[config.default_image_alias].generation == {"size": "auto"}
    assert config.router_settings["num_retries"] == 0
    assert config.router_settings["fallbacks"] == []
    assert deployments[1]["litellm_params"]["max_retries"] == 0


@pytest.mark.parametrize("explicit", [False, True])
def test_retry_defaults_are_safe_for_custom_catalogs(tmp_path, explicit):
    source = _config()
    source.pop("router_settings")
    if explicit:
        source["router_settings"] = {"num_retries": 2}
        source["model_list"][1]["litellm_params"]["max_retries"] = 3
    config = load_gateway_config(_write_config(tmp_path, source))
    assert config.router_settings["num_retries"] == (2 if explicit else 0)
    assert config.model_list[0]["litellm_params"]["max_retries"] == 0
    assert config.model_list[1]["litellm_params"]["max_retries"] == (3 if explicit else 0)
