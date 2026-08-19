import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from personaweave.domain import (
    AttributePermission,
    BoundingBox,
    CanonicalMaskEvidence,
    CompiledBatchContract,
    CompiledFaceEvidence,
    CompiledIdentityEvidence,
    CompiledOperation,
    MappingAction,
    ReferenceAssetEvidence,
    ReferenceSufficiency,
    ReviewReason,
    SemanticMaskContour,
    SemanticMaskProposal,
    SourceAssetEvidence,
    SourceOccurrenceEvidence,
    SourceOccurrenceReferenceAssessment,
    SourceOperationReferenceCoverage,
    TargetIdentityProfile,
)
from personaweave.formal import (
    ConfirmedBatchSnapshot,
    FormalFaceSnapshot,
    FormalIdentitySnapshot,
    FormalMappingSnapshot,
    FormalPersonSnapshot,
    FormalReferenceSnapshot,
    FormalSourceSnapshot,
    FormalSwapRequestFactory,
    FormalSwapRequestSnapshot,
    GenerationProfile,
)
from personaweave.formal_masks import (
    FormalMaskFaceScope,
    validate_and_rasterize_semantic_mask,
)


PERMISSIONS = AttributePermission(
    editable=["face_identity"],
    protected=[
        "apparent_age",
        "face_features",
        "hair",
        "eyewear",
        "accessories",
        "makeup",
    ],
)


def point(x: float, y: float) -> dict[str, float]:
    return {"x": x, "y": y}


def contour(
    points: list[dict[str, float]],
    *,
    operation: str = "include",
    face_id: str = "face-1",
    confidence: float = 0.95,
) -> SemanticMaskContour:
    return SemanticMaskContour.model_validate({
        "operation": operation,
        "face_instance_id": face_id,
        "person_id": "person-1",
        "person_code": "P01",
        "permissions": PERMISSIONS.model_dump(mode="json"),
        "points": points,
        "confidence": confidence,
    })


def proposal(contours: list[SemanticMaskContour], confidence: float = 0.95) -> SemanticMaskProposal:
    return SemanticMaskProposal(
        source_asset_id="source-1",
        source_asset_code="I01",
        contours=contours,
        confidence=confidence,
    )


def source_image(tmp_path: Path) -> Path:
    path = tmp_path / "source.png"
    Image.new("RGB", (100, 100), "white").save(path)
    return path


def raster(
    tmp_path: Path,
    value: SemanticMaskProposal,
    protected: list[BoundingBox] | None = None,
    *,
    source_path: Path | None = None,
):
    return validate_and_rasterize_semantic_mask(
        source_path=source_path or source_image(tmp_path),
        proposal=value,
        source_asset_id="source-1",
        source_asset_code="I01",
        editable_faces=[FormalMaskFaceScope(
            face_instance_id="face-1",
            person_id="person-1",
            person_code="P01",
            bbox=BoundingBox(x=20, y=20, width=30, height=30),
            permissions=PERMISSIONS,
        )],
        protected_faces=protected or [],
    )


def test_arbitrary_contours_and_exclusion_holes_are_canonical_l_png(tmp_path: Path) -> None:
    value = proposal([
        contour([point(.15, .15), point(.55, .18), point(.50, .55), point(.18, .50)]),
        contour(
            [point(.18, .18), point(.23, .18), point(.23, .23), point(.18, .23)],
            operation="exclude",
        ),
    ])

    first = raster(tmp_path, value)
    second = raster(tmp_path, value)

    assert first.sha256 == second.sha256
    assert first.png_bytes == second.png_bytes
    with Image.open(BytesIO(first.png_bytes)) as mask:
        assert mask.mode == "L"
        assert mask.size == (100, 100)
        assert mask.getpixel((20, 20)) == 0
        assert mask.getpixel((35, 35)) == 255
        assert mask.getpixel((52, 40)) == 0
    assert first.review_reasons == frozenset()


