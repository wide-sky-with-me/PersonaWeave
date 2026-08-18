import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "comfy-nodes"
    / "PersonaWeave"
    / "yunwu_image_api.py"
)
SPEC = importlib.util.spec_from_file_location("yunwu_image_api", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_json_generation_endpoint_drops_openai_v1_path_for_api_route() -> None:
    assert MODULE.image_api_endpoint(
        "https://yunwu.ai/v1", "/api/v3/images/generations", "json_generations"
    ) == "https://yunwu.ai/api/v3/images/generations"


def test_multipart_endpoint_keeps_openai_v1_base() -> None:
    assert MODULE.image_api_endpoint(
        "https://yunwu.ai/v1", "/images/edits", "multipart_edits"
    ) == "https://yunwu.ai/v1/images/edits"


def test_json_generation_payload_uses_single_or_multiple_image_contract() -> None:
    single = MODULE.json_generation_payload(
        model="doubao-seedream-5-0-pro-260628",
        prompt="edit the source",
        image_uris=["data:image/png;base64,AAAA"],
        size="2K",
    )
    assert single["image"] == "data:image/png;base64,AAAA"
    assert single["size"] == "2K"
    assert single["response_format"] == "url"
    assert single["watermark"] is False
    assert set(single) == {
        "model",
        "prompt",
        "size",
        "response_format",
        "watermark",
        "image",
    }

    multiple = MODULE.json_generation_payload(
        model="doubao-seedream-5-0-pro-260628",
        prompt="use the second image as identity reference",
        image_uris=["https://example.com/source.png", "https://example.com/ref.png"],
        size="2K",
    )
    assert multiple["image"] == [
        "https://example.com/source.png",
        "https://example.com/ref.png",
    ]


def test_json_generation_payload_rejects_more_than_ten_images() -> None:
    with pytest.raises(ValueError, match="at most 10"):
        MODULE.json_generation_payload(
            model="doubao-seedream-5-0-pro-260628",
            prompt="too many references",
            image_uris=[f"https://example.com/{index}.png" for index in range(11)],
            size="2K",
        )
