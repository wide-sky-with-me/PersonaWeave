import asyncio
import base64
import hashlib
import json
import sqlite3
from io import BytesIO
from pathlib import Path
from threading import Event, Thread, current_thread

import pytest
from PIL import Image

from personaweave.assets import AssetResolver
from personaweave.domain import (
    AttributePermission,
    BatchStatus,
    BoundingBox,
    CanonicalMaskEvidence,
    CompiledBatchContract,
    CompiledFaceEvidence,
    CompiledIdentityEvidence,
    CompiledOperation,
    HEAD_ATTRIBUTES,
    JobStage,
    JobStatus,
    MappingAction,
    MappingDecision,
    ReferenceAssetEvidence,
    ReviewReason,
    SemanticMaskContour,
    SemanticMaskProposal,
    ReferenceSufficiency,
    SourceAssetEvidence,
    TargetIdentityProfile,
    SourceOccurrenceReferenceAssessment,
    SourceOperationReferenceCoverage,
    SwapOperation,
    SwapRequest,
    SwapResult,
    ValidationResult,
    ValidationReviewerVerdict,
)
from personaweave.formal_masks import (
    CompiledMaskArtifact,
    FormalCompilationResult,
    FormalMaskFaceScope,
    validate_and_rasterize_semantic_mask,
)
from personaweave.storage import Repository
from personaweave.workflow import (
    InvalidTransition,
    WorkflowService,
    validate_swap_request_scope,
)


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    repo = Repository(tmp_path / "test.sqlite3")
    repo.initialize()
    return repo


def add_source(repository: Repository, batch_id: str, name: str = "group.jpg") -> dict:
    source_path = repository.database_path.parent / name
    Image.new("RGB", (100, 100), "white").save(source_path, format="JPEG")
    return repository.add_asset(
        batch_id=batch_id,
        kind="source",
        filename=name,
        media_type="image/jpeg",
        storage_uri=source_path.resolve().as_uri(),
        sha256="a" * 64,
    )


def semantic_mask_proposal(
    *,
    source_asset_id: str,
    source_asset_code: str,
    source_size: tuple[int, int],
    faces: list[dict[str, object]],
) -> SemanticMaskProposal:
    width, height = source_size
    contours = []
    for face in faces:
        if face["action"] == MappingAction.KEEP.value:
            continue
        bbox = BoundingBox.model_validate(face["bbox"])
        horizontal_span = bbox.width - 1
        vertical_span = bbox.height - 1
        left = (bbox.x + horizontal_span * 0.25) / max(1, width - 1)
        right = (bbox.x + horizontal_span * 0.75) / max(1, width - 1)
        top = (bbox.y + vertical_span * 0.25) / max(1, height - 1)
        bottom = (bbox.y + vertical_span * 0.75) / max(1, height - 1)
        contours.append(
            SemanticMaskContour(
                operation="include",
                face_instance_id=str(face["face_instance_id"]),
                person_id=str(face["person_id"]),
                person_code=str(face["person_code"]),
                permissions=AttributePermission(
                    editable=face["editable_attributes"],
                    protected=face["protected_attributes"],
                ),
                points=[
                    {"x": left, "y": top},
                    {"x": right, "y": top},
                    {"x": right, "y": bottom},
                    {"x": left, "y": bottom},
                ],
                confidence=1,
                evidence="Deterministic editable-face test contour.",
            )
        )
    return SemanticMaskProposal(
        source_asset_id=source_asset_id,
        source_asset_code=source_asset_code,
        contours=contours,
        confidence=1,
        summary="Deterministic semantic-mask test proposal.",
    )


class DeterministicSemanticMaskCompiler:
    async def compile_semantic_mask_proposal(
        self,
        *,
        source_asset_id: str,
        source_asset_code: str,
        source_uri: str,
        faces: list[dict[str, object]],
        protected_people: list[dict[str, str]],
        identity_evidence: list[dict[str, object]],
    ) -> SemanticMaskProposal:
        assert source_uri.startswith("data:image/jpeg;base64,")
        assert isinstance(protected_people, list)
        assert isinstance(identity_evidence, list)
        encoded = source_uri.split(",", 1)[1]
        with Image.open(BytesIO(base64.b64decode(encoded))) as source:
            source_size = source.size
        for face in faces:
            bbox = face["bbox"]
            assert face["normalized_bbox"] == pytest.approx({
                "x": bbox["x"] / source_size[0],
                "y": bbox["y"] / source_size[1],
                "width": bbox["width"] / source_size[0],
                "height": bbox["height"] / source_size[1],
            })
            assert face["source_dimensions"] == {
                "width": source_size[0],
                "height": source_size[1],
            }
        return semantic_mask_proposal(
            source_asset_id=source_asset_id,
            source_asset_code=source_asset_code,
            source_size=source_size,
            faces=faces,
        )


class CoordinatedSemanticMaskCompiler(DeterministicSemanticMaskCompiler):
    def __init__(self):
        self.calls: list[str] = []
        self.ready = asyncio.Event()

    async def compile_semantic_mask_proposal(self, **kwargs) -> SemanticMaskProposal:
        self.calls.append(kwargs["source_asset_code"])
        if len(self.calls) == 2:
            self.ready.set()
        await asyncio.wait_for(self.ready.wait(), timeout=1)
        return await super().compile_semantic_mask_proposal(**kwargs)


def formal_compilation(
    repository: Repository,
    batch_id: str,
    contract: CompiledBatchContract,
) -> FormalCompilationResult:
    operations = {operation.person_id: operation for operation in contract.operations}
    compiled_sources = []
    artifacts = []
    for source in contract.source_assets:
        source_record = repository.get_asset(source.asset_id)
        source_path = Path(source_record["storage_uri"].removeprefix("file://"))
        faces = repository.list_faces_for_asset(source.asset_id)
        compiled_faces = [
            CompiledFaceEvidence(
                face_instance_id=face["id"],
                person_id=face["person_id"],
                person_code=operations[face["person_id"]].person_code,
                bbox=BoundingBox.model_validate(face["bbox"]),
                source_appearance=face.get("appearance") or {},
            )
            for face in faces
        ]
        editable_faces = [
            face
            for face in faces
            if (
                (operation := operations.get(face["person_id"])) is not None
                and operation.action is not MappingAction.KEEP
            )
        ]
        if not editable_faces:
            compiled_sources.append(
                source.model_copy(update={"faces": compiled_faces})
            )
            continue
        face_payloads = []
        scopes = []
        protected = []
        for face in faces:
            operation = operations.get(face["person_id"])
            bbox = BoundingBox.model_validate(face["bbox"])
            if operation is None or operation.action is MappingAction.KEEP:
                protected.append(bbox)
                continue
            payload = {
                "face_instance_id": face["id"],
                "person_id": face["person_id"],
                "person_code": operation.person_code,
                "bbox": face["bbox"],
                "action": operation.action.value,
                "editable_attributes": list(operation.permissions.editable),
                "protected_attributes": list(operation.permissions.protected),
            }
            face_payloads.append(payload)
            scopes.append(
                FormalMaskFaceScope(
                    face_instance_id=face["id"],
                    person_id=face["person_id"],
                    person_code=operation.person_code,
                    bbox=bbox,
                    permissions=operation.permissions,
                )
            )
        with Image.open(source_path) as source_image:
            source_size = source_image.size
        proposal = semantic_mask_proposal(
            source_asset_id=source.asset_id,
            source_asset_code=source.code,
            source_size=source_size,
            faces=face_payloads,
        )
        rasterized = validate_and_rasterize_semantic_mask(
            source_path=source_path,
            proposal=proposal,
            source_asset_id=source.asset_id,
            source_asset_code=source.code,
            editable_faces=scopes,
            protected_faces=protected,
        )
        evidence = CanonicalMaskEvidence(
            source_asset_id=source.asset_id,
            source_asset_code=source.code,
            width=rasterized.width,
            height=rasterized.height,
            sha256=rasterized.sha256,
            proposal=rasterized.proposal,
            review_reasons=rasterized.review_reasons,
        )
        compiled_sources.append(
            SourceAssetEvidence(
                asset_id=source.asset_id,
                code=source.code,
                sha256=source.sha256,
                faces=compiled_faces,
                editable_face_instance_ids=[face["id"] for face in editable_faces],
                authorization_mask=evidence,
            )
        )
        artifacts.append(
            CompiledMaskArtifact(evidence=evidence, png_bytes=rasterized.png_bytes)
        )


    return FormalCompilationResult(
        contract=contract.model_copy(update={"source_assets": compiled_sources}),
        masks=tuple(artifacts),
    )
