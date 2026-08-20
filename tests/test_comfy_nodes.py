import hashlib
import sys
from io import BytesIO
from pathlib import Path

import pytest
np = pytest.importorskip("numpy")
from PIL import Image


torch = pytest.importorskip("torch")
sys.path.insert(0, str(Path(__file__).parents[1] / "comfy-nodes"))
from PersonaWeave.nodes import (  # noqa: E402
    MaskedOriginalComposite,
    _fit_mask_to_canvas,
    _tensor_mask_to_png,
)


def test_provider_mask_is_deterministic_fitted_inversion_of_canonical_pixels() -> None:
    canonical = Image.new("L", (2, 4), 0)
    canonical.putpixel((0, 0), 255)
    canonical.putpixel((1, 1), 255)
    canonical.putpixel((0, 3), 255)
    canonical_buffer = BytesIO()
    canonical.save(canonical_buffer, format="PNG")
    canonical_content = canonical_buffer.getvalue()
    canonical_digest = hashlib.sha256(canonical_content).hexdigest()

    with Image.open(BytesIO(canonical_content)) as decoded:
        assert decoded.mode == "L"
        assert hashlib.sha256(canonical_content).hexdigest() == canonical_digest
        canonical_tensor = torch.from_numpy(
            np.array(decoded, dtype=np.float32, copy=True) / 255.0
        ).unsqueeze(0)

    fitted = _fit_mask_to_canvas(
        canonical_tensor,
        canvas_width=6,
        canvas_height=6,
        transform=(1, 0, 3, 6),
    )
    provider_content = _tensor_mask_to_png(fitted, width=6, height=6)

    expected_authorization = torch.nn.functional.interpolate(
        canonical_tensor.unsqueeze(1),
        size=(6, 3),
        mode="nearest",
    ).squeeze(1)
    expected_authorization = torch.nn.functional.pad(
        expected_authorization,
        (1, 2, 0, 0),
        value=0.0,
    )
    with Image.open(BytesIO(provider_content)) as provider_mask:
        assert provider_mask.mode == "RGBA"
        assert provider_mask.size == (6, 6)
        assert np.array_equal(
            np.asarray(provider_mask.getchannel("A")),
            255
            - (
                expected_authorization[0].numpy() * 255
            ).round().astype(np.uint8),
        )
        assert provider_mask.getpixel((0, 0))[3] == 255
        assert provider_mask.getpixel((1, 0))[3] == 0
        assert provider_mask.getpixel((4, 0))[3] == 255


def test_provider_mask_serializer_rejects_an_unfitted_canvas() -> None:
    with pytest.raises(ValueError, match="already match"):
        _tensor_mask_to_png(torch.zeros((1, 2, 2)), width=3, height=2)


def test_masked_composite_feathers_inward_without_authorizing_outside_pixels() -> None:
    original = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
    candidate = torch.ones((1, 64, 64, 3), dtype=torch.float32)
    mask = torch.zeros((1, 64, 64), dtype=torch.float32)
    mask[:, 12:52, 12:52] = 1.0

    result, alpha = MaskedOriginalComposite().composite(
        original,
        candidate,
        mask,
        feather_radius=6.0,
    )

    assert torch.count_nonzero(result[:, :12, :, :]) == 0
    assert torch.count_nonzero(result[:, 52:, :, :]) == 0
    assert alpha[0, 12, 32] < 0.1
    assert alpha[0, 32, 32] > 0.95
    assert torch.equal(result[0, :, :, 0], alpha[0])
