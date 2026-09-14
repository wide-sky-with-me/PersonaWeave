import warnings

import pytest
from pydantic import ValidationError

from api.schemas import BatchTaskCreateRequest, PersonMappingItem, SwapParameters, SyncSwapRequest

MAPPING = {
    "person_index": 1,
    "target_reference_url": "https://example.com/target.png",
    "target_reference_person_index": 1,
    "source_asset_ids": ["asset-a"],
}


def test_person_mapping_rejects_whole_identity_with_explicit_subset():
    with pytest.raises(ValidationError, match="whole identity"):
        PersonMappingItem(**MAPPING, source_identity_id="identity-a")


def test_sync_mapping_rejects_legacy_fields_instead_of_ignoring_them():
    with pytest.raises(ValidationError, match="legacy source or target"):
        SyncSwapRequest(
            target_image_url="https://example.com/image.png",
            person_mappings=[MAPPING],
            source_asset_ids=["ignored-download"],
        )


def test_batch_mapping_rejects_legacy_target_fields():
    with pytest.raises(ValidationError, match="legacy source or target"):
        BatchTaskCreateRequest(
            batch_images=[{"client_target_id": "target-1", "image_url": "https://example.com/image.png"}],
            person_mappings=[MAPPING],
            target_reference_url="https://example.com/ignored.png",
        )


def test_explicit_mapping_subset_is_preserved_exactly():
    mapping = SyncSwapRequest(
        target_image_url="https://example.com/image.png", person_mappings=[MAPPING]
    ).person_mappings[0]
    assert mapping.source_identity_id is None
    assert mapping.source_asset_ids == ["asset-a"]


def test_deprecated_batch_urls_normalize_without_internal_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        request = BatchTaskCreateRequest(batch_image_urls=["https://example.com/image.png"])
        assert request.total_images == 1
        assert request.normalized_batch_images[0].client_target_id.startswith("legacy-")

    assert caught == []


def test_direct_deprecated_batch_urls_access_warns_callers():
    request = BatchTaskCreateRequest(batch_image_urls=["https://example.com/image.png"])

    with pytest.warns(DeprecationWarning, match="deprecated"):
        assert request.batch_image_urls == ["https://example.com/image.png"]


def test_identity_blend_strength_defaults_to_current_production_value_and_is_bounded():
    assert SwapParameters().identity_blend_strength == 0.9
    assert SwapParameters(identity_blend_strength=0.9).identity_blend_strength == 0.9
    with pytest.raises(ValidationError):
        SwapParameters(identity_blend_strength=0.96)