def prepared_edit_compilation(
    repository: Repository,
    source_face_presence: tuple[bool, ...],
) -> tuple[dict, list[dict], FormalCompilationResult]:
    batch = repository.create_batch("formal masks", "修改 @P01 的脸", 2)
    sources = [
        add_source(repository, batch["id"], f"source-{index}.jpg")
        for index in range(1, len(source_face_presence) + 1)
    ]
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [
            {
                "asset_id": source["id"],
                "provider": "deterministic",
                "faces": (
                    [{
                        "external_face_id": f"face-{index}",
                        "bbox": {"x": 20, "y": 20, "width": 40, "height": 40},
                        "detection_confidence": 1,
                        "suggested_person_key": "person-1",
                        "needs_confirmation": False,
                    }]
                    if has_face
                    else []
                ),
            }
            for index, (source, has_face) in enumerate(
                zip(sources, source_face_presence, strict=True),
                start=1,
            )
        ],
    )
    person = repository.list_persons(batch["id"])[0]
    contract = CompiledBatchContract(
        task_instruction=batch["task_instruction"],
        max_iterations=batch["max_iterations"],
        operations=[
            CompiledOperation(
                person_id=person["id"],
                person_code=person["display_code"],
                action=MappingAction.EDIT,
                permissions=AttributePermission(
                    editable=["face_features"],
                    protected=sorted(HEAD_ATTRIBUTES - {"face_features"}),
                ),
            )
        ],
        source_assets=[
            SourceAssetEvidence(
                asset_id=source["id"],
                code=source["display_code"],
                sha256=source["sha256"],
                faces=[],
            )
            for source in sources
        ],
    )
    return batch, sources, formal_compilation(repository, batch["id"], contract)


def test_mixed_sources_persist_masks_and_jobs_only_for_editable_sources(
    repository: Repository,
) -> None:
    batch, sources, compilation = prepared_edit_compilation(
        repository,
        (True, False),
    )
    contract = compilation.contract
    repository.store_compiled_contract(batch["id"], compilation)
    repository.confirm_compiled_contract(batch["id"], contract.sha256())

    masks = repository.list_formal_masks(batch["id"])
    jobs = WorkflowService(repository).start_swap(batch["id"])

    assert [mask["source_asset_id"] for mask in masks] == [sources[0]["id"]]
    assert [job["item_key"] for job in jobs] == [sources[0]["id"]]
    assert contract.source_assets[0].requires_authorization_mask is True
    assert contract.source_assets[1].requires_authorization_mask is False


def test_uncertain_mask_requires_exact_digest_acknowledgement(
    repository: Repository,
) -> None:
    batch, _, compilation = prepared_edit_compilation(repository, (True,))
    artifact = compilation.masks[0]
    uncertain_evidence = artifact.evidence.model_copy(
        update={
            "review_reasons": frozenset({ReviewReason.MASK_UNCERTAIN}),
        }
    )
    source = compilation.contract.source_assets[0].model_copy(
        update={"authorization_mask": uncertain_evidence}
    )
    contract = compilation.contract.model_copy(update={"source_assets": [source]})
    uncertain = FormalCompilationResult(
        contract=contract,
        masks=(
            CompiledMaskArtifact(
                evidence=uncertain_evidence,
                png_bytes=artifact.png_bytes,
            ),
        ),
    )
    repository.store_compiled_contract(batch["id"], uncertain)

    with pytest.raises(ValueError, match="uncertainty acknowledgement"):
        repository.confirm_compiled_contract(batch["id"], contract.sha256())
    confirmed = repository.confirm_compiled_contract(
        batch["id"],
        contract.sha256(),
        acknowledged_mask_sha256s=[uncertain_evidence.sha256],
    )

    assert confirmed["status"] == BatchStatus.READY
    assert repository.list_formal_masks(batch["id"])[0]["status"] == "confirmed"


def test_contract_invalidation_removes_committed_mask_after_database_commit(
    repository: Repository,
) -> None:
    batch, _, compilation = prepared_edit_compilation(repository, (True,))
    repository.store_compiled_contract(batch["id"], compilation)
    mask_path = Path(
        repository.list_formal_masks(batch["id"])[0]["storage_uri"].removeprefix(
            "file://"
        )
    )
    assert mask_path.is_file()

    repository.update_batch_spec(batch["id"], "修改 @P01 的发型", 2)

    assert repository.list_formal_masks(batch["id"]) == []
    assert not mask_path.exists()


def test_permanent_batch_delete_removes_committed_mask(
    repository: Repository,
) -> None:
    batch, _, compilation = prepared_edit_compilation(repository, (True,))
    repository.store_compiled_contract(batch["id"], compilation)
    mask_path = Path(
        repository.list_formal_masks(batch["id"])[0]["storage_uri"].removeprefix(
            "file://"
        )
    )
    repository.trash_batch(batch["id"])

    repository.delete_batch(batch["id"])

    assert not mask_path.exists()
    with pytest.raises(KeyError):
        repository.get_batch(batch["id"], include_deleted=True)


class OccurrenceCoverageCompiler:
    def __init__(self, *, fail_coverage: bool = False):
        self.fail_coverage = fail_coverage

    async def compile_target_identity(
        self,
        *,
        identity_id: str,
        identity_code: str,
        references: list[dict[str, str]],
    ):
        assert [item["code"] for item in references] == ["R01"]
        return (
            TargetIdentityProfile(
                identity_id=identity_id,
                identity_code=identity_code,
            ),
            ReferenceSufficiency(
                identity_id=identity_id,
                identity_code=identity_code,
                reference_count=1,
                visible_views=["frontal"],
                sufficient=True,
                requires_manual_review=False,
                summary="Only a frontal reference view is available.",
            ),
        )

    async def compile_attribute_permissions(
        self,
        *,
        task_instruction: str,
        operations: list[dict[str, str | None]],
    ):
        return {
            str(operation["person_code"]): AttributePermission(
                editable=["face_identity"],
                protected=sorted(HEAD_ATTRIBUTES - {"face_identity"}),
            )
            for operation in operations
        }

    async def compile_source_reference_coverage(
        self,
        *,
        source_asset_id: str,
        source_asset_code: str,
        source_uri: str,
        person_id: str,
        person_code: str,
        occurrences: list,
        target_profile: TargetIdentityProfile,
        reference_assessment: ReferenceSufficiency,
        references: list[dict[str, str]],
    ):
        if self.fail_coverage:
            raise RuntimeError("coverage compiler unavailable")
        assert target_profile.identity_id
        assert reference_assessment.visible_views == ["frontal"]
        assert references[0]["code"] == "R01"
        assessments = [
            SourceOccurrenceReferenceAssessment(
                face_instance_id=occurrence.face_instance_id,
                missing_coverage=["left profile reference"],
                sufficient=False,
                requires_manual_review=True,
                summary="The left-profile source pose is absent from the frontal reference.",
            )
            for occurrence in occurrences
        ]
        return SourceOperationReferenceCoverage(
            source_asset_id=source_asset_id,
            source_asset_code=source_asset_code,
            person_id=person_id,
            person_code=person_code,
            occurrences=occurrences,
            occurrence_assessments=assessments,
            sufficient=False,
            requires_manual_review=True,
            summary="The selected target references do not cover the source pose.",
        )


