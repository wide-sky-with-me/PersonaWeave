import base64
import io
import json
import os
import re
from urllib.parse import urlparse

import httpx
import numpy as np
import torch
import torch.nn.functional as functional
from PIL import Image, ImageDraw, ImageFilter

from .yunwu_image_api import image_api_endpoint, json_generation_payload


MINIMUM_IMAGE_API_TIMEOUT_SECONDS = 600.0


def _image_api_timeout_seconds() -> float:
    configured = os.getenv("PERSONAWEAVE_YUNWU_IMAGE_TIMEOUT_SECONDS", "600")
    try:
        return max(MINIMUM_IMAGE_API_TIMEOUT_SECONDS, float(configured))
    except ValueError as error:
        raise ValueError(
            "PERSONAWEAVE_YUNWU_IMAGE_TIMEOUT_SECONDS must be a number"
        ) from error


PROVIDER_ERROR_MARKER = "PERSONAWEAVE_PROVIDER_ERROR:"
_PROVIDER_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def _provider_fact(value: object) -> str | None:
    text = str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else None
    return text if text and _PROVIDER_TOKEN.fullmatch(text) else None


def _provider_error_facts(response: httpx.Response) -> dict[str, object]:
    """Extract bounded structured facts without retaining provider prose."""

    facts: dict[str, object] = {
        "failure_kind": "http",
        "upstream_status": response.status_code,
    }
    try:
        payload = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    objects = [item for item in (error, payload) if isinstance(item, dict)]
    for target, keys in (
        ("provider_code", ("code",)),
        ("provider_type", ("type",)),
        ("request_id", ("request_id", "requestId")),
    ):
        for item in objects:
            value = next(
                (_provider_fact(item.get(key)) for key in keys if item.get(key) is not None),
                None,
            )
            if value is not None:
                facts[target] = value
                break
    if "request_id" not in facts:
        for header in ("x-request-id", "request-id", "x-trace-id"):
            if value := _provider_fact(response.headers.get(header)):
                facts["request_id"] = value
                break
    retry_after = response.headers.get("retry-after", "")
    if retry_after.isdecimal():
        seconds = int(retry_after)
        if 0 <= seconds <= 86400:
            facts["retry_after_seconds"] = seconds
    return facts


