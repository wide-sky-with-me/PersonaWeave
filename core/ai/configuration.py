"""Load the native LiteLLM deployment catalog without importing provider runtimes."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def resolve_env(value):
    """Recursively resolve environment references without logging credential values."""
    if isinstance(value, dict):
        return {key: resolve_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_env(item) for item in value]
    if not isinstance(value, str) or not value.startswith("os.environ/"):
        return value
    name = value.split("/", 1)[1]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("Invalid AI gateway environment variable reference")
    resolved = os.getenv(name)
    if resolved is None or not resolved.strip():
        raise ValueError(f"Required AI gateway environment variable is missing: {name}")
    return resolved


@dataclass(frozen=True)
class ImageEditConfig:
    """Application metadata for an image alias in the shared deployment catalog."""

    model_name: str
    display_name: str = ""
    description: str = ""
    generation: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayConfig:
    """Validated model configuration consumed by the runtime gateway."""

    model_list: list[dict]
    vision_roles: dict[str, str]
    router_settings: dict
    image_edits: dict[str, ImageEditConfig]
    default_image_alias: str


def _nonempty_string(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_generation(generation) -> None:
    """Require one explicit size policy; reject impossible dynamic image dimensions."""
    if not isinstance(generation, dict) or not generation:
        raise ValueError("Image generation requires an explicit size policy")
    if set(generation) == {"size"} and generation["size"] == "auto":
        return
    if set(generation) == {"sizes"}:
        sizes = generation["sizes"]
        if (
            not isinstance(sizes, list)
            or not sizes
            or any(not isinstance(size, str) or not re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", size) for size in sizes)
            or len(set(sizes)) != len(sizes)
        ):
            raise ValueError("Image generation.sizes must contain unique positive WIDTHxHEIGHT strings")
        return
    numeric_fields = {"max_side", "min_side", "grid"}
    required_fields = numeric_fields | {"delimiter"}
    if not required_fields <= set(generation) or set(generation) - required_fields - {"max_pixels"}:
        raise ValueError("Image generation requires exactly one of size:auto, sizes, or dynamic dimension constraints")
    if generation["delimiter"] != "x":
        raise ValueError("Image generation delimiter must be x")
    numeric_fields |= {"max_pixels"} & set(generation)
    if any(type(generation[key]) is not int or generation[key] <= 0 for key in numeric_fields):
        raise ValueError("Image generation dimension constraints must be positive integers")
    minimum, maximum, grid = generation["min_side"], generation["max_side"], generation["grid"]
    if minimum > maximum or minimum % grid or maximum % grid:
        raise ValueError("Image generation sides must be ordered and divisible by grid")
    if "max_pixels" in generation and generation["max_pixels"] < minimum * minimum:
        raise ValueError("Image generation max_pixels cannot accommodate the minimum canvas")


def _validate_image_params(params) -> None:
    """Reject deployment payloads that could replace or augment the single current image."""
    payload_fields = {
        "image",
        "images",
        "image_url",
        "image_urls",
        "input_image",
        "input_images",
        "mask",
        "masks",
        "prompt",
        "messages",
        "message",
        "input",
        "inputs",
        "contents",
        "content",
        "parts",
        "attachments",
        "file",
        "files",
        "role",
        "reference_image",
        "reference_images",
        "control_image",
        "control_images",
        "image_path",
        "image_base64",
        "image_bytes",
        "inline_data",
        "inlinedata",
        "file_data",
        "filedata",
        "file_uri",
        "fileuri",
        "imageurl",
        "imageurls",
    }
    if payload_fields & set(params):
        raise ValueError("Image deployment cannot override business image, prompt, or attachment fields")
    if "n" in params and (type(params["n"]) is not int or params["n"] != 1):
        raise ValueError("Image deployment n must be exactly 1")
    if "extra_body" not in params:
        return
    extra_body = params["extra_body"]
    if not isinstance(extra_body, dict):
        raise ValueError("Image deployment extra_body must be a mapping of provider parameters")
    # Provider extensions can be nested, but cannot smuggle another input or route.
    reserved = payload_fields | {
        "model",
        "model_name",
        "api_key",
        "api_base",
        "base_url",
        "api_version",
        "api_type",
        "custom_llm_provider",
        "headers",
        "extra_headers",
        "authorization",
        "endpoint",
        "url",
        "extra_body",
        "size",
        "sizes",
        "image_size",
        "aspect_ratio",
    }
    pending = [extra_body]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if any(not isinstance(key, str) or key.lower().replace("-", "_") in reserved for key in value):
                raise ValueError("Image deployment extra_body contains reserved input or request fields")
            if "n" in value and (type(value["n"]) is not int or value["n"] != 1):
                raise ValueError("Image deployment extra_body n must be exactly 1")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def _validate_fallbacks(router_settings, aliases, image_edits) -> None:
    """Keep every Router fallback in its modality and its image parameter contract."""
    for setting in ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks"):
        if setting not in router_settings:
            continue
        chains = router_settings[setting]
        if not isinstance(chains, list):
            raise ValueError(f"router_settings.{setting} must be a list of alias mappings")
        for chain in chains:
            if not isinstance(chain, dict) or len(chain) != 1:
                raise ValueError(f"router_settings.{setting} requires single-alias mappings")
            source, targets = next(iter(chain.items()))
            if not _nonempty_string(source) or source not in aliases:
                raise ValueError(f"router_settings.{setting} references an unknown model alias")
            if not isinstance(targets, list):
                raise ValueError(f"router_settings.{setting} targets must be a list")
            for target in targets:
                if not _nonempty_string(target) or target not in aliases:
                    raise ValueError(f"router_settings.{setting} references an unknown model alias")
                if (source in image_edits) != (target in image_edits):
                    raise ValueError(f"router_settings.{setting} cannot cross vision and image roles")
                # The service chooses parameters before Router selects a fallback deployment.
                if source in image_edits and image_edits[source].generation != image_edits[target].generation:
                    raise ValueError(f"router_settings.{setting} image generation policies must match")


def load_gateway_config(config_path=None) -> GatewayConfig:
    """Read native deployments and application metadata; reject malformed or legacy config.

    Environment references stay unresolved until runtime validation. This keeps parsing
    side-effect free and permits catalog inspection without loading provider libraries.
    """
    root = Path(__file__).resolve().parents[2]
    path = Path(config_path or os.getenv("PERSONAWEAVE_AI_CONFIG", root / "config/ai_gateway.yaml"))
    if not path.is_file():
        raise FileNotFoundError(f"AI gateway config not found: {path}")
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        # PyYAML's normal exception includes source lines, potentially containing secrets.
        raise ValueError("AI gateway configuration is not valid YAML") from None
    if not isinstance(config, dict):
        raise ValueError("AI gateway config must be a mapping")
    allowed_fields = {"model_list", "image_models", "vision_roles", "default_image_alias", "router_settings"}
    if set(config) - allowed_fields:
        raise ValueError("AI gateway config contains unsupported or retired fields")
    model_list = config.get("model_list")
    if not isinstance(model_list, list) or not model_list:
        raise ValueError("AI gateway config requires a non-empty model_list")
    aliases = set()
    for deployment in model_list:
        if not isinstance(deployment, dict):
            raise ValueError("Each LiteLLM deployment must be a mapping")
        alias, params = deployment.get("model_name"), deployment.get("litellm_params")
        if not _nonempty_string(alias) or not isinstance(params, dict) or not _nonempty_string(params.get("model")):
            raise ValueError("Each LiteLLM deployment requires model_name and litellm_params.model")
        if {"provider", "params", "generation"} & set(deployment):
            raise ValueError("Deployments must use native litellm_params and separate image metadata")
        # Opt in to retries explicitly, including custom catalogs and new deployments.
        params.setdefault("max_retries", 0)
        # A Router model group can legitimately contain multiple deployments.
        aliases.add(alias)

    raw_edits = config.get("image_models")
    if not isinstance(raw_edits, list) or not raw_edits:
        raise ValueError("AI gateway config requires a non-empty image_models metadata list")
    image_edits = {}
    metadata_fields = {"model_name", "display_name", "description", "generation"}
    for edit in raw_edits:
        if not isinstance(edit, dict) or set(edit) - metadata_fields:
            raise ValueError("Image metadata only accepts model_name, display_name, description, generation")
        alias = edit.get("model_name")
        if not _nonempty_string(alias) or alias not in aliases:
            raise ValueError("Image metadata references an unknown model alias")
        if alias in image_edits:
            raise ValueError("Duplicate image model metadata alias")
        display_name, description = edit.get("display_name", alias), edit.get("description", "")
        if not _nonempty_string(display_name) or not isinstance(description, str):
            raise ValueError("Image metadata display_name and description must be strings")
        generation = edit.get("generation")
        _validate_generation(generation)
        image_edits[alias] = ImageEditConfig(alias, display_name, description, dict(generation))

    for deployment in model_list:
        if deployment["model_name"] in image_edits:
            _validate_image_params(deployment["litellm_params"])

    vision_roles = config.get("vision_roles")
    required_roles = {"default", "identity_arbitration", "refinement"}
    if not isinstance(vision_roles, dict) or set(vision_roles) != required_roles:
        raise ValueError("vision_roles must contain exactly: default, identity_arbitration, refinement")
    for alias in vision_roles.values():
        if not _nonempty_string(alias) or alias not in aliases:
            raise ValueError("vision_roles reference unknown model aliases")
        if alias in image_edits:
            raise ValueError("vision_roles cannot reference image model aliases")
    default_image_alias = config.get("default_image_alias")
    if not _nonempty_string(default_image_alias) or default_image_alias not in image_edits:
        raise ValueError("default_image_alias must reference a configured image model")
    router_settings = config.get("router_settings", {})
    if not isinstance(router_settings, dict):
        raise ValueError("router_settings must be a mapping")
    router_settings.setdefault("num_retries", 0)
    _validate_fallbacks(router_settings, aliases, image_edits)
    return GatewayConfig(model_list, dict(vision_roles), router_settings, image_edits, default_image_alias)