async def compile_reference_occurrence_case(
    repository: Repository,
    compiler: OccurrenceCoverageCompiler,
) -> dict:
    batch = repository.create_batch("coverage", "将 @P01 替换为 @T01")
    source = add_source(repository, batch["id"])
    identity = repository.create_identity(batch["id"], "target")
    repository.add_asset(
        batch_id=batch["id"],
        identity_id=identity["id"],
        kind="reference",
        filename="frontal.jpg",
        media_type="image/jpeg",
        storage_uri=source["storage_uri"],
        sha256="b" * 64,
    )
    service = WorkflowService(
        repository,
        contract_compiler=compiler,
        semantic_mask_compiler=DeterministicSemanticMaskCompiler(),
        asset_resolver=AssetResolver(repository.database_path.parent),
    )
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [{
            "asset_id": source["id"],
            "provider": "fake",
            "faces": [{
                "external_face_id": "face-1",
                "bbox": {"x": 1, "y": 2, "width": 64, "height": 64},
                "detection_confidence": 1,
                "suggested_person_key": "person-1",
                "appearance": {
                    "pose": "left profile",
                    "lighting_occlusion": "clear light, no occlusion or blur",
                },
            }],
        }],
    )
    person = repository.list_persons(batch["id"])[0]
    compiled = await service.compile_task_contract(
        batch["id"],
        [
            MappingDecision(
                person_id=person["id"],
                action=MappingAction.REPLACE,
                target_identity_id=identity["id"],
            )
        ],
    )
    return compiled["compiled_contract"]["operations"][0]["source_reference_coverage"][0]


@pytest.mark.anyio
async def test_formal_mask_proposals_run_concurrently(repository: Repository) -> None:
    batch = repository.create_batch("parallel masks", "修改 @P01 的面部身份")
    sources = [
        add_source(repository, batch["id"], f"group-{index}.jpg")
        for index in range(2)
    ]
    semantic_compiler = CoordinatedSemanticMaskCompiler()
    service = WorkflowService(
        repository,
        contract_compiler=OccurrenceCoverageCompiler(),
        semantic_mask_compiler=semantic_compiler,
        asset_resolver=AssetResolver(repository.database_path.parent),
    )
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [
            {
                "asset_id": source["id"],
                "provider": "fake",
                "faces": [{
                    "external_face_id": f"face-{index}",
                    "bbox": {"x": 20, "y": 20, "width": 40, "height": 40},
                    "detection_confidence": 1,
                    "suggested_person_key": "same-person",
                    "appearance": {},
                }],
            }
            for index, source in enumerate(sources)
        ],
    )
    person = repository.list_persons(batch["id"])[0]

    compiled = await service.compile_task_contract(
        batch["id"],
        [
            MappingDecision(
                person_id=person["id"],
                action=MappingAction.EDIT,
                instruction="仅修改面部身份",
            )
        ],
    )

    assert semantic_compiler.calls == ["I01", "I02"]
    assert [
        source["code"] for source in compiled["compiled_contract"]["source_assets"]
    ] == ["I01", "I02"]


def confirm_contract(
    repository: Repository,
    batch_id: str,
    decisions: list[MappingDecision],
) -> CompiledBatchContract:
    batch = repository.get_batch(batch_id)
    identities = {
        identity["id"]: identity for identity in repository.list_identities(batch_id)
    }
    evidence_by_id: dict[str, CompiledIdentityEvidence] = {}
    for identity_id in {
        decision.target_identity_id
        for decision in decisions
        if decision.target_identity_id is not None
    }:
        identity = identities[identity_id]
        references = identity["references"]
        reference_assets = [
            ReferenceAssetEvidence(
                code=reference["display_code"],
                sha256=reference["sha256"],
            )
            for reference in references
        ]
        board = [
            {"code": reference.code, "sha256": reference.sha256}
            for reference in reference_assets
        ]
        profile = TargetIdentityProfile(
            identity_id=identity_id,
            identity_code=identity["display_code"],
            reference_board_codes=[item["code"] for item in board],
            reference_board_digest=hashlib.sha256(
                json.dumps(
                    board,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        )
        assessment = ReferenceSufficiency(
            identity_id=identity_id,
            identity_code=identity["display_code"],
            reference_count=len(reference_assets),
            sufficient=bool(reference_assets),
            requires_manual_review=not reference_assets,
        )
        evidence_by_id[identity_id] = CompiledIdentityEvidence(
            profile=profile,
            reference_assessment=assessment,
            reference_assets=reference_assets,
        )
    people = {
        person["id"]: person for person in repository.list_persons(batch_id)
    }
    if {decision.person_id for decision in decisions} != set(people):
        raise ValueError("manual mappings must cover every current person")
    operations = []
    for decision in decisions:
        editable = (
            ["face_identity"]
            if decision.action is MappingAction.REPLACE
            else ["face_features"]
            if decision.action is MappingAction.EDIT
            else []
        )
        evidence = (
            evidence_by_id[decision.target_identity_id]
            if decision.target_identity_id
            else None
        )
        operations.append(
            CompiledOperation(
                person_id=decision.person_id,
                person_code=people[decision.person_id]["display_code"],
                action=decision.action,
                target_identity_id=decision.target_identity_id,
                target_identity_code=(
                    evidence.profile.identity_code if evidence else None
                ),
                instruction=decision.instruction,
                permissions=AttributePermission(
                    editable=editable,
                    protected=sorted(HEAD_ATTRIBUTES - set(editable)),
                ),
                target_profile=evidence.profile if evidence else None,
                reference_assessment=(
                    evidence.reference_assessment if evidence else None
                ),
                reference_assets=evidence.reference_assets if evidence else [],
                requires_manual_review=bool(
                    evidence
                    and evidence.reference_assessment.requires_manual_review
                ),
            )
        )
    contract = CompiledBatchContract(
        task_instruction=batch["task_instruction"],
        max_iterations=batch["max_iterations"],
        operations=operations,
        identities=list(evidence_by_id.values()),
        source_assets=[
            SourceAssetEvidence(
                asset_id=source["id"],
                code=source["display_code"],
                sha256=source["sha256"],
                faces=[],
            )
            for source in repository.list_assets(batch_id, "source")
        ],
        requires_manual_review=any(
            operation.requires_manual_review for operation in operations
        ),
    )
    compilation = formal_compilation(repository, batch_id, contract)
    contract = compilation.contract
    repository.store_compiled_contract(batch_id, compilation)
    repository.confirm_compiled_contract(batch_id, contract.sha256(), decisions)
    return contract


def test_legacy_rows_decode_missing_contract_evidence_as_safe_defaults() -> None:
    batch = Repository._decode_batch({"requires_manual_review": 0})
    identity = Repository._decode_identity({})

    assert batch["compiled_contract"] is None
    assert batch["contract_hash"] is None
    assert batch["confirmed_at"] is None
    assert batch["requires_manual_review"] is False
    assert identity["target_profile"] is None
    assert identity["reference_sufficiency"] is None


def test_initialize_demotes_legacy_ready_rows_and_clears_confirmation(
    repository: Repository,
) -> None:
    analyzed = repository.create_batch("legacy analyzed")
    source = add_source(repository, analyzed["id"])
    service = WorkflowService(repository)
    service.start_analysis(analyzed["id"])
    service.complete_analysis(
        analyzed["id"],
        [{
            "asset_id": source["id"],
            "provider": "legacy-detector",
            "faces": [{
                "external_face_id": "face-1",
                "bbox": {"x": 1, "y": 1, "width": 20, "height": 20},
                "detection_confidence": 1,
                "suggested_person_key": "legacy-person",
            }],
        }],
    )
    person = repository.list_persons(analyzed["id"])[0]
    repository.save_mappings(
        analyzed["id"],
        [MappingDecision(person_id=person["id"], action=MappingAction.KEEP)],
    )
    never_analyzed = repository.create_batch("legacy draft")
    with repository.connect() as connection:
        connection.execute(
            """UPDATE batches
            SET status = ?, compiled_contract_json = ?,
                compiled_contract_hash = NULL, contract_confirmed_at = ?
            WHERE id = ?""",
            (
                BatchStatus.READY,
                "{}",
                "2025-01-01T00:00:00+00:00",
                analyzed["id"],
            ),
        )
        connection.execute(
            "UPDATE persons SET confirmed = 1 WHERE batch_id = ?",
            (analyzed["id"],),
        )
        connection.execute(
            "UPDATE batches SET status = ? WHERE id = ?",
            (BatchStatus.READY, never_analyzed["id"]),
        )

    repository.initialize()

    analyzed_after = repository.get_batch(analyzed["id"])
    draft_after = repository.get_batch(never_analyzed["id"])
    assert analyzed_after["status"] == BatchStatus.AWAITING_CONFIRMATION
    assert draft_after["status"] == BatchStatus.DRAFT
    assert analyzed_after["compiled_contract"] is None
    assert analyzed_after["contract_hash"] is None
    assert analyzed_after["confirmed_at"] is None
    migrated_person = repository.list_persons(analyzed["id"])[0]
    assert migrated_person["confirmed"] == 0
    assert migrated_person["mapping"] is None


def test_recovery_exposes_review_candidate_with_stable_result_code(
    repository: Repository,
) -> None:
    batch = repository.create_batch("recover candidate")
    source = add_source(repository, batch["id"])
    job = repository.create_job(
        batch["id"],
        JobStage.SWAP,
        source["id"],
        {"asset_id": source["id"]},
    )
    repository.set_batch_status(batch["id"], BatchStatus.SWAPPING)
    repository.complete_job(
        job["id"],
        status=JobStatus.NEEDS_REVIEW,
        provider="fake-image-provider",
        output={
            "output_uri": "file:///tmp/candidate.png",
            "output_sha256": "candidate-one",
        },
    )

    assert repository.recover_swap_result_assets() == 1
    first = repository.list_assets(batch["id"], "result")
    assert len(first) == 1
    assert first[0]["display_code"] == "O01"
    assert first[0]["media_type"] == "image/png"
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.REVIEW

    updated = repository.upsert_swap_result(
        batch_id=batch["id"],
        source_asset_id=source["id"],
        storage_uri="file:///tmp/candidate-new.png",
        sha256="candidate-two",
    )
    assert updated["id"] == first[0]["id"]
    assert updated["display_code"] == "O01"
    assert updated["sha256"] == "candidate-two"
    assert len(repository.list_assets(batch["id"], "result")) == 1


def test_analysis_requires_source_image(repository: Repository) -> None:
    batch = repository.create_batch("empty")
    with pytest.raises(InvalidTransition, match="source image"):
        WorkflowService(repository).start_analysis(batch["id"])


def test_analysis_always_pauses_for_mapping_confirmation(repository: Repository) -> None:
    batch = repository.create_batch("event")
    source = add_source(repository, batch["id"])
    service = WorkflowService(repository)

    jobs = service.start_analysis(batch["id"])
    assert len(jobs) == 1
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.ANALYZING

    service.complete_analysis(
        batch["id"],
        [
            {
                "asset_id": source["id"],
                "provider": "fake",
                "faces": [
                    {
                        "external_face_id": "face-1",
                        "bbox": {"x": 10, "y": 20, "width": 80, "height": 90},
                        "detection_confidence": 0.99,
                        "suggested_person_key": "cluster-a",
                        "identity_confidence": 0.98,
                        "needs_confirmation": False,
                    }
                ],
            }
        ],
    )

    assert repository.get_batch(batch["id"])["status"] == BatchStatus.AWAITING_CONFIRMATION
    assert repository.list_persons(batch["id"])[0]["confirmed"] == 0


def test_every_detected_person_requires_a_mapping(repository: Repository) -> None:
    batch = repository.create_batch("people")
    source = add_source(repository, batch["id"])
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [
            {
                "asset_id": source["id"],
                "provider": "fake",
                "faces": [
                    {
                        "external_face_id": face_id,
                        "bbox": {"x": x, "y": 0, "width": 50, "height": 50},
                        "detection_confidence": 0.9,
                        "suggested_person_key": cluster,
                        "needs_confirmation": True,
                    }
                    for face_id, x, cluster in [
                        ("face-a", 0, "cluster-a"),
                        ("face-b", 100, "cluster-b"),
                    ]
                ],
            }
        ],
    )
    people = repository.list_persons(batch["id"])

    with pytest.raises(ValueError, match="cover every current person"):
        confirm_contract(
            repository,
            batch["id"],
            [MappingDecision(person_id=people[0]["id"], action=MappingAction.KEEP)],
        )


def test_confirmed_mapping_moves_batch_to_ready(repository: Repository) -> None:
    batch = repository.create_batch("ready")
    source = add_source(repository, batch["id"])
    identity = repository.create_identity(batch["id"], "target")
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [
            {
                "asset_id": source["id"],
                "provider": "fake",
                "faces": [
                    {
                        "external_face_id": "face-a",
                        "bbox": {"x": 0, "y": 0, "width": 50, "height": 50},
                        "detection_confidence": 0.9,
                        "suggested_person_key": "cluster-a",
                        "needs_confirmation": True,
                    }
                ],
            }
        ],
    )
    person = repository.list_persons(batch["id"])[0]
    confirm_contract(
        repository,
        batch["id"],
        [
            MappingDecision(
                person_id=person["id"],
                action=MappingAction.REPLACE,
                target_identity_id=identity["id"],
            )
        ],
    )

    assert repository.get_batch(batch["id"])["status"] == BatchStatus.READY
    assert repository.list_persons(batch["id"])[0]["mapping"]["action"] == "replace"