def test_protected_feature_hole_may_exclude_face_center(tmp_path: Path) -> None:
    artifact = raster(
        tmp_path,
        proposal([
            contour([point(.15, .15), point(.55, .15), point(.55, .55), point(.15, .55)]),
            contour(
                [point(.30, .30), point(.40, .30), point(.40, .40), point(.30, .40)],
                operation="exclude",
            ),
        ]),
    )

    with Image.open(BytesIO(artifact.png_bytes)) as mask:
        assert mask.getpixel((35, 35)) == 0
        assert mask.getpixel((25, 25)) == 255


def test_include_contour_must_cover_editable_face_center(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="include contour.*face center"):
        raster(
            tmp_path,
            proposal([
                contour([
                    point(.20, .20),
                    point(.33, .20),
                    point(.33, .50),
                    point(.20, .50),
                ])
            ]),
        )


@pytest.mark.parametrize(
    ("source_kind", "message"),
    [
        ("exif-orientation", "non-identity EXIF orientation"),
        ("alpha", "alpha channel"),
        ("palette-transparency", "contain transparency"),
    ],
)
def test_formal_mask_source_raster_semantics_fail_closed(
    tmp_path: Path,
    source_kind: str,
    message: str,
) -> None:
    source_path = tmp_path / (
        "oriented.jpg" if source_kind == "exif-orientation" else f"{source_kind}.png"
    )
    if source_kind == "exif-orientation":
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (100, 100), "white").save(
            source_path,
            format="JPEG",
            exif=exif,
        )
    elif source_kind == "alpha":
        Image.new("RGBA", (100, 100), "white").save(source_path, format="PNG")
    else:
        transparent = Image.new("P", (100, 100), 0)
        transparent.info["transparency"] = 0
        transparent.save(source_path, format="PNG")

    with pytest.raises(ValueError, match=message):
        raster(
            tmp_path,
            proposal([
                contour([
                    point(.2, .2),
                    point(.5, .2),
                    point(.5, .5),
                    point(.2, .5),
                ])
            ]),
            source_path=source_path,
        )


@pytest.mark.parametrize(("image_format", "suffix"), [("PNG", ".png"), ("JPEG", ".jpg")])
def test_opaque_rgb_sources_remain_supported(
    tmp_path: Path,
    image_format: str,
    suffix: str,
) -> None:
    source_path = tmp_path / f"opaque{suffix}"
    Image.new("RGB", (100, 100), "white").save(source_path, format=image_format)

    artifact = raster(
        tmp_path,
        proposal([
            contour([
                point(.2, .2),
                point(.5, .2),
                point(.5, .5),
                point(.2, .5),
            ])
        ]),
        source_path=source_path,
    )

    assert (artifact.width, artifact.height) == (100, 100)


