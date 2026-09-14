"""Portrait source resolution and swap-reference preparation."""

from api.media import TrustedImagePath, load_image_from_source
from api.portraits.library import resolve_asset_ids, resolve_identity_asset_ids
from core.face_metrics import calculate_reference_quality, calculate_yaw_ratio
from core.pipeline import PersonaWeavePipeline


def resolve_source_locations(
    source_identity_id: str | None,
    source_asset_ids: list[str],
    source_manual_images: list[str],
) -> list[str]:
    """Resolves a person's configured source into concrete loadable locations.

    Managed portrait-library identity/asset IDs resolve to on-disk asset
    paths via SQLite; manual images pass through untouched (URL/base64/local
    path), since only managed library sources are restricted to opaque IDs.
    """
    locations: list[str] = []
    if source_identity_id:
        asset_ids = resolve_identity_asset_ids(source_identity_id)
        if asset_ids is None:
            raise ValueError(f"Unknown portrait library identity id: {source_identity_id}")
        locations.extend(TrustedImagePath(path) for path in resolve_asset_ids(asset_ids))
    if source_asset_ids:
        locations.extend(TrustedImagePath(path) for path in resolve_asset_ids(source_asset_ids))
    locations.extend(source_manual_images)
    return locations


def build_source_references(pipeline: PersonaWeavePipeline, source_locations: list[str]) -> list[dict]:
    """Loads each source location, extracts its face embedding, and packages
    the reference metadata consumed by the swap pipeline."""
    refs = []
    for location in source_locations:
        img = load_image_from_source(location)
        faces = pipeline.detector.detect_faces(img, max_num=1, confidence_threshold=0.25)
        if not faces:
            continue
        emb = pipeline.recognizer.extract_embedding(img, faces[0])
        refs.append(
            {
                "path": location,
                "image": img,
                "face": faces[0],
                "embedding": emb,
                "yaw": calculate_yaw_ratio(faces[0].kps),
                "quality_score": calculate_reference_quality(img, faces[0]),
            }
        )
    return refs