def prompt_mapping_fixture(repository: Repository) -> tuple[WorkflowService, dict, list[dict], list[dict]]:
    batch = repository.create_batch("prompt mappings")
    source = add_source(repository, batch["id"])
    identities = [
        repository.create_identity(batch["id"], label)
        for label in ("target one", "target two")
    ]
    for identity in identities:
        repository.add_asset(
            batch_id=batch["id"],
            identity_id=identity["id"],
            kind="reference",
            filename=f"{identity['display_code']}.jpg",
            media_type="image/jpeg",
            storage_uri=f"file:///tmp/{identity['display_code']}.jpg",
            sha256="b" * 64,
        )
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [{
            "asset_id": source["id"],
            "provider": "fake",
            "faces": [
                {
                    "external_face_id": f"face-{index}",
                    "bbox": {"x": index * 100, "y": 0, "width": 50, "height": 50},
                    "detection_confidence": 0.99,
                    "suggested_person_key": f"person-{index}",
                    "needs_confirmation": False,
                }
                for index in range(2)
            ],
        }],
    )
    return service, batch, repository.list_persons(batch["id"]), identities


def test_task_instruction_compiles_numbered_mapping_and_keeps_others(
    repository: Repository,
) -> None:
    service, batch, people, identities = prompt_mapping_fixture(repository)
    repository.update_batch_spec(
        batch["id"],
        f"将 @{people[0]['display_code']} 替换为 @{identities[1]['display_code']}，保持其他人物",
        3,
    )

    decisions = service.compile_instruction_mappings(batch["id"])
    confirm_contract(repository, batch["id"], decisions)

    assert [decision.action for decision in decisions] == [
        MappingAction.REPLACE,
        MappingAction.KEEP,
    ]
    assert decisions[0].target_identity_id == identities[1]["id"]
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.READY


def test_protect_all_other_people_does_not_expand_numbered_mapping(
    repository: Repository,
) -> None:
    service, batch, people, identities = prompt_mapping_fixture(repository)
    repository.update_batch_spec(
        batch["id"],
        f"将 @{people[0]['display_code']} 在全部原图中的面部身份统一替换为 "
        f"@{identities[0]['display_code']}。严格保留其他所有人物、背景和文字。",
        3,
    )

    decisions = service.compile_instruction_mappings(batch["id"])
    confirm_contract(repository, batch["id"], decisions)
    jobs = service.start_swap(batch["id"])

    assert [decision.action for decision in decisions] == [
        MappingAction.REPLACE,
        MappingAction.KEEP,
    ]
    assert [item["source_person_code"] for item in jobs[0]["input"]["replacements"]] == [
        people[0]["display_code"]
    ]
    assert len(jobs[0]["input"]["protected_face_instance_ids"]) == 1