@pytest.mark.parametrize(
    "points, message",
    [
        ([point(.1, .1), point(.6, .5), point(.1, .6), point(.5, .1)], "self-intersecting"),
        ([point(0, 0), point(.9, 0), point(.9, .9), point(0, .9)], "oversized"),
    ],
)
def test_unsafe_polygon_topology_and_area_fail_closed(
    tmp_path: Path,
    points: list[dict[str, float]],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        raster(tmp_path, proposal([contour(points)]))


def test_out_of_bounds_points_are_rejected_by_bounded_model() -> None:
    with pytest.raises(ValidationError):
        contour([point(-.1, .1), point(.2, .1), point(.2, .2)])


def test_mapping_expansion_and_protected_neighbor_pixels_fail_closed(tmp_path: Path) -> None:
    expanded = proposal([
        contour(
            [point(.2, .2), point(.5, .2), point(.5, .5), point(.2, .5)],
            face_id="unknown-face",
        )
    ])
    with pytest.raises(ValueError, match="confirmed face mappings"):
        raster(tmp_path, expanded)

    overlapping = proposal([
        contour([point(.2, .2), point(.7, .2), point(.7, .5), point(.2, .5)])
    ])
    with pytest.raises(ValueError, match="protected-face pixels"):
        raster(
            tmp_path,
            overlapping,
            protected=[BoundingBox(x=55, y=25, width=10, height=10)],
        )



def test_safe_low_confidence_mask_adds_only_mask_uncertain(tmp_path: Path) -> None:
    artifact = raster(
        tmp_path,
        proposal([
            contour(
                [point(.2, .2), point(.5, .2), point(.5, .5), point(.2, .5)],
                confidence=.7,
            )
        ], confidence=.8),
    )
    assert artifact.review_reasons == frozenset({ReviewReason.MASK_UNCERTAIN})


def mask_evidence(source_id: str, source_code: str, reason: ReviewReason | None) -> CanonicalMaskEvidence:
    reasons = frozenset({reason}) if reason else frozenset()
    return CanonicalMaskEvidence(
        source_asset_id=source_id,
        source_asset_code=source_code,
        width=100,
        height=100,
        sha256=("1" if reason else "2") * 64,
        proposal=SemanticMaskProposal(
            source_asset_id=source_id,
            source_asset_code=source_code,
            contours=[],
            confidence=.8 if reason else .95,
        ),
        review_reasons=reasons,
    )


def coverage(source_id: str, source_code: str, face_id: str, sufficient: bool) -> SourceOperationReferenceCoverage:
    occurrence = SourceOccurrenceEvidence(
        face_instance_id=face_id,
        bbox=BoundingBox(x=20, y=20, width=30, height=30),
        face_area_pixels=900,
    )
    assessment = SourceOccurrenceReferenceAssessment(
        face_instance_id=face_id,
        sufficient=sufficient,
        requires_manual_review=not sufficient,
    )
    return SourceOperationReferenceCoverage(
        source_asset_id=source_id,
        source_asset_code=source_code,
        person_id="person-1",
        person_code="P01",
        occurrences=[occurrence],
        occurrence_assessments=[assessment],
        sufficient=sufficient,
    )


def test_source_varying_reference_and_mask_reasons_do_not_leak_between_requests() -> None:
    board = [{"code": "R01", "sha256": "a" * 64}]
    board_digest = hashlib.sha256(
        json.dumps(board, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    profile = TargetIdentityProfile(
        identity_id="identity-1",
        identity_code="T01",
        reference_board_codes=["R01"],
        reference_board_digest=board_digest,
    )
    reference_assessment = ReferenceSufficiency(
        identity_id="identity-1",
        identity_code="T01",
        reference_count=1,
        sufficient=True,
        requires_manual_review=False,
    )
    reference_asset = ReferenceAssetEvidence(code="R01", sha256="a" * 64)
    operation = CompiledOperation(
        person_id="person-1",
        person_code="P01",
        action=MappingAction.REPLACE,
        target_identity_id="identity-1",
        target_identity_code="T01",
        permissions=PERMISSIONS,
        target_profile=profile,
        reference_assessment=reference_assessment,
        reference_assets=[reference_asset],
        source_reference_coverage=[
            coverage("source-1", "I01", "face-1", True),
            coverage("source-2", "I02", "face-2", False),
        ],
    )
    contract = CompiledBatchContract(
        task_instruction="replace @P01 with @T01",
        max_iterations=1,
        operations=[operation],
        identities=[CompiledIdentityEvidence(
            profile=profile,
            reference_assessment=reference_assessment,
            reference_assets=[reference_asset],
        )],
        source_assets=[
            SourceAssetEvidence(
                asset_id="source-1",
                code="I01",
                sha256="b" * 64,
                faces=[
                    CompiledFaceEvidence(
                        face_instance_id="face-1",
                        person_id="person-1",
                        person_code="P01",
                        bbox=BoundingBox(x=20, y=20, width=30, height=30),
                        source_appearance={},
                    )
                ],
                editable_face_instance_ids=["face-1"],
                authorization_mask=mask_evidence("source-1", "I01", None),
            ),
            SourceAssetEvidence(
                asset_id="source-2",
                code="I02",
                sha256="c" * 64,
                faces=[
                    CompiledFaceEvidence(
                        face_instance_id="face-2",
                        person_id="person-1",
                        person_code="P01",
                        bbox=BoundingBox(x=20, y=20, width=30, height=30),
                        source_appearance={},
                    )
                ],
                editable_face_instance_ids=["face-2"],
                authorization_mask=mask_evidence(
                    "source-2", "I02", ReviewReason.MASK_UNCERTAIN
                ),
            ),
        ],
    )
    contract_hash = contract.sha256()
    canonical = json.loads(contract.canonical_json())
    assert "review_reasons" not in canonical
    assert "requires_manual_review" not in canonical
    assert "requires_manual_review" not in canonical["operations"][0]
    assert "requires_authorization_mask" not in canonical["source_assets"][0]
    assert (
        "requires_manual_review"
        not in canonical["source_assets"][0]["authorization_mask"]
    )
    assert CompiledBatchContract.model_validate_json(
        contract.canonical_json()
    ) == contract
    masks_by_source = {
        source.asset_id: source.authorization_mask
        for source in contract.source_assets
    }
    sources = tuple(
        FormalSourceSnapshot(
            id=source_id,
            code=source_code,
            storage_uri=f"file:///{source_id}.png",
            media_type="image/png",
            sha256=digest,
            persisted_authorization_mask_uri=f"file:///masks/{source_id}.png",
            persisted_authorization_mask_sha256=masks_by_source[source_id].sha256,
            persisted_authorization_mask_width=masks_by_source[source_id].width,
            persisted_authorization_mask_height=masks_by_source[source_id].height,
            faces=(FormalFaceSnapshot(
                id=face_id,
                person_id="person-1",
                bbox=BoundingBox(x=20, y=20, width=30, height=30),
                appearance={},
            ),),
        )
        for source_id, source_code, digest, face_id in (
            ("source-1", "I01", "b" * 64, "face-1"),
            ("source-2", "I02", "c" * 64, "face-2"),
        )
    )
    snapshot = dict(
        batch=ConfirmedBatchSnapshot(
            id="batch-1",
            task_instruction=contract.task_instruction,
            max_iterations=1,
            contract=contract,
            contract_hash=contract_hash,
            confirmed_at="now",
        ),
        people=(FormalPersonSnapshot(id="person-1", code="P01"),),
        identities=(FormalIdentitySnapshot(
            id="identity-1",
            code="T01",
            references=(FormalReferenceSnapshot(
                code="R01",
                storage_uri="file:///reference.png",
                media_type="image/png",
                sha256="a" * 64,
            ),),
        ),
        ),
        sources=sources,
        mappings=(FormalMappingSnapshot(
            person_id="person-1",
            action=MappingAction.REPLACE,
            target_identity_id="identity-1",
            instruction="",
        ),),
    )
    factory = FormalSwapRequestFactory()
    profile_config = GenerationProfile(
        image_model="image",
        image_api_mode="multipart_edits",
        image_api_path="/images/edits",
        image_output_size="1024x1024",
        image_response_format="url",
    )

    safe = factory.create(
        FormalSwapRequestSnapshot(source_id="source-1", **snapshot),
        profile_config,
    )
    uncertain = factory.create(
        FormalSwapRequestSnapshot(source_id="source-2", **snapshot),
        profile_config,
    )

    assert safe.review_reasons == frozenset()
    assert safe.replacements[0].review_reasons == frozenset()
    assert uncertain.review_reasons == frozenset({
        ReviewReason.REFERENCE_INSUFFICIENCY,
        ReviewReason.MASK_UNCERTAIN,
    })
    assert uncertain.replacements[0].review_reasons == uncertain.review_reasons
    assert operation.review_reasons == frozenset()
    safe_digest = hashlib.sha256(
        safe.model_dump_json().encode()
    ).hexdigest()
    changed_mask_digest = hashlib.sha256(
        safe.model_copy(
            update={"authorization_mask_sha256": "f" * 64}
        ).model_dump_json().encode()
    ).hexdigest()
    assert changed_mask_digest != safe_digest
