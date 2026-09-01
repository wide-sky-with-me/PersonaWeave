"""Configuration-driven AI gateway with explicit provider boundaries."""

import base64
import binascii
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import litellm
import yaml
from pydantic import BaseModel, ValidationError

from core.image_security import decode_image_bytes, download_image_bytes
from core.providers.dmx_gemini import DMXGeminiProvider
from core.providers.dmx_images_edit import DMXImagesEditProvider
from core.providers.dmx_responses import (
    DMXInputParametersResponsesProvider,
    DMXResponsesProvider,
    ImageEditResult,
)
from core.providers.dmx_seedream import DMXSeedreamProvider


def _resolve_env(value):
    if not isinstance(value, str) or not value.startswith("os.environ/"):
        return value
    name = value.split("/", 1)[1]
    resolved = os.getenv(name)
    if resolved is None:
        raise ValueError(f"Required AI gateway environment variable is missing: {name}")
    return resolved


@dataclass(frozen=True)
class ImageEditConfig:
    """One stable image-edit alias and its provider parameters."""

    model_name: str
    params: dict
    provider: str = "litellm"
    display_name: str = ""
    description: str = ""
    generation: dict = field(default_factory=dict)


class AIGateway:
    """Expose stable aliases while providers own their documented protocols."""

    def __init__(self, config_path=None):
        root = Path(__file__).resolve().parents[1]
        path = Path(config_path or os.getenv("PERSONAWEAVE_AI_CONFIG", root / "config/ai_gateway.yaml"))
        if not path.is_file():
            raise FileNotFoundError(f"AI gateway config not found: {path}")
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        model_list = config.get("model_list")
        if not isinstance(model_list, list) or not model_list:
            raise ValueError("AI gateway config requires a non-empty model_list")
        for deployment in model_list:
            if not deployment.get("model_name") or not deployment.get("litellm_params", {}).get("model"):
                raise ValueError("Each LiteLLM deployment requires model_name and litellm_params.model")
        self._model_list = model_list
        configured_vision_aliases = {deployment["model_name"] for deployment in model_list}
        self.vision_roles = dict(config.get("vision_roles") or {})
        required_vision_roles = {"default", "identity_arbitration", "refinement"}
        if set(self.vision_roles) != required_vision_roles:
            raise ValueError(f"vision_roles must contain exactly: {', '.join(sorted(required_vision_roles))}")
        unknown_roles = {
            role: alias for role, alias in self.vision_roles.items() if alias not in configured_vision_aliases
        }
        if unknown_roles:
            raise ValueError(f"vision_roles reference unknown model aliases: {unknown_roles}")
        self._router_settings = config.get("router_settings", {})
        self._router = None

        raw_edits = config.get("image_models")
        if not isinstance(raw_edits, list) or not raw_edits:
            raise ValueError("AI gateway config requires a non-empty image_models list")
        self.image_edits = {}
        for edit in raw_edits:
            alias = edit.get("model_name")
            provider = edit.get("provider")
            params = edit.get("params") or edit.get("litellm_params") or {}
            generation = edit.get("generation") or {}
            if (
                not alias
                or provider
                not in {
                    "litellm",
                    "dmx-gemini",
                    "dmx-images-edit",
                    "dmx-responses",
                    "dmx-input-responses",
                    "dmx-seedream",
                }
                or not params.get("model")
            ):
                raise ValueError("Each image model requires model_name, provider, and params.model")
            if alias in self.image_edits:
                raise ValueError(f"Duplicate image model alias: {alias}")
            if provider == "dmx-gemini":
                if generation.get("image_size") not in {"2K", "4K"}:
                    raise ValueError(f"Gemini image model '{alias}' requires generation.image_size (2K or 4K)")
            elif not all(generation.get(key) for key in ("max_side", "min_side", "grid", "delimiter")):
                raise ValueError(f"Image model '{alias}' requires generation.max_side/min_side/grid/delimiter")
            self.image_edits[alias] = ImageEditConfig(
                model_name=alias,
                provider=provider,
                params=dict(params),
                display_name=str(edit.get("display_name") or alias),
                description=str(edit.get("description") or ""),
                generation=dict(generation),
            )
        self.default_image_alias = config.get("default_image_alias") or next(iter(self.image_edits))
        if self.default_image_alias not in self.image_edits:
            raise ValueError("default_image_alias must reference a configured image model")

    @property
    def configured(self):
        return bool(self._model_list)

    @staticmethod
    def _register_custom_model_costs(deployments):
        """Register explicit pricing metadata before Router registers unknown backend model names."""
        for deployment in deployments:
            model = deployment["litellm_params"]["model"]
            model_info = deployment.get("model_info") or {}
            if not model_info:
                continue
            provider = model.split("/", 1)[0] if "/" in model else "openai"
            litellm.register_model(
                {
                    model: {
                        **model_info,
                        "litellm_provider": provider,
                        "mode": "chat",
                    }
                },
                persist_across_reloads=False,
            )

    def _get_router(self):
        if self._router is None:
            deployments = [
                {
                    **deployment,
                    "litellm_params": {key: _resolve_env(value) for key, value in deployment["litellm_params"].items()},
                }
                for deployment in self._model_list
            ]
            self._register_custom_model_costs(deployments)
            settings = self._router_settings
            self._router = litellm.Router(
                model_list=deployments,
                routing_strategy=settings.get("routing_strategy", "simple-shuffle"),
                num_retries=settings.get("num_retries", 2),
                timeout=settings.get("timeout", 180),
                fallbacks=settings.get("fallbacks", []),
            )
        return self._router

    def vision_alias(self, role: str) -> str:
        """Resolve a configured semantic VLM role to its stable model alias."""
        try:
            return self.vision_roles[role]
        except KeyError as exc:
            raise ValueError(f"Unknown VLM role: {role}") from exc

    async def vision_completion(self, messages, alias=None, **kwargs):
        resolved_alias = alias or self.vision_alias("default")
        return await self._get_router().acompletion(model=resolved_alias, messages=messages, **kwargs)

    def parse_structured_response(self, response, contract: type[BaseModel]):
        """Validate one model response at the provider boundary with a Pydantic contract."""
        text = self.response_text(response).strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        try:
            return contract.model_validate_json(text.strip())
        except ValidationError as exc:
            raise ValueError(f"Invalid {contract.__name__} response: {exc}") from exc

    async def structured_vision_completion(self, messages, contract: type[BaseModel], alias=None, **kwargs):
        """Call a VLM and make bounded text-only repairs after schema validation failures."""
        response = await self.vision_completion(messages, alias=alias, **kwargs)
        try:
            return response, self.parse_structured_response(response, contract)
        except ValueError as first_error:
            invalid_text = self.response_text(response)
            validation_error = first_error
            repair_error = first_error
            # A second bounded repair handles providers that fix missing fields on the first pass but
            # overlook a remaining length/enum violation. These calls contain JSON only, never images.
            for attempt in range(1, 3):
                repair_messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are a deterministic JSON Schema repairer. Repair only the supplied JSON; do not "
                            "re-analyze an image, add or remove reports/issues, invent new findings, or change valid "
                            "factual judgments. You MUST change every field path named in Validation failure; never "
                            "copy an invalid value at one of those paths unchanged. Add every missing required field. "
                            "For a missing enum field, choose the allowed value most consistent with that item's existing "
                            "description. For a string_too_short field, rewrite it as meaningful, schema-compliant text "
                            "using the surrounding description; never pad it with punctuation or spaces. In particular, "
                            "a face issue region must name a precise anatomical anchor: replace vague values such as "
                            "'双眼' with a description-specific anchor such as '双眼瞳孔与视线方向' or '双侧上眼睑边缘'. "
                            "Before returning, check every listed validation path against the schema. Return only one JSON "
                            "value and no Markdown."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Repair attempt {attempt} of 2.\n\n"
                            f"Pydantic schema:\n{contract.model_json_schema()}\n\n"
                            f"Validation failure:\n{validation_error}\n\nInvalid JSON:\n{invalid_text}"
                        ),
                    },
                ]
                repaired = await self.vision_completion(
                    repair_messages,
                    alias=alias,
                    temperature=0,
                    response_format={"type": "json_object"},
                    timeout=kwargs.get("timeout", 110),
                )
                try:
                    return repaired, self.parse_structured_response(repaired, contract)
                except ValueError as exc:
                    repair_error = exc
                    validation_error = exc
                    invalid_text = self.response_text(repaired)
            raise ValueError(
                f"{first_error}; structured repair failed after 2 attempts: {repair_error}"
            ) from repair_error

    @staticmethod
    def response_text(response):
        content = response.choices[0].message.content
        if not isinstance(content, str):
            raise TypeError("LiteLLM vision response content must be text")
        return content

    @staticmethod
    def _as_data_url(image):
        if isinstance(image, str) and image.startswith("data:image/"):
            return image
        if not isinstance(image, bytes):
            raise TypeError("Image edit inputs must be encoded bytes or image data URLs")
        return "data:image/png;base64," + base64.b64encode(image).decode("ascii")

    async def edit_image(self, images, prompt, alias=None, parameters=None):
        """Run an image model by stable alias and normalize provider metadata."""
        resolved_alias = alias or self.default_image_alias
        if resolved_alias not in self.image_edits:
            available = ", ".join(sorted(self.image_edits))
            raise ValueError(f"Unknown image-edit model alias '{resolved_alias}'. Configured aliases: {available}")
        config = self.image_edits[resolved_alias]
        params = {key: _resolve_env(value) for key, value in config.params.items()}
        if config.provider in {
            "dmx-gemini",
            "dmx-images-edit",
            "dmx-responses",
            "dmx-input-responses",
            "dmx-seedream",
        }:
            provider_classes = {
                "dmx-gemini": DMXGeminiProvider,
                "dmx-images-edit": DMXImagesEditProvider,
                "dmx-responses": DMXResponsesProvider,
                "dmx-input-responses": DMXInputParametersResponsesProvider,
                "dmx-seedream": DMXSeedreamProvider,
            }
            provider = provider_classes[config.provider](
                api_base=params["api_base"],
                api_key=params["api_key"],
                timeout=float(params.get("timeout", 300)),
            )
            return await provider.edit(
                model=params["model"],
                images=[self._as_data_url(image) for image in images],
                prompt=prompt,
                parameters=parameters,
            )

        response = await litellm.aimage_edit(
            model=params["model"],
            image=images,
            prompt=prompt,
            n=1,
            api_base=params.get("api_base"),
            api_key=params.get("api_key"),
            timeout=float(params.get("timeout", 180)),
        )
        item = response.data[0]
        if item.b64_json:
            try:
                image_bytes = base64.b64decode(item.b64_json, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("LiteLLM image edit returned invalid base64 image data") from exc
            decode_image_bytes(image_bytes)
        elif item.url:
            async with httpx.AsyncClient(timeout=180) as client:
                image_bytes = await download_image_bytes(item.url, client)
        else:
            raise ValueError("LiteLLM image edit returned neither b64_json nor url")
        return ImageEditResult(
            image_bytes=image_bytes,
            model=params["model"],
            provider="litellm",
            usage=getattr(response, "usage", None) or {},
        )