def test_generation_scope_guard_rejects_expanded_numbered_mapping() -> None:
    request = SwapRequest(
        batch_id="batch",
        asset_id="asset",
        asset_uri="data:image/png;base64,AA==",
        task_instruction="将 @P01 替换为 @T01，严格保留其他所有人物",
        compiled_instruction="corrupted mapping",
        replacements=[
            SwapOperation(
                face_instance_id="face-1",
                source_person_code="P01",
                target_identity_code="T01",
                target_identity_id="target",
                bbox=BoundingBox(x=1, y=2, width=30, height=40),
            ),
            SwapOperation(
                face_instance_id="face-2",
                source_person_code="P02",
                target_identity_code="T01",
                target_identity_id="target",
                bbox=BoundingBox(x=50, y=2, width=30, height=40),
            ),
        ],
        protected_face_instance_ids=[],
    )

    with pytest.raises(ValueError, match="@P02"):
        validate_swap_request_scope(request)


def test_task_instruction_can_map_every_person_to_one_identity(
    repository: Repository,
) -> None:
    service, batch, _, identities = prompt_mapping_fixture(repository)
    repository.update_batch_spec(
        batch["id"],
        f"把所有人物都替换为 @{identities[0]['display_code']}，其他内容保持不变",
        3,
    )

    decisions = service.compile_instruction_mappings(batch["id"])

    assert {decision.action for decision in decisions} == {MappingAction.REPLACE}
    assert {decision.target_identity_id for decision in decisions} == {identities[0]["id"]}


def test_task_instruction_accepts_numbered_text_edit_without_target_reference(
    repository: Repository,
) -> None:
    service, batch, people, _ = prompt_mapping_fixture(repository)
    repository.update_batch_spec(batch["id"], f"修改 @{people[0]['display_code']} 的脸", 3)

    decisions = service.compile_instruction_mappings(batch["id"])

    assert [decision.action for decision in decisions] == [
        MappingAction.EDIT,
        MappingAction.KEEP,
    ]
    assert all(decision.target_identity_id is None for decision in decisions)


def test_freeform_task_runs_without_identity_or_reference_material(
    repository: Repository,
) -> None:
    batch = repository.create_batch("text only")
    source = add_source(repository, batch["id"])
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [{
            "asset_id": source["id"],
            "provider": "fake",
            "faces": [{
                "external_face_id": "face-a",
                "bbox": {"x": 10, "y": 10, "width": 50, "height": 50},
                "detection_confidence": 0.99,
                "suggested_person_key": "person-a",
                "needs_confirmation": False,
            }],
        }],
    )
    repository.update_batch_spec(
        batch["id"],
        "把画面中戴眼镜的人改成银色短发，保持其他内容不变",
        3,
    )

    decisions = service.compile_instruction_mappings(batch["id"])
    confirm_contract(repository, batch["id"], decisions)
    jobs = service.start_swap(batch["id"])

    assert [decision.action for decision in decisions] == [MappingAction.EDIT]
    assert repository.list_identities(batch["id"]) == []
    assert len(jobs) == 1
    assert jobs[0]["input"]["selection_mode"] == "prompt"
    assert jobs[0]["input"]["replacements"][0]["target_identity_id"] is None
    assert jobs[0]["input"]["replacements"][0]["reference_uris"] == []
    assert "戴眼镜的人" in jobs[0]["input"]["compiled_instruction"]


def test_full_orchestration_creates_per_image_swap_and_validation_jobs(
    repository: Repository,
) -> None:
    batch = repository.create_batch("full")
    source = add_source(repository, batch["id"])
    identity = repository.create_identity(batch["id"], "target")
    repository.add_asset(
        batch_id=batch["id"],
        identity_id=identity["id"],
        kind="reference",
        filename="target.jpg",
        media_type="image/jpeg",
        storage_uri="file:///tmp/target.jpg",
        sha256="b" * 64,
    )
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [
            {
                "asset_id": source["id"],
                "provider": "fake-analyzer",
                "faces": [
                    {
                        "external_face_id": "replace-me",
                        "bbox": {"x": 10, "y": 10, "width": 100, "height": 100},
                        "detection_confidence": 0.99,
                        "suggested_person_key": "cluster-target",
                        "needs_confirmation": True,
                    },
                    {
                        "external_face_id": "protect-me",
                        "bbox": {"x": 200, "y": 10, "width": 100, "height": 100},
                        "detection_confidence": 0.99,
                        "suggested_person_key": "cluster-protected",
                        "needs_confirmation": True,
                    },
                ],
            }
        ],
    )
    people = {person["cluster_key"]: person for person in repository.list_persons(batch["id"])}
    confirm_contract(
        repository,
        batch["id"],
        [
            MappingDecision(
                person_id=people["cluster-target"]["id"],
                action=MappingAction.REPLACE,
                target_identity_id=identity["id"],
            ),
            MappingDecision(
                person_id=people["cluster-protected"]["id"],
                action=MappingAction.KEEP,
            ),
        ],
    )

    swap_jobs = service.start_swap(batch["id"])
    assert len(swap_jobs) == 1
    assert len(swap_jobs[0]["input"]["replacements"]) == 1
    assert len(swap_jobs[0]["input"]["protected_face_instance_ids"]) == 1
    assert swap_jobs[0]["input"]["source_image_code"] == "I01"
    assert swap_jobs[0]["input"]["preserve_outside_mask"] is True
    assert "@P01" in swap_jobs[0]["input"]["compiled_instruction"]
    assert "@T01" in swap_jobs[0]["input"]["compiled_instruction"]

    validation_jobs = service.complete_swap(
        batch["id"],
        [
            SwapResult(
                source_asset_id=source["id"],
                output_uri="https://assets.example/result.jpg",
                output_sha256="result",
                provider="fake-swapper",
                mask_uris=["https://assets.example/mask.png"],
            )
        ],
    )
    assert len(validation_jobs) == 1
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.VALIDATING

    result_asset_id = validation_jobs[0]["item_key"]
    with pytest.raises(
        ValueError,
        match="requires reviewer A and reviewer B",
    ):
        service.complete_validation(
            batch["id"],
            [
                ValidationResult(
                    result_asset_id=result_asset_id,
                    passed=True,
                    provider="unattested-validator",
                    checks={"outside_mask_unchanged": True},
                )
            ],
        )

    service.complete_validation(
        batch["id"],
        [
            ValidationResult(
                result_asset_id=result_asset_id,
                passed=True,
                provider="review-model-a+review-model-b+local-source-comparison",
                checks={"outside_mask_unchanged": True},
                reviewer_verdicts=[
                    ValidationReviewerVerdict(
                        reviewer_id="reviewer_a",
                        provider="review-model-a",
                        passed=True,
                        checks={"quality_passed": True},
                    ),
                    ValidationReviewerVerdict(
                        reviewer_id="reviewer_b",
                        provider="review-model-b",
                        passed=True,
                        checks={"quality_passed": True},
                    ),
                ],
            )
        ],
    )
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.REVIEW

    service.approve_batch(batch["id"])
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.COMPLETED