def _provider_failure_message(
    response: httpx.Response | None = None,
    *,
    timed_out: bool = False,
) -> str:
    if timed_out:
        facts: dict[str, object] = {"failure_kind": "timeout"}
    elif response is None:
        facts = {"failure_kind": "http"}
    else:
        facts = _provider_error_facts(response)
    marker = PROVIDER_ERROR_MARKER + json.dumps(
        facts,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    fallback = (
        "Yunwu image API request timed out"
        if timed_out
        else "Yunwu image API request failed"
    )
    return f"{marker} {fallback}"


def _provider_canvas(output_size: str, source_width: int, source_height: int) -> tuple[int, int]:
    exact = re.fullmatch(r"(\d{3,4})x(\d{3,4})", output_size.strip().lower())
    if exact:
        width, height = int(exact.group(1)), int(exact.group(2))
        if not (512 <= width <= 4096 and 512 <= height <= 4096):
            raise ValueError("configured image output dimensions must be between 512 and 4096")
        return width, height
    preset = output_size.strip().upper()
    if preset not in {"1K", "2K"}:
        raise ValueError("configured image output size must be 1K, 2K, or WIDTHxHEIGHT")
    if preset == "1K":
        landscape, portrait, square = (1536, 1024), (1024, 1536), (1024, 1024)
    else:
        landscape, portrait, square = (2496, 1664), (1664, 2496), (2048, 2048)
    if source_width > source_height * 1.15:
        return landscape
    if source_height > source_width * 1.15:
        return portrait
    return square


def _tensor_image_to_png(image: torch.Tensor) -> bytes:
    array = image[0].detach().cpu().clamp(0, 1).numpy()
    pil_image = Image.fromarray((array * 255).round().astype(np.uint8), mode="RGB")
    buffer = io.BytesIO()
    pil_image.save(buffer, format="PNG")
    return buffer.getvalue()


def _tensor_image_to_data_uri(image: torch.Tensor) -> str:
    encoded = base64.b64encode(_tensor_image_to_png(image)).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _tensor_mask_to_png(mask: torch.Tensor, width: int, height: int) -> bytes:
    value = mask[0] if mask.ndim == 3 else mask
    array = value.detach().cpu().clamp(0, 1).numpy()
    authorization = Image.fromarray((array * 255).round().astype(np.uint8), mode="L")
    if authorization.size != (width, height):
        raise ValueError("provider mask must already match the fitted provider canvas")
    pil_mask = Image.new("RGBA", authorization.size, (255, 255, 255, 255))
    # The documented edit contract uses fully transparent pixels for the area
    # the provider may change. PersonaWeave masks use 1 for that same area.
    pil_mask.putalpha(Image.fromarray(255 - np.asarray(authorization), mode="L"))
    buffer = io.BytesIO()
    pil_mask.save(buffer, format="PNG")
    return buffer.getvalue()


def _png_to_tensor(content: bytes) -> torch.Tensor:
    with Image.open(io.BytesIO(content)) as image:
        rgb = image.convert("RGB")
        array = np.asarray(rgb, dtype=np.float32) / 255.0
    return torch.from_numpy(array).unsqueeze(0)


def _image_result(payload: dict) -> tuple[str | None, str | None]:
    data = payload.get("data")
    if isinstance(data, list):
        result = data[0] if data and isinstance(data[0], dict) else None
    elif isinstance(data, dict):
        result = data
    else:
        result = None
    if not result:
        raise RuntimeError("Yunwu image edit response omitted image result data")
    encoded = result.get("b64_json")
    image_url = result.get("url")
    if encoded is not None and not isinstance(encoded, str):
        raise RuntimeError("Yunwu image edit response contained invalid b64_json")
    if image_url is not None and not isinstance(image_url, str):
        raise RuntimeError("Yunwu image edit response contained invalid image URL")
    if not encoded and not image_url:
        raise RuntimeError("Yunwu image edit response omitted b64_json and image URL")
    return encoded, image_url


def _fit_image_to_canvas(
    image: torch.Tensor,
    canvas_width: int,
    canvas_height: int,
) -> tuple[torch.Tensor, tuple[int, int, int, int]]:
    height, width = image.shape[1:3]
    scale = min(canvas_width / width, canvas_height / height)
    fitted_width = max(1, round(width * scale))
    fitted_height = max(1, round(height * scale))
    resized = functional.interpolate(
        image.movedim(-1, 1),
        size=(fitted_height, fitted_width),
        mode="bilinear",
        align_corners=False,
    ).movedim(1, -1)
    left = (canvas_width - fitted_width) // 2
    top = (canvas_height - fitted_height) // 2
    right = canvas_width - fitted_width - left
    bottom = canvas_height - fitted_height - top
    padded = functional.pad(resized.movedim(-1, 1), (left, right, top, bottom), value=1.0).movedim(1, -1)
    return padded, (left, top, fitted_width, fitted_height)


def _fit_mask_to_canvas(
    mask: torch.Tensor,
    canvas_width: int,
    canvas_height: int,
    transform: tuple[int, int, int, int],
) -> torch.Tensor:
    left, top, fitted_width, fitted_height = transform
    value = mask if mask.ndim == 3 else mask.unsqueeze(0)
    resized = functional.interpolate(
        value.unsqueeze(1),
        size=(fitted_height, fitted_width),
        mode="nearest",
    ).squeeze(1)
    right = canvas_width - fitted_width - left
    bottom = canvas_height - fitted_height - top
    return functional.pad(resized, (left, right, top, bottom), value=0.0)


class YunwuMaskedImageEdit:
    """Optional candidate generator. API credentials stay in process environment."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "prompt": ("STRING", {"multiline": True}),
                "model": (
                    "STRING",
                    {"default": os.getenv("PERSONAWEAVE_YUNWU_IMAGE_MODEL") or "gpt-image-1"},
                ),
                "api_mode": (
                    ["json_generations", "multipart_edits"],
                    {"default": os.getenv("PERSONAWEAVE_YUNWU_IMAGE_API_MODE") or "multipart_edits"},
                ),
                "api_path": (
                    "STRING",
                    {"default": os.getenv("PERSONAWEAVE_YUNWU_IMAGE_API_PATH") or "/images/edits"},
                ),
                "output_size": (
                    "STRING",
                    {"default": os.getenv("PERSONAWEAVE_YUNWU_IMAGE_SIZE") or "1024x1024"},
                ),
                "response_format": (
                    ["url", "b64_json"],
                    {"default": os.getenv("PERSONAWEAVE_YUNWU_IMAGE_RESPONSE_FORMAT") or "url"},
                ),
            },
            "optional": {
                "mask": ("MASK",),
                "reference_image": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("candidate",)
    FUNCTION = "edit"
    CATEGORY = "PersonaWeave/API"

    def edit(
        self,
        image: torch.Tensor,
        prompt: str,
        model: str,
        api_mode: str,
        api_path: str,
        output_size: str,
        response_format: str,
        mask: torch.Tensor | None = None,
        reference_image: torch.Tensor | None = None,
    ):
        api_key = os.getenv("PERSONAWEAVE_YUNWU_API_KEY")
        base_url = os.getenv("PERSONAWEAVE_YUNWU_BASE_URL", "https://yunwu.ai/v1").rstrip("/")
        if not api_key:
            raise RuntimeError("PERSONAWEAVE_YUNWU_API_KEY is required for image editing")
        if not prompt.strip():
            raise ValueError("image edit prompt cannot be empty")
        if mask is not None and api_mode != "multipart_edits":
            raise ValueError("masked image editing requires multipart_edits")

        height, width = image.shape[1:3]
        provider_width, provider_height = _provider_canvas(output_size, width, height)
        provider_image, transform = _fit_image_to_canvas(
            image,
            provider_width,
            provider_height,
        )
        provider_mask = (
            _fit_mask_to_canvas(mask, provider_width, provider_height, transform)
            if mask is not None
            else None
        )
        timeout_seconds = _image_api_timeout_seconds()
        timeout = httpx.Timeout(timeout_seconds)
        with httpx.Client(timeout=timeout) as client:
            try:
                if api_mode == "json_generations":
                    image_uris = [_tensor_image_to_data_uri(provider_image)]
                    if reference_image is not None:
                        image_uris.append(_tensor_image_to_data_uri(reference_image))
                    response = client.post(
                        image_api_endpoint(base_url, api_path, api_mode),
                        headers={
                            "Accept": "application/json",
                            "Authorization": f"Bearer {api_key}",
                        },
                        json=json_generation_payload(
                            model=model,
                            prompt=prompt,
                            image_uris=image_uris,
                            size=output_size,
                            response_format=response_format,
                        ),
                    )
                elif api_mode == "multipart_edits":
                    files = [
                        ("image", ("source.png", _tensor_image_to_png(provider_image), "image/png")),
                    ]
                    if reference_image is not None:
                        provider_reference, _ = _fit_image_to_canvas(
                            reference_image,
                            provider_width,
                            provider_height,
                        )
                        files.append(
                            (
                                "image",
                                ("reference.png", _tensor_image_to_png(provider_reference), "image/png"),
                            )
                        )
                    if provider_mask is not None:
                        files.append(
                            (
                                "mask",
                                (
                                    "mask.png",
                                    _tensor_mask_to_png(provider_mask, provider_width, provider_height),
                                    "image/png",
                                ),
                            )
                        )
                    response = client.post(
                        image_api_endpoint(base_url, api_path, api_mode),
                        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
                        data={
                            "model": model,
                            "prompt": prompt,
                            "n": "1",
                            "size": output_size,
                            "response_format": response_format,
                        },
                        files=files,
                    )
                else:
                    raise ValueError(f"unsupported Yunwu image API mode: {api_mode}")
            except httpx.TimeoutException as error:
                raise RuntimeError(_provider_failure_message(timed_out=True)) from error
            except httpx.RequestError as error:
                raise RuntimeError(_provider_failure_message()) from error
            if response.is_error:
                raise RuntimeError(_provider_failure_message(response)) from None
            payload = response.json()
            encoded, image_url = _image_result(payload)
            if encoded:
                try:
                    content = base64.b64decode(encoded, validate=True)
                except ValueError as error:
                    raise RuntimeError("Yunwu image edit response contained invalid base64") from error
            else:
                parsed = urlparse(image_url or "")
                if parsed.scheme != "https" or not parsed.hostname:
                    raise RuntimeError("Yunwu image edit returned an unsafe image URL")
                try:
                    image_response = client.get(image_url)
                except httpx.TimeoutException as error:
                    raise RuntimeError(_provider_failure_message(timed_out=True)) from error
                except httpx.RequestError as error:
                    raise RuntimeError(_provider_failure_message()) from error
                if image_response.is_error:
                    raise RuntimeError(_provider_failure_message(image_response)) from None
                content = image_response.content
        candidate = _png_to_tensor(content)
        if candidate.shape[1:3] != (provider_height, provider_width):
            candidate = functional.interpolate(
                candidate.movedim(-1, 1),
                size=(provider_height, provider_width),
                mode="bilinear",
                align_corners=False,
            ).movedim(1, -1)
        left, top, fitted_width, fitted_height = transform
        candidate = candidate[:, top : top + fitted_height, left : left + fitted_width, :]
        if candidate.shape[1:3] != (height, width):
            candidate = functional.interpolate(
                candidate.movedim(-1, 1),
                size=(height, width),
                mode="bilinear",
                align_corners=False,
            ).movedim(1, -1)
        return (candidate,)

class MaskedOriginalComposite:
    """Restore the original image outside the explicit authorization mask."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "original": ("IMAGE",),
                "candidate": ("IMAGE",),
                "mask": ("MASK",),
                "feather_radius": ("FLOAT", {"default": 4.0, "min": 0.0, "max": 64.0, "step": 0.5}),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("protected_image", "applied_mask")
    FUNCTION = "composite"
    CATEGORY = "PersonaWeave/Safety"

    def composite(
        self,
        original: torch.Tensor,
        candidate: torch.Tensor,
        mask: torch.Tensor,
        feather_radius: float,
    ):
        height, width = original.shape[1:3]
        if candidate.shape[1:3] != (height, width):
            candidate = functional.interpolate(
                candidate.movedim(-1, 1),
                size=(height, width),
                mode="bilinear",
                align_corners=False,
            ).movedim(1, -1)
        raw_mask = mask
        if raw_mask.ndim == 2:
            raw_mask = raw_mask.unsqueeze(0)
        raw_mask = functional.interpolate(
            raw_mask.unsqueeze(1),
            size=(height, width),
            mode="nearest",
        ).squeeze(1).clamp(0, 1)

        feathered = []
        for value in raw_mask:
            pil_mask = Image.fromarray((value.cpu().numpy() * 255).round().astype(np.uint8), mode="L")
            if feather_radius:
                pil_mask = pil_mask.filter(ImageFilter.GaussianBlur(radius=feather_radius))
            softened = torch.from_numpy(np.array(pil_mask, dtype=np.float32, copy=True) / 255.0)
            feathered.append(softened)
        alpha = torch.stack(feathered).to(original.device, original.dtype)
        # The blur may never authorize pixels outside the caller-provided mask.
        alpha = alpha * (raw_mask.to(original.device) > 0).to(original.dtype)
        result = original * (1 - alpha.unsqueeze(-1)) + candidate.to(original.device) * alpha.unsqueeze(-1)
        return (result.clamp(0, 1), alpha)


class NormalizedRegionsToMask:
    """Create provider mask guidance from multimodal normalized face regions."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "regions_json": ("STRING", {"multiline": True}),
            }
        }

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("mask",)
    FUNCTION = "build"
    CATEGORY = "PersonaWeave/Safety"

    def build(self, image: torch.Tensor, regions_json: str):
        try:
            regions = json.loads(regions_json)
        except json.JSONDecodeError as error:
            raise ValueError("regions_json must be valid JSON") from error
        if not isinstance(regions, list) or not regions:
            raise ValueError("regions_json must contain at least one region")
        height, width = image.shape[1:3]
        mask_image = Image.new("L", (width, height), 0)
        drawing = ImageDraw.Draw(mask_image)
        for region in regions:
            if not isinstance(region, dict):
                raise ValueError("each edit region must be an object")
            try:
                x = float(region["x"])
                y = float(region["y"])
                region_width = float(region["width"])
                region_height = float(region["height"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("edit region coordinates are invalid") from error
            if (
                x < 0
                or y < 0
                or region_width <= 0
                or region_height <= 0
                or x + region_width > 1
                or y + region_height > 1
            ):
                raise ValueError("edit region must stay inside normalized source coordinates")
            drawing.ellipse(
                (
                    round(x * width),
                    round(y * height),
                    round((x + region_width) * width),
                    round((y + region_height) * height),
                ),
                fill=255,
            )
        mask = torch.from_numpy(np.asarray(mask_image, dtype=np.float32) / 255.0).unsqueeze(0)
        return (mask,)


class OutsideMaskPixelGuard:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "original": ("IMAGE",),
                "result": ("IMAGE",),
                "mask": ("MASK",),
                "max_channel_delta": ("INT", {"default": 0, "min": 0, "max": 255}),
            }
        }

    RETURN_TYPES = ("BOOLEAN", "STRING")
    RETURN_NAMES = ("passed", "report_json")
    FUNCTION = "check"
    CATEGORY = "PersonaWeave/Safety"

    def check(
        self,
        original: torch.Tensor,
        result: torch.Tensor,
        mask: torch.Tensor,
        max_channel_delta: int,
    ):
        if original.shape != result.shape:
            report = {"passed": False, "reason": "image_shape_mismatch"}
            return (False, json.dumps(report, separators=(",", ":")))
        height, width = original.shape[1:3]
        if mask.ndim == 2:
            mask = mask.unsqueeze(0)
        mask = functional.interpolate(mask.unsqueeze(1), size=(height, width), mode="nearest").squeeze(1)
        protected = mask <= 0
        deltas = ((original - result).abs() * 255).round().to(torch.int16)
        changed = (deltas.amax(dim=-1) > max_channel_delta) & protected.to(deltas.device)
        changed_pixels = int(changed.sum().item())
        checked_pixels = int(protected.sum().item())
        report = {
            "passed": changed_pixels == 0,
            "outside_mask_changed_pixels": changed_pixels,
            "outside_mask_checked_pixels": checked_pixels,
            "max_channel_delta": max_channel_delta,
        }
        return (changed_pixels == 0, json.dumps(report, separators=(",", ":")))


def _tile_starts(length: int, tile_size: int, stride: int) -> list[int]:
    if length <= tile_size:
        return [0]
    starts = list(range(0, length - tile_size + 1, stride))
    final = length - tile_size
    if starts[-1] != final:
        starts.append(final)
    return starts


class OverlappingImageTiles:
    """Split one source image into a fixed-size batch so distant faces remain visible."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "tile_size": ("INT", {"default": 1024, "min": 512, "max": 2048, "step": 64}),
                "overlap": ("FLOAT", {"default": 0.25, "min": 0.0, "max": 0.5, "step": 0.05}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("tiles", "tiles_json")
    FUNCTION = "split"
    CATEGORY = "PersonaWeave/Analysis"

    def split(self, image: torch.Tensor, tile_size: int, overlap: float):
        if image.shape[0] != 1:
            raise ValueError("overlapping tile analysis accepts exactly one source image")
        height, width = image.shape[1:3]
        stride = max(1, round(tile_size * (1.0 - overlap)))
        x_starts = _tile_starts(width, tile_size, stride)
        y_starts = _tile_starts(height, tile_size, stride)
        tiles = []
        metadata = []
        for y in y_starts:
            for x in x_starts:
                valid_width = min(tile_size, width - x)
                valid_height = min(tile_size, height - y)
                crop = image[:, y : y + valid_height, x : x + valid_width, :]
                if valid_width != tile_size or valid_height != tile_size:
                    crop = functional.pad(
                        crop.movedim(-1, 1),
                        (0, tile_size - valid_width, 0, tile_size - valid_height),
                    ).movedim(1, -1)
                tiles.append(crop)
                metadata.append(
                    {
                        "x": x,
                        "y": y,
                        "valid_width": valid_width,
                        "valid_height": valid_height,
                    }
                )
        payload = {
            "source_width": width,
            "source_height": height,
            "tiles": metadata,
        }
        return (torch.cat(tiles, dim=0), json.dumps(payload, separators=(",", ":")))


class RegionHintImageTiles:
    """Build local detector tiles around normalized regions proposed by visual auditing."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "regions_json": ("STRING", {"multiline": True}),
                "tile_size": ("INT", {"default": 768, "min": 512, "max": 1536, "step": 64}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("tiles", "tiles_json")
    FUNCTION = "split"
    CATEGORY = "PersonaWeave/Analysis"

    def split(
        self,
        image: torch.Tensor,
        regions_json: str,
        tile_size: int,
    ):
        if image.shape[0] != 1:
            raise ValueError("region-hint analysis accepts exactly one source image")
        try:
            regions = json.loads(regions_json)
        except json.JSONDecodeError as error:
            raise ValueError("region hints must be valid JSON") from error
        if not isinstance(regions, list) or not regions:
            raise ValueError("region-hint analysis requires at least one normalized bbox")

        height, width = image.shape[1:3]
        starts: set[tuple[int, int]] = set()
        for region in regions:
            if not isinstance(region, dict):
                raise ValueError("each region hint must be an object")
            x = float(region["x"]) * width
            y = float(region["y"]) * height
            region_width = float(region["width"]) * width
            region_height = float(region["height"]) * height
            center_x = x + region_width / 2
            center_y = y + region_height / 2
            left = max(0, min(round(center_x - tile_size / 2), max(0, width - tile_size)))
            top = max(0, min(round(center_y - tile_size / 2), max(0, height - tile_size)))
            starts.add((left, top))

        tiles = []
        metadata = []
        for x, y in sorted(starts, key=lambda item: (item[1], item[0])):
            valid_width = min(tile_size, width - x)
            valid_height = min(tile_size, height - y)
            crop = image[:, y : y + valid_height, x : x + valid_width, :]
            if valid_width != tile_size or valid_height != tile_size:
                crop = functional.pad(
                    crop.movedim(-1, 1),
                    (0, tile_size - valid_width, 0, tile_size - valid_height),
                ).movedim(1, -1)
            tiles.append(crop)
            metadata.append(
                {
                    "x": x,
                    "y": y,
                    "valid_width": valid_width,
                    "valid_height": valid_height,
                }
            )
        payload = {
            "source_width": width,
            "source_height": height,
            "tiles": metadata,
        }
        return (torch.cat(tiles, dim=0), json.dumps(payload, separators=(",", ":")))


def _intersection_over_union(left: dict, right: dict) -> float:
    left_x2 = left["x"] + left["width"]
    left_y2 = left["y"] + left["height"]
    right_x2 = right["x"] + right["width"]
    right_y2 = right["y"] + right["height"]
    intersection_width = max(0.0, min(left_x2, right_x2) - max(left["x"], right["x"]))
    intersection_height = max(0.0, min(left_y2, right_y2) - max(left["y"], right["y"]))
    intersection = intersection_width * intersection_height
    if intersection <= 0:
        return 0.0
    union = left["width"] * left["height"] + right["width"] * right["height"] - intersection
    return intersection / union if union > 0 else 0.0


def _non_maximum_suppression(faces: list[dict], threshold: float = 0.35) -> list[dict]:
    selected = []
    for candidate in sorted(faces, key=lambda face: face["detection_confidence"], reverse=True):
        if all(_intersection_over_union(candidate["bbox"], kept["bbox"]) < threshold for kept in selected):
            selected.append(candidate)
    return selected


def _ordered_rings(edges) -> list[list[int]]:
    adjacency: dict[int, set[int]] = {}
    for left, right in edges:
        adjacency.setdefault(int(left), set()).add(int(right))
        adjacency.setdefault(int(right), set()).add(int(left))
    visited: set[int] = set()
    rings: list[list[int]] = []
    for start in adjacency:
        if start in visited:
            continue
        ring = [start]
        visited.add(start)
        previous, current = -1, start
        while True:
            next_vertex = next(
                (value for value in adjacency[current] if value != previous),
                None,
            )
            if next_vertex is None or next_vertex == start:
                break
            ring.append(next_vertex)
            visited.add(next_vertex)
            previous, current = current, next_vertex
        rings.append(ring)
    return rings


def _landmark_faces_on_source(face_landmarks, tiles_json: str) -> list[dict]:
    tile_metadata = json.loads(tiles_json)
    tiles = tile_metadata.get("tiles", [])
    source_width = int(tile_metadata["source_width"])
    source_height = int(tile_metadata["source_height"])
    frames = face_landmarks.get("frames", []) if isinstance(face_landmarks, dict) else []
    candidates = []
    for frame_index, frame in enumerate(frames):
        tile = tiles[frame_index] if frame_index < len(tiles) else None
        if not tile:
            continue
        offset_x = int(tile.get("x", 0))
        offset_y = int(tile.get("y", 0))
        valid_width = float(tile["valid_width"])
        valid_height = float(tile["valid_height"])
        for face in frame if isinstance(frame, list) else []:
            bbox = np.asarray(face["bbox_xyxy"], dtype=np.float32)
            center_x = float((bbox[0] + bbox[2]) / 2)
            center_y = float((bbox[1] + bbox[3]) / 2)
            if center_x > valid_width or center_y > valid_height:
                continue
            x1 = max(0.0, float(bbox[0]) + offset_x)
            y1 = max(0.0, float(bbox[1]) + offset_y)
            x2 = min(float(source_width), float(bbox[2]) + offset_x)
            y2 = min(float(source_height), float(bbox[3]) + offset_y)
            landmarks = np.asarray(face["landmarks_xy"], dtype=np.float32).copy()
            landmarks[:, 0] = np.clip(landmarks[:, 0] + offset_x, 0, source_width - 1)
            landmarks[:, 1] = np.clip(landmarks[:, 1] + offset_y, 0, source_height - 1)
            candidates.append(
                {
                    "bbox": {
                        "x": round(x1),
                        "y": round(y1),
                        "width": max(1, round(x2 - x1)),
                        "height": max(1, round(y2 - y1)),
                    },
                    "detection_confidence": max(0.0, min(1.0, float(face.get("score", 0.0)))),
                    "_landmarks": landmarks,
                }
            )
    selected = _non_maximum_suppression(candidates)
    for index, face in enumerate(selected):
        face["external_face_id"] = f"face-{index}"
    return selected


def _square_crop(bbox: dict, width: int, height: int, context_scale: float) -> dict:
    center_x = bbox["x"] + bbox["width"] / 2
    center_y = bbox["y"] + bbox["height"] / 2
    side = min(width, height, max(bbox["width"], bbox["height"]) * context_scale)
    left = max(0, min(round(center_x - side / 2), round(width - side)))
    top = max(0, min(round(center_y - side / 2), round(height - side)))
    side = max(1, round(side))
    return {"x": left, "y": top, "width": side, "height": side}


class FaceLandmarksToCropPlan:
    """Build high-resolution per-face crops and exact face-oval masks."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "face_landmarks": ("FACE_LANDMARKS",),
                "tiles_json": ("STRING", {"forceInput": True}),
                "crop_size": ("INT", {"default": 768, "min": 512, "max": 1536, "step": 64}),
                "context_scale": (
                    "FLOAT",
                    {"default": 2.2, "min": 1.4, "max": 4.0, "step": 0.1},
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("face_crops", "face_masks", "mask_preview", "source_mask", "report_json")
    FUNCTION = "build"
    CATEGORY = "PersonaWeave/Analysis"

    def build(
        self,
        image: torch.Tensor,
        face_landmarks,
        tiles_json: str,
        crop_size: int,
        context_scale: float,
    ):
        if image.shape[0] != 1:
            raise ValueError("face crop planning accepts exactly one source image")
        height, width = image.shape[1:3]
        faces = _landmark_faces_on_source(face_landmarks, tiles_json)
        if not faces:
            raise ValueError("no face landmarks were detected")
        connection_sets = face_landmarks.get("connection_sets", {})
        face_oval = connection_sets.get("face_oval")
        if not face_oval:
            raise ValueError("MediaPipe face landmarks omitted the face_oval topology")
        rings = _ordered_rings(face_oval)

        full_mask_image = Image.new("L", (width, height), 0)
        full_drawing = ImageDraw.Draw(full_mask_image)
        crops = []
        crop_masks = []
        previews = []
        report_faces = []
        for face in faces:
            landmarks = face["_landmarks"]
            face_mask_image = Image.new("L", (width, height), 0)
            drawing = ImageDraw.Draw(face_mask_image)
            for ring in rings:
                polygon = [(float(landmarks[index, 0]), float(landmarks[index, 1])) for index in ring]
                drawing.polygon(polygon, fill=255)
                full_drawing.polygon(polygon, fill=255)
            crop = _square_crop(face["bbox"], width, height, context_scale)
            left, top = crop["x"], crop["y"]
            right, bottom = left + crop["width"], top + crop["height"]
            image_crop = image[:, top:bottom, left:right, :]
            image_crop = functional.interpolate(
                image_crop.movedim(-1, 1),
                size=(crop_size, crop_size),
                mode="bilinear",
                align_corners=False,
            ).movedim(1, -1)
            mask_crop_pil = face_mask_image.crop((left, top, right, bottom)).resize(
                (crop_size, crop_size),
                Image.Resampling.NEAREST,
            )
            mask_crop = torch.from_numpy(
                np.array(mask_crop_pil, dtype=np.float32, copy=True) / 255.0
            ).unsqueeze(0)
            color = torch.tensor(
                [0.06, 0.72, 0.48],
                dtype=image_crop.dtype,
                device=image_crop.device,
            ).view(1, 1, 1, 3)
            alpha = mask_crop.unsqueeze(-1).to(image_crop.dtype) * 0.42
            preview = image_crop * (1 - alpha) + color * alpha
            crops.append(image_crop)
            crop_masks.append(mask_crop)
            previews.append(preview)
            face_pixels = round(max(face["bbox"]["width"], face["bbox"]["height"]) * crop_size / crop["width"])
            report_faces.append(
                {
                    "external_face_id": face["external_face_id"],
                    "bbox": face["bbox"],
                    "crop": crop,
                    "detection_confidence": face["detection_confidence"],
                    "cloud_face_long_edge_pixels": face_pixels,
                }
            )

        crop_batch = torch.cat(crops, dim=0)
        mask_batch = torch.cat(crop_masks, dim=0)
        preview_batch = torch.cat(previews, dim=0)
        columns = min(2, len(previews))
        rows = (len(previews) + columns - 1) // columns
        sheet = torch.full(
            (1, rows * crop_size, columns * crop_size, 3),
            0.94,
            dtype=preview_batch.dtype,
        )
        for index, preview in enumerate(preview_batch):
            row, column = divmod(index, columns)
            sheet[:, row * crop_size : (row + 1) * crop_size, column * crop_size : (column + 1) * crop_size, :] = preview
        full_mask = torch.from_numpy(
            np.array(full_mask_image, dtype=np.float32, copy=True) / 255.0
        ).unsqueeze(0)
        report = {
            "detected_face_count": len(report_faces),
            "crop_size": crop_size,
            "context_scale": context_scale,
            "mask_type": "mediapipe_face_oval",
            "faces": report_faces,
        }
        return (
            crop_batch,
            mask_batch,
            sheet.clamp(0, 1),
            full_mask,
            json.dumps(report, separators=(",", ":")),
        )


def _faces_from_bboxes(bboxes, tiles_json: str | None = None) -> list[dict]:
    frames = bboxes if isinstance(bboxes, list) else []
    tile_metadata = json.loads(tiles_json) if tiles_json else None
    tiles = tile_metadata.get("tiles", []) if isinstance(tile_metadata, dict) else []
    source_width = tile_metadata.get("source_width") if isinstance(tile_metadata, dict) else None
    source_height = tile_metadata.get("source_height") if isinstance(tile_metadata, dict) else None
    faces = []
    for frame_index, frame in enumerate(frames):
        for face_index, bbox in enumerate(frame if isinstance(frame, list) else []):
            tile = tiles[frame_index] if frame_index < len(tiles) else None
            offset_x = int(tile.get("x", 0)) if tile else 0
            offset_y = int(tile.get("y", 0)) if tile else 0
            x = float(bbox["x"])
            y = float(bbox["y"])
            width = float(bbox["width"])
            height = float(bbox["height"])
            if tile and (
                x + width / 2 > float(tile["valid_width"])
                or y + height / 2 > float(tile["valid_height"])
            ):
                continue
            global_x = max(0, round(x + offset_x))
            global_y = max(0, round(y + offset_y))
            global_width = max(1, round(width))
            global_height = max(1, round(height))
            if source_width is not None:
                global_width = min(global_width, max(1, int(source_width) - global_x))
            if source_height is not None:
                global_height = min(global_height, max(1, int(source_height) - global_y))
            faces.append(
                {
                    "external_face_id": f"frame-{frame_index}-face-{face_index}",
                    "bbox": {
                        "x": global_x,
                        "y": global_y,
                        "width": global_width,
                        "height": global_height,
                    },
                    "detection_confidence": max(
                        0.0, min(1.0, float(bbox.get("score", 0.0)))
                    ),
                    "suggested_person_key": None,
                    "identity_confidence": None,
                    "needs_confirmation": True,
                }
            )
    faces = _non_maximum_suppression(faces)
    for index, face in enumerate(faces):
        face["external_face_id"] = f"face-{index}"
    return faces


class BoundingBoxesToFaceMask:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "bboxes": ("BOUNDING_BOX",),
                "tiles_json": ("STRING", {"forceInput": True}),
                "expand_ratio": (
                    "FLOAT",
                    {"default": 0.18, "min": 0.0, "max": 0.5, "step": 0.01},
                ),
            }
        }

    RETURN_TYPES = ("MASK", "STRING")
    RETURN_NAMES = ("face_mask", "report_json")
    FUNCTION = "build"
    CATEGORY = "PersonaWeave/Analysis"

    def build(self, image: torch.Tensor, bboxes, tiles_json: str, expand_ratio: float):
        if image.shape[0] != 1:
            raise ValueError("face mask builder accepts exactly one source image")
        height, width = image.shape[1:3]
        faces = _faces_from_bboxes(bboxes, tiles_json)
        if not faces:
            raise ValueError("no faces were detected; refusing to call the image editor")
        mask_image = Image.new("L", (width, height), 0)
        drawing = ImageDraw.Draw(mask_image)
        for face in faces:
            bbox = face["bbox"]
            expand_x = round(bbox["width"] * expand_ratio)
            expand_y = round(bbox["height"] * expand_ratio)
            left = max(0, bbox["x"] - expand_x)
            top = max(0, bbox["y"] - expand_y)
            right = min(width - 1, bbox["x"] + bbox["width"] + expand_x)
            bottom = min(height - 1, bbox["y"] + bbox["height"] + expand_y)
            drawing.ellipse((left, top, right, bottom), fill=255)
        mask = torch.from_numpy(np.asarray(mask_image, dtype=np.float32) / 255.0).unsqueeze(0)
        report = {
            "detected_face_count": len(faces),
            "faces": faces,
            "mask_expand_ratio": expand_ratio,
        }
        return (mask, json.dumps(report, separators=(",", ":")))


class FaceAnalysisJson:
    """Convert ComfyUI BOUNDING_BOX output into PersonaWeave's analysis contract."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bboxes": ("BOUNDING_BOX",),
                "request_json": ("STRING", {"multiline": True}),
            },
            "optional": {"tiles_json": ("STRING", {"forceInput": True})},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("analysis_json",)
    FUNCTION = "serialize"
    CATEGORY = "PersonaWeave/Analysis"

    def serialize(self, bboxes, request_json: str, tiles_json: str | None = None):
        request = json.loads(request_json)
        faces = _faces_from_bboxes(bboxes, tiles_json)
        return (json.dumps({"asset_id": request.get("asset_id"), "faces": faces}, separators=(",", ":")),)


class MultiScaleFaceAnalysisJson:
    """Merge detector output from two image scales into one stable source-coordinate list."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "primary_bboxes": ("BOUNDING_BOX",),
                "primary_tiles_json": ("STRING", {"forceInput": True}),
                "secondary_bboxes": ("BOUNDING_BOX",),
                "secondary_tiles_json": ("STRING", {"forceInput": True}),
                "request_json": ("STRING", {"multiline": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("analysis_json",)
    FUNCTION = "serialize"
    CATEGORY = "PersonaWeave/Analysis"

    def serialize(
        self,
        primary_bboxes,
        primary_tiles_json: str,
        secondary_bboxes,
        secondary_tiles_json: str,
        request_json: str,
    ):
        request = json.loads(request_json)
        faces = _non_maximum_suppression([
            *_faces_from_bboxes(primary_bboxes, primary_tiles_json),
            *_faces_from_bboxes(secondary_bboxes, secondary_tiles_json),
        ])
        for index, face in enumerate(faces):
            face["external_face_id"] = f"face-{index}"
        return (
            json.dumps(
                {"asset_id": request.get("asset_id"), "faces": faces},
                separators=(",", ":"),
            ),
        )


class JsonOutput:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"text": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ()
    FUNCTION = "emit"
    CATEGORY = "PersonaWeave/Output"
    OUTPUT_NODE = True

    def emit(self, text: str):
        return {"ui": {"text": [text]}}


NODE_CLASS_MAPPINGS = {
    "PersonaWeaveYunwuImageEdit": YunwuMaskedImageEdit,
    "PersonaWeaveYunwuMaskedImageEdit": YunwuMaskedImageEdit,
    "PersonaWeaveMaskedOriginalComposite": MaskedOriginalComposite,
    "PersonaWeaveNormalizedRegionsToMask": NormalizedRegionsToMask,
    "PersonaWeaveOutsideMaskPixelGuard": OutsideMaskPixelGuard,
    "PersonaWeaveOverlappingImageTiles": OverlappingImageTiles,
    "PersonaWeaveRegionHintImageTiles": RegionHintImageTiles,
    "PersonaWeaveBoundingBoxesToFaceMask": BoundingBoxesToFaceMask,
    "PersonaWeaveFaceLandmarksToCropPlan": FaceLandmarksToCropPlan,
    "PersonaWeaveFaceAnalysisJson": FaceAnalysisJson,
    "PersonaWeaveMultiScaleFaceAnalysisJson": MultiScaleFaceAnalysisJson,
    "PersonaWeaveJsonOutput": JsonOutput,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PersonaWeaveYunwuImageEdit": "PersonaWeave Yunwu Image Edit",
    "PersonaWeaveYunwuMaskedImageEdit": "PersonaWeave Yunwu Masked Image Edit",
    "PersonaWeaveMaskedOriginalComposite": "PersonaWeave Restore Outside Mask",
    "PersonaWeaveNormalizedRegionsToMask": "PersonaWeave Model Regions to Mask",
    "PersonaWeaveOutsideMaskPixelGuard": "PersonaWeave Outside-Mask Pixel Guard",
    "PersonaWeaveOverlappingImageTiles": "PersonaWeave Overlapping Image Tiles",
    "PersonaWeaveRegionHintImageTiles": "PersonaWeave Region Hint Image Tiles",
    "PersonaWeaveBoundingBoxesToFaceMask": "PersonaWeave Bounding Boxes to Face Mask",
    "PersonaWeaveFaceLandmarksToCropPlan": "PersonaWeave Face Landmarks to Crop Plan",
    "PersonaWeaveFaceAnalysisJson": "PersonaWeave Face Analysis JSON",
    "PersonaWeaveMultiScaleFaceAnalysisJson": "PersonaWeave Multi-Scale Face Analysis JSON",
    "PersonaWeaveJsonOutput": "PersonaWeave JSON Output",
}
