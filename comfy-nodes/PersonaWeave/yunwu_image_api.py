from urllib.parse import urlparse


IMAGE_API_MODES = {"json_generations", "multipart_edits"}


def image_api_endpoint(base_url: str, path: str, api_mode: str) -> str:
    """Resolve an explicitly configured image API path without inspecting model names."""

    if api_mode not in IMAGE_API_MODES:
        raise ValueError(f"unsupported Yunwu image API mode: {api_mode}")
    if path.startswith(("http://", "https://")):
        parsed_path = urlparse(path)
        if not parsed_path.netloc:
            raise ValueError("Yunwu image API URL is invalid")
        return path.rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("PERSONAWEAVE_YUNWU_BASE_URL must be an absolute HTTP(S) URL")
    normalized_path = f"/{path.lstrip('/')}"
    if api_mode == "json_generations" and normalized_path.startswith("/api/"):
        return f"{parsed.scheme}://{parsed.netloc}{normalized_path}"
    return f"{base_url.rstrip('/')}{normalized_path}"


def json_generation_payload(
    *,
    model: str,
    prompt: str,
    image_uris: list[str],
    size: str,
    response_format: str = "url",
) -> dict:
    if not image_uris:
        raise ValueError("JSON image editing requires the source image")
    if len(image_uris) > 10:
        raise ValueError("JSON image editing accepts at most 10 source/reference images")
    return {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": response_format,
        "watermark": False,
        "image": image_uris[0] if len(image_uris) == 1 else image_uris,
    }


def multipart_edit_data(
    *,
    model: str,
    prompt: str,
    size: str,
    response_format: str | None = None,
) -> dict[str, str]:
    data = {"model": model, "prompt": prompt, "n": "1", "size": size}
    if response_format:
        data["response_format"] = response_format
    return data