def test_superseded_contract_jobs_do_not_reenter_completion_or_reconciliation(
    repository: Repository,
) -> None:
    batch = repository.create_batch("contract cutover", "仅修改当前人物的面部")
    old_source = add_source(repository, batch["id"], "old-source.jpg")
    current_source = add_source(repository, batch["id"], "current-source.jpg")
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [
            {
                "asset_id": source["id"],
                "provider": "fake",
                "faces": [{
                    "external_face_id": f"face-{index}",
                    "bbox": {"x": 20, "y": 20, "width": 40, "height": 40},
                    "detection_confidence": 1,
                    "suggested_person_key": cluster,
                    "appearance": {},
                }],
            }
            for index, (source, cluster) in enumerate(
                (
                    (old_source, "old-person"),
                    (current_source, "current-person"),
                ),
                start=1,
            )
        ],
    )
    people = {
        person["cluster_key"]: person
        for person in repository.list_persons(batch["id"])
    }
    confirm_contract(
        repository,
        batch["id"],
        [
            MappingDecision(
                person_id=people["old-person"]["id"],
                action=MappingAction.KEEP,
            ),
            MappingDecision(
                person_id=people["current-person"]["id"],
                action=MappingAction.EDIT,
                instruction="仅修改面部身份",
            ),
        ],
    )
    current_swap = service.start_swap(batch["id"])[0]
    assert current_swap["item_key"] == current_source["id"]
    stale_swap = repository.create_job(
        batch["id"],
        JobStage.SWAP,
        old_source["id"],
        {
            **current_swap["input"],
            "asset_id": old_source["id"],
            "contract_hash": "0" * 64,
        },
    )
    repository.fail_job(
        stale_swap["id"],
        "provider_unavailable",
        "superseded contract failure",
    )

    validation_jobs = service.complete_swap(
        batch["id"],
        [
            SwapResult(
                source_asset_id=current_source["id"],
                output_uri="https://assets.example/current.jpg",
                output_sha256="current-result",
                provider="fake",
            )
        ],
    )

    assert len(validation_jobs) == 1
    current_result = repository.get_asset(validation_jobs[0]["item_key"])
    assert current_result["parent_asset_id"] == current_source["id"]
    assert validation_jobs[0]["input"]["contract_hash"] == repository.get_batch(
        batch["id"]
    )["contract_hash"]
    stale_validation = repository.create_job(
        batch["id"],
        JobStage.VALIDATE,
        current_result["id"],
        {
            "result_asset_id": current_result["id"],
            "contract_hash": "0" * 64,
        },
    )
    repository.fail_job(
        stale_validation["id"],
        "provider_unavailable",
        "superseded validation failure",
    )
    repository.complete_job(
        validation_jobs[0]["id"],
        status=JobStatus.NEEDS_REVIEW,
        provider="fake",
        output={},
    )

    assert repository.reconcile_terminal_batch_failures() == 0
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.VALIDATING
    latest_job_ids = {
        job["id"] for job in repository.latest_jobs(batch["id"])
    }
    assert stale_swap["id"] not in latest_job_ids
    assert stale_validation["id"] not in latest_job_ids
    progress = repository.batch_progress(batch["id"])["stages"]
    assert progress["swap"]["total"] == 1
    assert progress["swap"]["failed"] == 0
    assert progress["validate"]["total"] == 1
    assert progress["validate"]["failed"] == 0
    service.complete_validation(
        batch["id"],
        [
            ValidationResult(
                result_asset_id=current_result["id"],
                passed=True,
                provider="review-model-a+review-model-b+local-source-comparison",
                checks={"quality_passed": True},
                reviewer_verdicts=[
                    ValidationReviewerVerdict(
                        reviewer_id="reviewer_a",
                        provider="review-model-a",
                        passed=True,
                        checks={"quality_passed": True},
                    ),
                    ValidationReviewerVerdict(
                        reviewer_id="reviewer_b",
                        provider="review-model-b",
                        passed=True,
                        checks={"quality_passed": True},
                    ),
                ],
            )
        ],
    )
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.REVIEW
    service.approve_batch(batch["id"])
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.COMPLETED


def test_validation_jobs_link_peer_results_for_the_same_target_identity(
    repository: Repository,
) -> None:
    batch = repository.create_batch("consistent set", "将 @P01 替换为 @T01")
    first = add_source(repository, batch["id"], "first.jpg")
    second = add_source(repository, batch["id"], "second.jpg")
    identity = repository.create_identity(batch["id"], "target")
    repository.add_asset(
        batch_id=batch["id"],
        identity_id=identity["id"],
        kind="reference",
        filename="target.jpg",
        media_type="image/jpeg",
        storage_uri="file:///tmp/target.jpg",
        sha256="b" * 64,
    )
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [
            {
                "asset_id": asset["id"],
                "provider": "fake",
                "faces": [{
                    "external_face_id": f"face-{index}",
                    "bbox": {"x": 10, "y": 10, "width": 80, "height": 80},
                    "detection_confidence": 0.99,
                    "suggested_person_key": "same-person",
                    "needs_confirmation": True,
                }],
            }
            for index, asset in enumerate((first, second), start=1)
        ],
    )
    person = repository.list_persons(batch["id"])[0]
    confirm_contract(
        repository,
        batch["id"],
        [MappingDecision(
            person_id=person["id"],
            action=MappingAction.REPLACE,
            target_identity_id=identity["id"],
        )],
    )
    service.start_swap(batch["id"])

    validation_jobs = service.complete_swap(
        batch["id"],
        [
            SwapResult(
                source_asset_id=asset["id"],
                output_uri=f"https://assets.example/{asset['display_code']}.jpg",
                output_sha256=f"result-{asset['display_code']}",
                provider="fake",
            )
            for asset in (first, second)
        ],
    )

    assert len(validation_jobs) == 2
    for job in validation_jobs:
        assert job["input"]["expected_subjects"][0]["target_identity_code"] == "T01"
        assert len(job["input"]["peer_results"]) == 1
        assert job["input"]["peer_results"][0]["target_identity_codes"] == ["T01"]


def test_people_can_be_manually_merged_and_split_without_reusing_codes(
    repository: Repository,
) -> None:
    batch = repository.create_batch("person corrections")
    source = add_source(repository, batch["id"])
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [{
            "asset_id": source["id"],
            "provider": "fake",
            "faces": [
                {
                    "external_face_id": f"face-{index}",
                    "bbox": {"x": index * 50, "y": 0, "width": 40, "height": 40},
                    "detection_confidence": 0.99,
                    "suggested_person_key": f"cluster-{index}",
                    "needs_confirmation": True,
                }
                for index in range(3)
            ],
        }],
    )
    people = repository.list_persons(batch["id"])

    merged = repository.merge_persons(batch["id"], [people[0]["id"], people[1]["id"]])
    assert merged["display_code"] == "P01"
    assert len(merged["faces"]) == 2

    split = repository.split_person_faces(
        batch["id"],
        merged["id"],
        [merged["faces"][0]["id"]],
    )
    assert split["display_code"] == "P04"
    assert len(split["faces"]) == 1


def test_rerun_analysis_preserves_history_and_never_reuses_person_codes(
    repository: Repository,
) -> None:
    batch = repository.create_batch("reanalyze")
    source = add_source(repository, batch["id"])
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(batch["id"], [{
        "asset_id": source["id"],
        "provider": "first-detector",
        "faces": [{
            "external_face_id": "face-0",
            "bbox": {"x": 0, "y": 0, "width": 20, "height": 20},
            "detection_confidence": 0.8,
            "suggested_person_key": "first-person",
        }],
    }])
    assert repository.list_persons(batch["id"])[0]["display_code"] == "P01"

    jobs = service.reset_analysis(batch["id"])

    assert jobs[0]["attempt"] == 2
    assert repository.get_batch(batch["id"])["status"] == BatchStatus.ANALYZING
    assert len(repository.list_jobs(batch["id"])) == 2
    service.complete_analysis(batch["id"], [{
        "asset_id": source["id"],
        "provider": "second-detector",
        "faces": [{
            "external_face_id": "face-0",
            "bbox": {"x": 5, "y": 5, "width": 20, "height": 20},
            "detection_confidence": 0.95,
            "suggested_person_key": "second-person",
        }],
    }])
    assert repository.list_persons(batch["id"])[0]["display_code"] == "P02"


