import hashlib
from pathlib import Path

from PIL import Image

from personaweave.assets import AssetResolver
from personaweave.domain import ValidationRequest, ValidationSubject
from personaweave.storage import Repository
from personaweave.validation import compare_source_result


def save_image(path: Path, color: str) -> None:
    Image.new("RGB", (100, 100), color).save(path, format="PNG")


def validation_request(source: Path, result: Path) -> ValidationRequest:
    return ValidationRequest(
        batch_id="batch",
        result_asset_id="result",
        source_uri=source.resolve().as_uri(),
        result_uri=result.resolve().as_uri(),
        expected_face_instance_ids=["face"],
        protected_face_instance_ids=[],
        expected_subjects=[
            ValidationSubject(
                face_instance_id="face",
                source_person_code="P01",
                target_identity_code="T01",
                bbox={"x": 35, "y": 35, "width": 30, "height": 30},
            )
        ],
    )


def formal_validation_request(
    source: Path,
    result: Path,
    mask: Path,
) -> ValidationRequest:
    request = validation_request(source, result)
    raw = mask.read_bytes()
    return request.model_copy(update={
        "authorization_mask_uri": mask.resolve().as_uri(),
        "authorization_mask_sha256": hashlib.sha256(raw).hexdigest(),
        "authorization_mask_width": 100,
        "authorization_mask_height": 100,
        "preserve_outside_mask": True,
    })


def test_source_comparison_accepts_changes_only_inside_requested_region(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    result = tmp_path / "result.png"
    save_image(source, "white")
    save_image(result, "white")
    with Image.open(result) as image:
        edited = image.convert("RGB")
    for x in range(40, 60):
        for y in range(40, 60):
            edited.putpixel((x, y), (0, 0, 0))
    edited.save(result)
    repository = Repository(tmp_path / "validation.sqlite3")
    repository.initialize()

    review = compare_source_result(
        validation_request(source, result),
        AssetResolver(tmp_path),
        repository,
    )

    assert review.passed is True
    assert review.checks["outside_requested_regions_preserved"] is True
    assert review.checks["global_tone_preserved"] is True


def test_source_comparison_rejects_broad_original_changes(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    result = tmp_path / "result.png"
    save_image(source, "white")
    save_image(result, "black")
    repository = Repository(tmp_path / "validation.sqlite3")
    repository.initialize()

    review = compare_source_result(
        validation_request(source, result),
        AssetResolver(tmp_path),
        repository,
    )

    assert review.passed is False
    assert review.checks["outside_requested_regions_preserved"] is False
    assert review.checks["global_tone_preserved"] is False


def test_source_comparison_fails_closed_when_image_is_unavailable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "missing-source.png"
    result = tmp_path / "missing-result.png"
    repository = Repository(tmp_path / "validation.sqlite3")
    repository.initialize()

    review = compare_source_result(
        validation_request(source, result),
        AssetResolver(tmp_path),
        repository,
    )

    assert review.passed is False
    assert review.checks == {"comparison.available": False}
    assert review.reasons


def test_source_comparison_rejects_result_without_edit_change(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    result = tmp_path / "result.png"
    save_image(source, "white")
    save_image(result, "white")
    repository = Repository(tmp_path / "validation.sqlite3")
    repository.initialize()

    review = compare_source_result(
        validation_request(source, result),
        AssetResolver(tmp_path),
        repository,
    )

    assert review.passed is False
    assert review.checks["editable_region_changed"] is False
    assert any("未检测到足够" in reason for reason in review.reasons)


def test_formal_comparison_requires_exact_outside_mask_preservation(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    result = tmp_path / "result.png"
    mask_path = tmp_path / "mask.png"
    save_image(source, "white")
    save_image(result, "white")
    Image.new("L", (100, 100), 0).save(mask_path)

    with Image.open(result) as image:
        edited = image.convert("RGB")
    for x in range(40, 60):
        for y in range(40, 60):
            edited.putpixel((x, y), (0, 0, 0))
    edited.putpixel((0, 0), (254, 255, 255))
    edited.save(result)
    with Image.open(mask_path) as image:
        mask = image.convert("L")
    for x in range(40, 60):
        for y in range(40, 60):
            mask.putpixel((x, y), 255)
    mask.save(mask_path)

    repository = Repository(tmp_path / "validation.sqlite3")
    repository.initialize()
    review = compare_source_result(
        formal_validation_request(source, result, mask_path),
        AssetResolver(tmp_path),
        repository,
    )

    assert review.passed is False
    assert review.checks["authorization_mask.available"] is True
    assert review.checks["authorization_mask.outside_pixels_unchanged"] is False
    assert review.checks["authorization_mask.outside_change_ratio"] > 0
    assert any("授权蒙版外检测到像素变化" in reason for reason in review.reasons)


def test_formal_comparison_accepts_exact_masked_edit(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    result = tmp_path / "result.png"
    mask_path = tmp_path / "mask.png"
    save_image(source, "white")
    save_image(result, "white")
    Image.new("L", (100, 100), 0).save(mask_path)

    with Image.open(result) as image:
        edited = image.convert("RGB")
    with Image.open(mask_path) as image:
        mask = image.convert("L")
    for x in range(40, 60):
        for y in range(40, 60):
            edited.putpixel((x, y), (0, 0, 0))
            mask.putpixel((x, y), 255)
    edited.save(result)
    mask.save(mask_path)

    repository = Repository(tmp_path / "validation.sqlite3")
    repository.initialize()
    review = compare_source_result(
        formal_validation_request(source, result, mask_path),
        AssetResolver(tmp_path),
        repository,
    )

    assert review.passed is True
    assert review.checks["authorization_mask.outside_pixels_unchanged"] is True
    assert review.checks["editable_region_changed"] is True
