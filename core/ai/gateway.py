"""Provider-neutral model routing through LiteLLM and safe image I/O."""

import base64
import binascii

import httpx
import litellm
import numpy as np
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
from pydantic import BaseModel, ValidationError

from core.ai.configuration import load_gateway_config, resolve_env
from core.ai.types import ImageEditResult, ModelConfigurationError, ModelRequestError
from core.image_security import (
    MAX_IMAGE_BYTES,
    decode_data_uri,
    decode_image_bytes,
    download_image_bytes,
    encode_snapshot_image,
)


class AIGateway:
    """Keep application aliases and image contracts independent of LiteLLM providers."""

    def __init__(self, config_path=None):
        config = load_gateway_config(config_path)
        self._model_list = config.model_list
        self.vision_roles = config.vision_roles
        self._router_settings = config.router_settings
        self.image_edits = config.image_edits
        self.default_image_alias = config.default_image_alias
        self._router = None

    @property
    def configured(self):
        return bool(self._model_list)

    @staticmethod
    def _service_error(kind: str, exc: Exception) -> RuntimeError:
        """Return application-safe diagnostics without copying any upstream error text."""
        return ModelRequestError(kind, getattr(exc, "status_code", None))

    def validate_environment(self):
        """Check referenced settings without a network call or initializing LiteLLM."""
        resolve_env(self._model_list)
        resolve_env(self._router_settings)

    def _get_router(self):
        if self._router is None:
            # Native model_list is the only connection/model registry. Do not synthesize
            # prices or override provider detection: LiteLLM owns those decisions.
            try:
                router = litellm.Router(
                    model_list=resolve_env(self._model_list),
                    **resolve_env(self._router_settings),
                )
            except Exception:
                # LiteLLM can raise HTTP-shaped errors during local provider detection.
                # Do not label them upstream failures or expose connection details.
                raise ModelConfigurationError() from None
            # LiteLLM 1.98.0 factory drops call-level client for aimage_edit. Bind the
            # transport in its native endpoint registration so retries/fallbacks still
            # use Router, never a parallel provider path. Remove this registration once
            # an upstream version forwards client and the redirect regression passes.
            router.aimage_edit = router.factory_function(self._native_image_edit, call_type="aimage_edit")
            self._router = router
        return self._router

    def vision_alias(self, role: str) -> str:
        """Resolve a configured semantic VLM role to its stable model alias."""
        try:
            return self.vision_roles[role]
        except KeyError as exc:
            raise ValueError(f"Unknown VLM role: {role}") from exc

    async def vision_completion(self, messages, alias=None, **kwargs):
        resolved_alias = alias or self.vision_alias("default")
        if resolved_alias in self.image_edits or resolved_alias not in {d["model_name"] for d in self._model_list}:
            raise ValueError(f"Unknown vision model alias: {resolved_alias}")
        forbidden = {"model", "api_key", "api_base", "base_url", "custom_llm_provider", "client", "api_version"}
        if forbidden.intersection(kwargs):
            raise ValueError("Model connections may only be configured in model_list")
        router = self._get_router()
        try:
            return await router.acompletion(model=resolved_alias, messages=messages, **kwargs)
        except Exception as exc:
            # Upstream exception bodies may echo credentials or image payloads.
            raise self._service_error("Vision", exc) from None

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

    async def structured_vision_completion(
        self, messages, contract: type[BaseModel], alias=None, *, max_text_repairs: int = 2, **kwargs
    ):
        """Validate structured output with an explicit text-repair budget, never a provider parameter."""
        if (
            isinstance(max_text_repairs, bool)
            or not isinstance(max_text_repairs, int)
            or not 0 <= max_text_repairs <= 2
        ):
            raise ValueError("max_text_repairs must be an integer between zero and two")
        response = await self.vision_completion(messages, alias=alias, **kwargs)
        try:
            return response, self.parse_structured_response(response, contract)
        except ValueError as first_error:
            if max_text_repairs == 0:
                raise
            invalid_text = self.response_text(response)
            validation_error = first_error
            repair_error = first_error
            # A second bounded repair handles providers that fix missing fields on the first pass but
            # overlook a remaining length/enum violation. These calls contain JSON only, never images.
            for attempt in range(1, max_text_repairs + 1):
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
                            f"Repair attempt {attempt} of {max_text_repairs}.\n\n"
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
                f"{first_error}; structured repair failed after {max_text_repairs} attempts: {repair_error}"
            ) from repair_error

    @staticmethod
    def response_text(response):
        content = response.choices[0].message.content
        if not isinstance(content, str):
            raise TypeError("LiteLLM vision response content must be text")
        return content

    def image_model_names(self, alias: str) -> str:
        """Return only upstream model identifiers for the public catalog, never connection parameters."""
        models = dict.fromkeys(
            resolve_env(deployment["litellm_params"]["model"])
            for deployment in self._model_list
            if deployment["model_name"] == alias
        )
        return ", ".join(models)

    @staticmethod
    def _image_bytes(image) -> bytes:
        """Validate the one in-memory input before LiteLLM can resolve files or URLs."""
        if isinstance(image, np.ndarray):
            # Only internal callers supply decoded canvases; external bytes/URIs
            # still use the strict upload limit below.
            return encode_snapshot_image(image)
        if isinstance(image, str):
            decode_data_uri(image)
            return base64.b64decode(image.split(",", 1)[1], validate=True)
        if not isinstance(image, bytes):
            raise TypeError("Image edit input must be a decoded BGR canvas, encoded bytes or an image data URI")
        decode_image_bytes(image)
        return image

    @staticmethod
    async def _reject_redirect(response: httpx.Response) -> None:
        # Even 307/308 can replay the full photograph to another host. Fail before
        # any transport decides to follow; protocol translation remains in LiteLLM.
        if response.is_redirect:
            raise ValueError("Model endpoint redirects are not allowed; configure its final API base URL")

    async def _native_image_edit(self, **kwargs):
        """Bind per-attempt transport safety without implementing any provider protocol."""
        request_id = None

        async def check_response(response):
            nonlocal request_id
            await self._reject_redirect(response)
            request_id = response.headers.get("x-request-id")

        transport = AsyncHTTPHandler(event_hooks={"response": [check_response]})
        transport.client.follow_redirects = False
        try:
            response = await litellm.aimage_edit(client=transport, **kwargs)
            response._hidden_params["personaweave_request_id"] = request_id
            return response
        finally:
            await transport.close()

    async def edit_image(self, images, prompt, alias=None, parameters=None):
        """Route exactly one whole image through LiteLLM and validate its normalized response."""
        resolved_alias = alias or self.default_image_alias
        if resolved_alias not in self.image_edits:
            raise ValueError(f"Unknown image-edit model alias: {resolved_alias}")
        if not isinstance(images, (list, tuple)) or len(images) != 1:
            raise ValueError("Image editing requires exactly one current whole image")
        image = self._image_bytes(images[0])
        parameters = dict(parameters or {})
        if set(parameters) - {"size"}:
            raise ValueError("Only the declared generation size may be passed by the refinement service")
        router = self._get_router()
        try:
            response = await router.aimage_edit(model=resolved_alias, image=image, prompt=prompt, n=1, **parameters)
        except Exception as exc:
            raise self._service_error("Image", exc) from None
        if not response.data or len(response.data) != 1:
            raise ValueError("LiteLLM image edit must return exactly one image")
        item = response.data[0]
        if item.b64_json:
            if len(item.b64_json) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
                raise ValueError("Image exceeds the encoded byte limit")
            try:
                image_bytes = base64.b64decode(item.b64_json, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("LiteLLM image edit returned invalid base64 image data") from exc
            decode_image_bytes(image_bytes)
        elif item.url:
            # Returned URLs are untrusted and use a separate, unauthenticated client.
            async with httpx.AsyncClient(timeout=180, follow_redirects=False, trust_env=False) as client:
                image_bytes = await download_image_bytes(item.url, client)
        else:
            raise ValueError("LiteLLM image edit returned neither b64_json nor url")
        hidden = getattr(response, "_hidden_params", {}) or {}
        usage = getattr(response, "usage", None)
        if isinstance(usage, BaseModel):
            usage = usage.model_dump(mode="json", exclude_none=True)
        headers = hidden.get("additional_headers") or {}
        return ImageEditResult(
            image_bytes=image_bytes,
            # Never report the initially configured model as the actual result after a fallback.
            model=getattr(response, "model", None) or hidden.get("litellm_model_name") or hidden.get("model") or "",
            provider=hidden.get("custom_llm_provider") or "litellm",
            usage=usage if isinstance(usage, dict) else {},
            request_id=hidden.get("personaweave_request_id")
            or getattr(response, "_request_id", None)
            or headers.get("x-request-id"),
        )