def test_confirmed_contract_is_required_and_propagated_to_one_source_job(
    repository: Repository,
) -> None:
    batch = repository.create_batch("contract", "修改 @P01 的脸", 2)
    source = add_source(repository, batch["id"])
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [{
            "asset_id": source["id"],
            "provider": "fake",
            "faces": [{
                "external_face_id": "face-1",
                "bbox": {"x": 1, "y": 2, "width": 30, "height": 40},
                "detection_confidence": 0.99,
                "suggested_person_key": "person-1",
                "needs_confirmation": False,
            }],
        }],
    )
    person = repository.list_persons(batch["id"])[0]
    with pytest.raises(InvalidTransition, match="cannot swap"):
        service.start_swap(batch["id"])
    contract = CompiledBatchContract(
        task_instruction="修改 @P01 的脸",
        max_iterations=2,
        operations=[
            CompiledOperation(
                person_id=person["id"],
                person_code=person["display_code"],
                action=MappingAction.EDIT,
                permissions=AttributePermission(
                    editable=["face_features"],
                    protected=[
                        "face_identity",
                        "apparent_age",
                        "hair",
                        "eyewear",
                        "accessories",
                        "makeup",
                    ],
                ),
            )
        ],
        identities=[],
        source_assets=[
            SourceAssetEvidence(
                asset_id=source["id"],
                code=source["display_code"],
                sha256=source["sha256"],
                faces=[],
            )
        ],
    )
    compilation = formal_compilation(repository, batch["id"], contract)
    contract = compilation.contract
    repository.store_compiled_contract(batch["id"], compilation)
    repository.confirm_compiled_contract(batch["id"], contract.sha256())

    jobs = service.start_swap(batch["id"])

    assert len(jobs) == 1
    assert jobs[0]["input"]["contract_hash"] == contract.sha256()
    assert jobs[0]["input"]["compiled_contract"] == contract.model_dump(
        mode="json",
        exclude_computed_fields=True,
    )
    assert jobs[0]["input"]["replacements"][0]["permissions"]["editable"] == [
        "face_features"
    ]
    with pytest.raises(InvalidTransition, match="cannot swap"):
        service.start_swap(batch["id"])
    assert len(repository.latest_stage_jobs(batch["id"], JobStage.SWAP)) == 1


def test_ready_claim_cas_failure_creates_no_swap_jobs(
    repository: Repository,
) -> None:
    batch = repository.create_batch("stale READY claim", "keep", 1)
    source = add_source(repository, batch["id"])
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [{"asset_id": source["id"], "provider": "fake", "faces": []}],
    )
    contract = CompiledBatchContract(
        task_instruction="keep",
        max_iterations=1,
        operations=[],
        source_assets=[
            SourceAssetEvidence(
                asset_id=source["id"],
                code=source["display_code"],
                sha256=source["sha256"],
                faces=[],
            )
        ],
    )
    compilation = formal_compilation(repository, batch["id"], contract)
    contract = compilation.contract
    repository.store_compiled_contract(batch["id"], compilation)
    ready = repository.confirm_compiled_contract(batch["id"], contract.sha256())

    with pytest.raises(ValueError, match="READY claim"):
        repository.claim_ready_and_create_swap_jobs(
            batch["id"],
            contract_hash=ready["contract_hash"],
            confirmed_at="stale-confirmation-timestamp",
            jobs=[(source["id"], {"max_iterations": 1})],
        )

    assert repository.get_batch(batch["id"])["status"] == BatchStatus.READY
    assert repository.latest_stage_jobs(batch["id"], JobStage.SWAP) == []


@pytest.mark.anyio
async def test_insufficient_references_mark_affected_output_for_manual_review(
    repository: Repository,
) -> None:
    class InsufficientCompiler:
        async def compile_target_identity(
            self,
            *,
            identity_id: str,
            identity_code: str,
            references: list[dict[str, str]],
        ):
            assert references == []
            return (
                TargetIdentityProfile(
                    identity_id=identity_id,
                    identity_code=identity_code,
                ),
                ReferenceSufficiency(
                    identity_id=identity_id,
                    identity_code=identity_code,
                    reference_count=0,
                    sufficient=False,
                    requires_manual_review=True,
                    summary="No usable reference views.",
                ),
            )

        async def compile_attribute_permissions(
            self,
            *,
            task_instruction: str,
            operations: list[dict[str, str | None]],
        ):
            return {
                str(operation["person_code"]): AttributePermission(
                    editable=["face_identity"],
                    protected=sorted(HEAD_ATTRIBUTES - {"face_identity"}),
                )
                for operation in operations
            }

        async def compile_source_reference_coverage(
            self,
            *,
            source_asset_id: str,
            source_asset_code: str,
            source_uri: str,
            person_id: str,
            person_code: str,
            occurrences: list,
            target_profile: TargetIdentityProfile,
            reference_assessment: ReferenceSufficiency,
            references: list[dict[str, str]],
        ):
            assert references == []
            assessments = [
                SourceOccurrenceReferenceAssessment(
                    face_instance_id=occurrence.face_instance_id,
                    missing_coverage=["no reference views"],
                    sufficient=False,
                    requires_manual_review=True,
                )
                for occurrence in occurrences
            ]
            return SourceOperationReferenceCoverage(
                source_asset_id=source_asset_id,
                source_asset_code=source_asset_code,
                person_id=person_id,
                person_code=person_code,
                occurrences=occurrences,
                occurrence_assessments=assessments,
                sufficient=False,
                requires_manual_review=True,
                summary="No reference views cover this source occurrence.",
            )

    batch = repository.create_batch("insufficient", "将 @P01 替换为 @T01")
    source = add_source(repository, batch["id"])
    identity = repository.create_identity(batch["id"], "target")
    service = WorkflowService(
        repository,
        contract_compiler=InsufficientCompiler(),
        semantic_mask_compiler=DeterministicSemanticMaskCompiler(),
        asset_resolver=AssetResolver(repository.database_path.parent),
    )
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [{
            "asset_id": source["id"],
            "provider": "fake",
            "faces": [{
                "external_face_id": "face-1",
                "bbox": {"x": 0, "y": 0, "width": 20, "height": 20},
                "detection_confidence": 1,
                "suggested_person_key": "person-1",
            }],
        }],
    )
    person = repository.list_persons(batch["id"])[0]
    decision = MappingDecision(
        person_id=person["id"],
        action=MappingAction.REPLACE,
        target_identity_id=identity["id"],
    )

    compiled = await service.compile_task_contract(batch["id"], [decision])

    persisted_contract = CompiledBatchContract.model_validate(
        compiled["compiled_contract"]
    )
    assert persisted_contract.requires_manual_review is True
    assert persisted_contract.review_reasons == frozenset({
        ReviewReason.REFERENCE_INSUFFICIENCY
    })
    service.confirm_task_contract(batch["id"], compiled["contract_hash"])
    job = service.start_swap(batch["id"])[0]
    assert job["input"]["review_reasons"] == ["reference_insufficiency"]
    assert "requires_manual_review" not in job["input"]
    assert job["input"]["replacements"][0]["review_reasons"] == [
        "reference_insufficiency"
    ]
    assert "requires_manual_review" not in job["input"]["replacements"][0]
    assert "requires_manual_review" not in SwapRequest.model_fields


@pytest.mark.anyio
async def test_source_pose_absent_from_reference_views_requires_manual_review(
    repository: Repository,
) -> None:
    coverage = await compile_reference_occurrence_case(
        repository,
        OccurrenceCoverageCompiler(),
    )

    model = SourceOperationReferenceCoverage.model_validate(coverage)
    assert model.sufficient is False
    assert model.requires_manual_review is True
    assessment = model.occurrence_assessments[0]
    assert assessment.missing_coverage == ["left profile reference"]
    assert assessment.requires_manual_review is True


@pytest.mark.anyio
async def test_source_reference_compiler_failure_fails_closed(
    repository: Repository,
) -> None:
    coverage = await compile_reference_occurrence_case(
        repository,
        OccurrenceCoverageCompiler(fail_coverage=True),
    )

    model = SourceOperationReferenceCoverage.model_validate(coverage)
    assert model.sufficient is False
    assert model.requires_manual_review is True
    assert model.occurrence_assessments[0].sufficient is False
    assert model.summary == (
        "Reference coverage compiler failed closed for this source person."
    )


def test_task_mutation_invalidates_hash_confirmation(repository: Repository) -> None:
    batch = repository.create_batch("stale", "keep", 1)
    source = add_source(repository, batch["id"])
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [{"asset_id": source["id"], "provider": "fake", "faces": []}],
    )
    contract = CompiledBatchContract(
        task_instruction="keep",
        max_iterations=1,
        operations=[],
        identities=[],
        source_assets=[
            SourceAssetEvidence(
                asset_id=source["id"],
                code=source["display_code"],
                sha256=source["sha256"],
                faces=[],
            )
        ],
    )
    compilation = formal_compilation(repository, batch["id"], contract)
    contract = compilation.contract
    repository.store_compiled_contract(batch["id"], compilation)
    repository.update_batch_spec(batch["id"], "changed", 1)
    invalidated = repository.get_batch(batch["id"])
    assert invalidated["status"] == BatchStatus.AWAITING_CONFIRMATION
    assert invalidated["compiled_contract"] is None
    assert invalidated["contract_hash"] is None
    assert invalidated["confirmed_at"] is None

    with pytest.raises(ValueError, match="stale"):
        repository.confirm_compiled_contract(batch["id"], contract.sha256())


