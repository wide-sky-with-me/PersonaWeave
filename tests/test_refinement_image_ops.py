"""Pure image fidelity, normalization and configured sizing remain valid after worker retirement."""

import asyncio
import base64
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from core.ai.configuration import ImageEditConfig
from core.ai.gateway import AIGateway
from core.image_security import MAX_IMAGE_BYTES
from core.refinement.image_ops import decode_image, encode_image, generation_parameters, normalize_candidate


def canvas(height=32, width=32, value=127):
    return np.full((height, width, 3), value, dtype=np.uint8)


def test_lossless_encoding_never_downscales_selected_generation_canvas():
    image = np.arange(61 * 97 * 3, dtype=np.uint8).reshape(61, 97, 3)
    encoded = encode_image(image, max_dim=8, lossless=True)
    assert encoded.startswith("data:image/png;base64,")
    restored = decode_image(base64.b64decode(encoded.split(",", 1)[1]))
    np.testing.assert_array_equal(restored, image)


def test_diagnostic_preview_downscales_without_changing_aspect_ratio():
    encoded = encode_image(canvas(100, 200), max_dim=50)
    assert encoded.startswith("data:image/jpeg;base64,")
    restored = decode_image(base64.b64decode(encoded.split(",", 1)[1]))
    assert restored.shape == (25, 50, 3)


def test_encoding_failure_is_not_reported_as_image(monkeypatch):
    monkeypatch.setattr(cv2, "imencode", lambda *args: (False, None))
    with pytest.raises(ValueError, match="encode"):
        encode_image(canvas())


def test_invalid_encoded_images_are_rejected():
    with pytest.raises(ValueError, match="decoded"):
        decode_image(b"not-an-image")


@pytest.mark.parametrize(
    ("shape", "operation"),
    [((32, 64), "center-crop-width"), ((64, 32), "center-crop-height"), ((64, 64), "resize-only")],
)
def test_provider_canvas_is_normalized_without_local_pixel_compositing(shape, operation):
    candidate = canvas(*shape, value=73)
    normalized, audit = normalize_candidate(candidate, (24, 24, 3))
    np.testing.assert_array_equal(normalized, canvas(24, 24, value=73))
    assert audit == {
        "provider_output_size": [shape[1], shape[0]],
        "target_canvas_size": [24, 24],
        "canvas_normalization": operation,
    }


def test_normalization_crops_only_the_excess_edges():
    candidate = canvas(20, 40, value=100)
    candidate[:, :10] = 0
    candidate[:, 30:] = 255
    normalized, _ = normalize_candidate(candidate, (20, 20, 3))
    np.testing.assert_array_equal(normalized, canvas(20, 20, value=100))


def test_generation_parameters_follow_declared_limits():
    gateway = SimpleNamespace(
        default_image_alias="auto",
        image_edits={
            "auto": SimpleNamespace(generation={"size": "auto"}),
            "fixed": SimpleNamespace(generation={"sizes": ["1024x1024", "1536x1024", "1024x1536"]}),
            "dynamic": SimpleNamespace(
                generation={"max_side": 2848, "max_pixels": 2048 * 2048, "min_side": 512, "grid": 8, "delimiter": "x"}
            ),
            "small": SimpleNamespace(
                generation={"max_side": 2048, "max_pixels": 2048 * 2048, "min_side": 512, "grid": 8, "delimiter": "x"}
            ),
        },
    )
    assert generation_parameters(gateway, None, (3616, 5424, 3)) == ({"size": "auto"}, None)
    assert generation_parameters(gateway, "fixed", (3616, 5424, 3)) == ({"size": "1536x1024"}, [1536, 1024])
    assert generation_parameters(gateway, "fixed", (5424, 3616, 3)) == ({"size": "1024x1536"}, [1024, 1536])
    assert generation_parameters(gateway, "small", (3616, 5424, 3)) == ({"size": "2048x1360"}, [2048, 1360])
    assert generation_parameters(gateway, "dynamic", (3616, 5424, 3)) == ({"size": "2504x1672"}, [2504, 1672])
    with pytest.raises(ValueError, match="Unknown image-edit"):
        generation_parameters(gateway, "retired-model", (64, 64, 3))


def test_impossible_canvas_does_not_silently_exceed_pixel_limit():
    gateway = SimpleNamespace(
        default_image_alias="limited",
        image_edits={
            "limited": SimpleNamespace(
                generation={"max_side": 2048, "min_side": 512, "grid": 8, "max_pixels": 512 * 512}
            )
        },
    )
    with pytest.raises(ValueError, match="generation limits"):
        generation_parameters(gateway, None, (100, 5000, 3))


def test_image_edit_rejects_invalid_and_oversized_outputs():
    gateway = AIGateway.__new__(AIGateway)
    gateway.image_edits = {"alias": ImageEditConfig(model_name="alias", generation={"size": "auto"})}
    gateway.default_image_alias = "alias"

    async def non_image(**kwargs):
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(b"not-image").decode(), url=None)])

    gateway._router = SimpleNamespace(aimage_edit=non_image)
    with pytest.raises(ValueError, match="decoded"):
        asyncio.run(gateway.edit_image([encode_image(canvas(), lossless=True)], "repair"))

    async def oversized(**kwargs):
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode(), url=None)]
        )

    gateway._router = SimpleNamespace(aimage_edit=oversized)
    with pytest.raises(ValueError, match="encoded byte limit"):
        asyncio.run(gateway.edit_image([encode_image(canvas(), lossless=True)], "repair"))
