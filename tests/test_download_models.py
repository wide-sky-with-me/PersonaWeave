import io
import zipfile

import pytest

from scripts.dev import REQUIRED_MODELS
from scripts.download_models import MODEL_SHA256, MODEL_URLS, is_valid_model, safe_extract_zip


def _archive(member: str) -> zipfile.ZipFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, b"model")
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def test_safe_extract_zip_extracts_member_inside_destination(tmp_path):
    with _archive("buffalo_l/det_10g.onnx") as archive:
        safe_extract_zip(archive, str(tmp_path))

    assert (tmp_path / "buffalo_l" / "det_10g.onnx").read_bytes() == b"model"


@pytest.mark.parametrize("member", ["../escape.onnx", "/tmp/escape.onnx"])
def test_safe_extract_zip_rejects_path_traversal(tmp_path, member):
    with _archive(member) as archive, pytest.raises(ValueError, match="Unsafe ZIP member path"):
        safe_extract_zip(archive, str(tmp_path))

    assert not (tmp_path.parent / "escape.onnx").exists()


def test_gfpgan_face_restorer_is_not_a_runtime_asset():
    """GFPGAN has no identity/fidelity control, unlike CodeFormer (core/restorer.py),
    so it was not part of the masked, opt-in restoration reintroduction and stays retired."""
    retired = {"gfpgan_1.4.onnx"}

    assert retired.isdisjoint(MODEL_URLS)
    assert all(not any(name in path for name in retired) for path in REQUIRED_MODELS)


def test_codeformer_restorer_is_optional_not_startup_required():
    """CodeFormer (core/restorer.py) is a production capability reachable via the
    `enable_face_restoration` API parameter, but it stays opt-in and off by default
    (see core/pipeline.py), so it must not become a hard startup requirement like the
    detector/recognizer/inswapper models in REQUIRED_MODELS."""
    assert not any("codeformer.onnx" in path for path in REQUIRED_MODELS)


def test_every_download_has_a_pinned_digest():
    assert set(MODEL_URLS) <= set(MODEL_SHA256)


def test_model_validation_rejects_digest_mismatch(tmp_path):
    candidate = tmp_path / "model.onnx"
    candidate.write_bytes(b"x" * 2048)

    assert is_valid_model(str(candidate))
    assert not is_valid_model(str(candidate), "0" * 64)