def test_invalidation_losing_to_ready_claim_preserves_confirmed_contract(
    repository: Repository,
) -> None:
    batch = repository.create_batch("serialized invalidation", "keep", 1)
    source = add_source(repository, batch["id"])
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [{
            "asset_id": source["id"],
            "provider": "fake",
            "faces": [{
                "external_face_id": "face-1",
                "bbox": {"x": 0, "y": 0, "width": 20, "height": 20},
                "detection_confidence": 1,
                "suggested_person_key": "person-1",
            }],
        }],
    )
    person = repository.list_persons(batch["id"])[0]
    decision = MappingDecision(
        person_id=person["id"],
        action=MappingAction.KEEP,
    )
    contract = confirm_contract(repository, batch["id"], [decision])
    before = repository.get_batch(batch["id"])

    claim = repository.connect()
    claim_connection = claim.__enter__()
    claim_connection.execute("BEGIN IMMEDIATE")
    claim_connection.execute(
        "UPDATE batches SET status = ? WHERE id = ? AND status = ?",
        (BatchStatus.SWAPPING, batch["id"], BatchStatus.READY),
    )
    mutation_started = Event()
    mutation_errors: list[Exception] = []

    def mutate() -> None:
        mutation_started.set()
        try:
            repository.update_batch_spec(batch["id"], "changed", 1)
        except Exception as error:
            mutation_errors.append(error)

    mutation = Thread(target=mutate)
    mutation.start()
    assert mutation_started.wait(timeout=1)
    claim_connection.commit()
    claim.__exit__(None, None, None)
    mutation.join(timeout=2)

    assert not mutation.is_alive()
    assert len(mutation_errors) == 1
    assert isinstance(mutation_errors[0], ValueError)
    assert str(mutation_errors[0]) == "contract inputs are locked after generation starts"
    after = repository.get_batch(batch["id"])
    assert after["status"] == BatchStatus.SWAPPING
    assert after["contract_hash"] == before["contract_hash"] == contract.sha256()
    assert after["compiled_contract"] == before["compiled_contract"]
    assert repository.list_persons(batch["id"])[0]["mapping"] is not None


def test_reference_mutation_cannot_leave_persisted_contract_evidence_stale(
    repository: Repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = repository.create_batch("serialized contract storage", "replace", 1)
    source = add_source(repository, batch["id"])
    identity = repository.create_identity(batch["id"], "target")
    reference = repository.add_asset(
        batch_id=batch["id"],
        identity_id=identity["id"],
        kind="reference",
        filename="reference-1.jpg",
        media_type="image/jpeg",
        storage_uri="file:///tmp/reference-1.jpg",
        sha256="b" * 64,
    )
    service = WorkflowService(repository)
    service.start_analysis(batch["id"])
    service.complete_analysis(
        batch["id"],
        [{
            "asset_id": source["id"],
            "provider": "fake",
            "faces": [{
                "external_face_id": "face-1",
                "bbox": {"x": 0, "y": 0, "width": 20, "height": 20},
                "detection_confidence": 1,
                "suggested_person_key": "person-1",
            }],
        }],
    )
    person = repository.list_persons(batch["id"])[0]
    reference_evidence = ReferenceAssetEvidence(
        code=reference["display_code"],
        sha256=reference["sha256"],
    )
    reference_board = [
        {"code": reference_evidence.code, "sha256": reference_evidence.sha256}
    ]
    profile = TargetIdentityProfile(
        identity_id=identity["id"],
        identity_code=identity["display_code"],
        reference_board_codes=[item["code"] for item in reference_board],
        reference_board_digest=hashlib.sha256(
            json.dumps(
                reference_board,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
    )
    assessment = ReferenceSufficiency(
        identity_id=identity["id"],
        identity_code=identity["display_code"],
        reference_count=1,
        sufficient=True,
        requires_manual_review=False,
    )
    contract = CompiledBatchContract(
        task_instruction="replace",
        max_iterations=1,
        operations=[
            CompiledOperation(
                person_id=person["id"],
                person_code=person["display_code"],
                action=MappingAction.REPLACE,
                target_identity_id=identity["id"],
                target_identity_code=identity["display_code"],
                permissions=AttributePermission(
                    editable=["face_identity"],
                    protected=sorted(HEAD_ATTRIBUTES - {"face_identity"}),
                ),
                target_profile=profile,
                reference_assessment=assessment,
                reference_assets=[reference_evidence],
            )
        ],
        identities=[
            CompiledIdentityEvidence(
                profile=profile,
                reference_assessment=assessment,
                reference_assets=[reference_evidence],
            )
        ],
        source_assets=[
            SourceAssetEvidence(
                asset_id=source["id"],
                code=source["display_code"],
                sha256=source["sha256"],
                faces=[],
            )
        ],
    )
    compilation = formal_compilation(repository, batch["id"], contract)

    evidence_read = Event()
    release_store = Event()
    mutation_attempted = Event()
    mutation_finished = Event()

    class PausingCursor(sqlite3.Cursor):
        def execute(self, sql: str, parameters=(), /):
            cursor = super().execute(sql, parameters)
            if (
                "WHERE identity_id = ? AND kind = ?" in " ".join(sql.split())
                and parameters[1] == "reference"
            ):
                evidence_read.set()
                assert release_store.wait(timeout=2)
            return cursor

    class PausingConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters=(), /):
            return self.cursor(factory=PausingCursor).execute(sql, parameters)

    class MutationConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters=(), /):
            statement = sql.lstrip()
            if statement.startswith(
                (
                    "INSERT INTO display_code_counters",
                    "UPDATE display_code_counters",
                    "INSERT INTO assets",
                )
            ):
                mutation_attempted.set()
            return super().execute(sql, parameters)


    original_connect = sqlite3.connect

    def controlled_connect(database, *args, **kwargs):
        if current_thread().name == "contract-store":
            kwargs["factory"] = PausingConnection
        elif current_thread().name == "reference-mutation":
            kwargs["factory"] = MutationConnection
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", controlled_connect)
    store_errors: list[Exception] = []
    mutation_errors: list[Exception] = []

    def store() -> None:
        try:
            repository.store_compiled_contract(batch["id"], compilation)
        except Exception as error:
            store_errors.append(error)

    def mutate() -> None:
        try:
            Repository(repository.database_path).add_asset(
                batch_id=batch["id"],
                identity_id=identity["id"],
                kind="reference",
                filename="reference-2.jpg",
                media_type="image/jpeg",
                storage_uri="file:///tmp/reference-2.jpg",
                sha256="c" * 64,
            )
        except Exception as error:
            mutation_errors.append(error)
        finally:
            mutation_finished.set()

    store_thread = Thread(target=store, name="contract-store")
    mutation_thread = Thread(target=mutate, name="reference-mutation")
    store_thread.start()
    try:
        assert evidence_read.wait(timeout=1)
        mutation_thread.start()
        assert mutation_attempted.wait(timeout=1)
        assert not mutation_finished.wait(timeout=0.1)
    finally:
        release_store.set()
        store_thread.join(timeout=2)
        if mutation_thread.ident is not None:
            mutation_thread.join(timeout=2)

    assert not store_thread.is_alive()
    assert not mutation_thread.is_alive()
    assert not store_errors
    assert not mutation_errors
    final_batch = repository.get_batch(batch["id"])
    assert final_batch["compiled_contract"] is None
    assert final_batch["contract_hash"] is None
    assert {
        item["sha256"]
        for item in repository.list_identities(batch["id"])[0]["references"]
    } == {"b" * 64, "c" * 64}
