import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from PIL import Image

from personaweave.assets import AssetResolver
from personaweave.domain import (
    AttributePermission,
    BoundingBox,
    CanonicalMaskEvidence,
    CompiledBatchContract,
    CompiledFaceEvidence,
    CompiledIdentityEvidence,
    CompiledOperation,
    MappingAction,
    PersonAppearance,
    ReferenceAssetEvidence,
    ReferenceSufficiency,
    ReviewReasonSet,
    ReviewReason,
    SemanticMaskProposal,
    SourceAssetEvidence,
    SourceOccurrenceEvidence,
    SourceOccurrenceReferenceAssessment,
    SourceOperationReferenceCoverage,
    SwapOperation,
    SwapRequest,
    TargetIdentityProfile,
    compile_formal_instruction,
    formal_selection_mode,
    instruction_requests_all_people_edit,
    project_formal_swap_operations,
)
from personaweave.formal_masks import (
    CompiledMaskArtifact,
    FormalCompilationResult,
    FormalMaskFaceScope,
    validate_and_rasterize_semantic_mask,
)


class TaskContractCompiler(Protocol):
    async def compile_target_identity(self, *, identity_id: str, identity_code: str, references: list[dict[str, str]]) -> tuple[TargetIdentityProfile, ReferenceSufficiency]: ...
    async def compile_attribute_permissions(self, *, task_instruction: str, operations: list[dict[str, str | None]]) -> dict[str, AttributePermission]: ...
    async def compile_source_reference_coverage(
        self, *, source_asset_id: str, source_asset_code: str, source_uri: str,
        person_id: str, person_code: str, occurrences: list[SourceOccurrenceEvidence],
        target_profile: TargetIdentityProfile, reference_assessment: ReferenceSufficiency,
        references: list[dict[str, str]],
    ) -> SourceOperationReferenceCoverage: ...


class SemanticMaskProposalCompiler(Protocol):
    async def compile_semantic_mask_proposal(
        self, *, source_asset_id: str, source_asset_code: str, source_uri: str,
        faces: list[dict[str, object]], protected_people: list[dict[str, str]],
        identity_evidence: list[dict[str, object]],
    ) -> SemanticMaskProposal: ...


@dataclass(frozen=True, slots=True)
class FormalReferenceSnapshot:
    code: str
    storage_uri: str
    media_type: str
    sha256: str


@dataclass(frozen=True, slots=True)
class FormalFaceSnapshot:
    id: str
    person_id: str
    bbox: BoundingBox
    appearance: PersonAppearance


@dataclass(frozen=True, slots=True)
class FormalSourceSnapshot:
    id: str
    code: str
    storage_uri: str
    media_type: str
    sha256: str
    faces: tuple[FormalFaceSnapshot, ...]
    persisted_authorization_mask_uri: str | None = None
    persisted_authorization_mask_sha256: str | None = None
    persisted_authorization_mask_width: int | None = None
    persisted_authorization_mask_height: int | None = None


@dataclass(frozen=True, slots=True)
class FormalPersonSnapshot:
    id: str
    code: str

def _compiled_face_evidence(
    source: FormalSourceSnapshot,
    people: dict[str, FormalPersonSnapshot],
) -> list[CompiledFaceEvidence]:
    return [
        CompiledFaceEvidence(
            face_instance_id=face.id,
            person_id=face.person_id,
            person_code=people[face.person_id].code,
            bbox=face.bbox,
            source_appearance=face.appearance,
        )
        for face in source.faces
    ]



@dataclass(frozen=True, slots=True)
class FormalIdentitySnapshot:
    id: str
    code: str
    references: tuple[FormalReferenceSnapshot, ...]


@dataclass(frozen=True, slots=True)
class FormalMappingSnapshot:
    person_id: str
    action: MappingAction
    target_identity_id: str | None
    instruction: str




@dataclass(frozen=True, slots=True)
class FormalCompilationSnapshot:
    batch_id: str
    task_instruction: str
    max_iterations: int
    people: tuple[FormalPersonSnapshot, ...]
    identities: tuple[FormalIdentitySnapshot, ...]
    sources: tuple[FormalSourceSnapshot, ...]
    mappings: tuple[FormalMappingSnapshot, ...]


@dataclass(frozen=True, slots=True)
class ConfirmedBatchSnapshot:
    id: str
    task_instruction: str
    max_iterations: int
    contract: CompiledBatchContract
    contract_hash: str
    confirmed_at: str


@dataclass(frozen=True, slots=True)
class FormalSwapRequestSnapshot:
    batch: ConfirmedBatchSnapshot
    source_id: str
    people: tuple[FormalPersonSnapshot, ...]
    identities: tuple[FormalIdentitySnapshot, ...]
    sources: tuple[FormalSourceSnapshot, ...]
    mappings: tuple[FormalMappingSnapshot, ...]


@dataclass(frozen=True, slots=True)
class GenerationProfile:
    image_model: str
    image_api_mode: Literal["json_generations", "multipart_edits"]
    image_api_path: str
    image_output_size: str
    image_response_format: Literal["url", "b64_json"] | None




def explicit_instruction_mappings(instruction: str) -> dict[str, str]:
    normalized = instruction.upper()
    replace_word = r"(?:替换(?:为|成)?|换成|变成|改成|->|=>|→)"
    pairs = [
        *re.findall(rf"@(P\d+)\s*(?:(?![。；;\n]).){{0,80}}?{replace_word}\s*@(T\d+)", normalized),
        *((person_code, identity_code) for identity_code, person_code in re.findall(
            rf"(?:用|使用)\s*@(T\d+)\s*(?:(?![。；;\n]).){{0,80}}?(?:替换|换掉)\s*@(P\d+)", normalized
        )),
    ]
    mappings: dict[str, str] = {}
    for person_code, identity_code in pairs:
        previous = mappings.get(person_code)
        if previous and previous != identity_code:
            raise ValueError(f"@{person_code} 同时指向 @{previous} 和 @{identity_code}，请明确唯一目标身份")
        mappings[person_code] = identity_code
    return mappings


def validate_swap_request_scope(request: SwapRequest) -> None:
    instruction = request.task_instruction.upper()
    mentioned_people = set(re.findall(r"@(P\d+)\b", instruction))
    all_people_requested = instruction_requests_all_people_edit(instruction)
    actual_codes = {
        operation.source_person_code.upper()
        for operation in request.replacements if operation.source_person_code
    }
    if mentioned_people and not all_people_requested:
        unauthorized = actual_codes - mentioned_people
        if unauthorized:
            rendered = ", ".join(f"@{code}" for code in sorted(unauthorized))
            raise ValueError(f"结构化映射超出用户明确人物范围：{rendered}")
    explicit_mappings = explicit_instruction_mappings(instruction)
    for operation in request.replacements:
        source_code = (operation.source_person_code or "").upper()
        expected_target = explicit_mappings.get(source_code)
        actual_target = (operation.target_identity_code or "").upper()
        if expected_target and actual_target != expected_target:
            raise ValueError(
                f"@{source_code} 的结构化目标 @{actual_target or 'NONE'} 与用户要求 @{expected_target} 不一致"
            )


class FormalSwapRequestFactory:
    """Own review policy and projection of one confirmed source request."""


    def create(
        self,
        snapshot: FormalSwapRequestSnapshot,
        profile: GenerationProfile,
    ) -> SwapRequest:
        if profile.image_api_mode != "multipart_edits":
            raise ValueError("formal generation requires multipart_edits")
        batch = snapshot.batch
        contract = batch.contract
        if contract.sha256() != batch.contract_hash:
            raise ValueError("confirmed task contract hash is stale")
        if (
            contract.task_instruction != batch.task_instruction
            or contract.max_iterations != batch.max_iterations
        ):
            raise ValueError("confirmed task contract no longer matches the batch")
        people = {person.id: person for person in snapshot.people}
        mappings = {mapping.person_id: mapping for mapping in snapshot.mappings}
        compiled_by_person = {
            operation.person_id: operation for operation in contract.operations
        }
        if (
            len(mappings) != len(snapshot.mappings)
            or set(mappings) != set(people)
            or set(compiled_by_person) != set(people)
        ):
            raise ValueError("confirmed task contract does not cover every person")
        for person_id, mapping in mappings.items():
            compiled = compiled_by_person[person_id]
            if (
                mapping.action != compiled.action
                or mapping.target_identity_id != compiled.target_identity_id
                or mapping.instruction != compiled.instruction
            ):
                raise ValueError("confirmed mappings no longer match the task contract")
        sources = {source.id: source for source in snapshot.sources}
        source_evidence_by_id = {
            evidence.asset_id: evidence for evidence in contract.source_assets
        }
        if {
            source.id: (source.code, source.sha256) for source in snapshot.sources
        } != {
            evidence.asset_id: (evidence.code, evidence.sha256)
            for evidence in contract.source_assets
        }:
            raise ValueError("confirmed source lineage is stale")
        try:
            snapshot_face_evidence = {
                source.id: _compiled_face_evidence(source, people)
                for source in snapshot.sources
            }
        except KeyError as error:
            raise ValueError("confirmed source face refers to an unknown person") from error
        if any(
            evidence.faces != snapshot_face_evidence[evidence.asset_id]
            for evidence in contract.source_assets
        ):
            raise ValueError("confirmed source face evidence is stale")
        try:
            source = sources[snapshot.source_id]
        except KeyError as error:
            raise ValueError("requested source is outside the confirmed snapshot") from error
        source_evidence = source_evidence_by_id[source.id]
        if not source_evidence.editable_face_instance_ids:
            raise ValueError("confirmed source is not eligible for formal generation")
        authorization_mask = source_evidence.authorization_mask
        if authorization_mask is None:
            raise ValueError("editable source has no canonical authorization mask evidence")
        if not source.persisted_authorization_mask_uri:
            raise ValueError("editable source has no persisted canonical authorization mask")
        if (
            source.persisted_authorization_mask_sha256 != authorization_mask.sha256
            or source.persisted_authorization_mask_width != authorization_mask.width
            or source.persisted_authorization_mask_height != authorization_mask.height
        ):
            raise ValueError(
                "persisted authorization mask does not match canonical contract evidence"
            )
        face_ids = [face.id for face in source.faces]
        if len(face_ids) != len(set(face_ids)):
            raise ValueError("confirmed source face instances are not unique")
        editable_face_ids = set(source_evidence.editable_face_instance_ids)
        if not editable_face_ids.issubset(face_ids):
            raise ValueError("canonical editable-face projection is stale")

        identities = {identity.id: identity for identity in snapshot.identities}
        selection_mode = formal_selection_mode(batch.task_instruction)
        replacements: list[SwapOperation] = []
        for projected in project_formal_swap_operations(contract, source_evidence):
            if projected.source_person_id is None:
                raise ValueError("formal swap projection is missing its source person")
            compiled = compiled_by_person[projected.source_person_id]
            references = (
                identities[compiled.target_identity_id].references
                if compiled.action is MappingAction.REPLACE
                and compiled.target_identity_id
                else ()
            )
            replacements.append(
                projected.model_copy(
                    update={
                        "reference_uris": [
                            item.storage_uri for item in references
                        ],
                    }
                )
            )
        protected_face_ids = [
            face.face_instance_id
            for face in source_evidence.faces
            if face.face_instance_id not in editable_face_ids
        ]
        review_reasons: ReviewReasonSet = frozenset(
            reason
            for operation in replacements
            for reason in operation.review_reasons
        )
        if (
            source_evidence.authorization_mask is not None
            and ReviewReason.MASK_UNCERTAIN
            in source_evidence.authorization_mask.review_reasons
        ):
            review_reasons = review_reasons | frozenset({ReviewReason.MASK_UNCERTAIN})
        compiled_instruction = compile_formal_instruction(contract, source_evidence)
        request = SwapRequest(
            batch_id=batch.id,
            asset_id=source.id,
            asset_uri=source.storage_uri,
            replacements=replacements,
            protected_face_instance_ids=protected_face_ids,
            source_image_code=source.code,
            task_instruction=batch.task_instruction,
            compiled_instruction=compiled_instruction,
            selection_mode=selection_mode,
            max_iterations=batch.max_iterations,
            image_model=profile.image_model,
            image_api_mode=profile.image_api_mode,
            image_api_path=profile.image_api_path,
            image_output_size=profile.image_output_size,
            image_response_format=profile.image_response_format,
            compiled_contract=contract,
            contract_hash=batch.contract_hash,
            review_reasons=review_reasons,
            authorization_mask_uri=source.persisted_authorization_mask_uri,
            authorization_mask_sha256=authorization_mask.sha256,
            authorization_mask_width=authorization_mask.width,
            authorization_mask_height=authorization_mask.height,
            preserve_outside_mask=True,
        )
        validate_swap_request_scope(request)
        return request


class FormalContractCompiler:
    """Compile one typed immutable snapshot into a complete formal contract."""

    def __init__(self, concurrency: int = 4):
        if not 1 <= concurrency <= 16:
            raise ValueError("formal compilation concurrency must be between 1 and 16")
        self.concurrency = concurrency

    async def compile(
        self,
        snapshot: FormalCompilationSnapshot,
        compiler: TaskContractCompiler,
        mask_compiler: SemanticMaskProposalCompiler,
        resolver: AssetResolver | None,
    ) -> FormalCompilationResult:
        people = {person.id: person for person in snapshot.people}
        mappings = {mapping.person_id: mapping for mapping in snapshot.mappings}
        if len(mappings) != len(snapshot.mappings) or set(mappings) != set(people):
            raise ValueError("manual mappings must cover every current person exactly once")
        target_ids = {mapping.target_identity_id for mapping in snapshot.mappings if mapping.action is MappingAction.REPLACE and mapping.target_identity_id}
        identities = {item.id: item for item in snapshot.identities if item.id in target_ids}
        if set(identities) != target_ids:
            raise ValueError("manual mappings reference an unknown target identity")
        remote_refs = {
            identity.id: [
                {
                    "code": ref.code,
                    "uri": (
                        resolver.for_remote(ref.storage_uri, ref.media_type)
                        if resolver
                        else ref.storage_uri
                    ),
                }
                for ref in identity.references
            ]
            for identity in identities.values()
        }
        identity_codes = {identity.id: identity.code for identity in identities.values()}
        permission_inputs: list[dict[str, str | None]] = [
            {
                "person_code": people[mapping.person_id].code,
                "action": mapping.action.value,
                "target_identity_code": (
                    identity_codes.get(mapping.target_identity_id)
                    if mapping.target_identity_id is not None
                    else None
                ),
            }
            for mapping in snapshot.mappings
        ]
        compilation_semaphore = asyncio.Semaphore(self.concurrency)

        async def compile_identity(
            identity: FormalIdentitySnapshot,
        ) -> tuple[str, CompiledIdentityEvidence]:
            references = remote_refs[identity.id]
            async with compilation_semaphore:
                profile, assessment = await compiler.compile_target_identity(
                    identity_id=identity.id,
                    identity_code=identity.code,
                    references=references,
                )
            board = [{"code": ref.code, "sha256": ref.sha256} for ref in identity.references]
            profile = profile.model_copy(update={
                "reference_board_codes": [item["code"] for item in board],
                "reference_board_digest": hashlib.sha256(json.dumps(board, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            })
            return identity.id, CompiledIdentityEvidence(
                profile=profile,
                reference_assessment=assessment,
                reference_assets=[ReferenceAssetEvidence(**item) for item in board],
            )

        async def compile_permissions() -> dict[str, AttributePermission]:
            async with compilation_semaphore:
                return await compiler.compile_attribute_permissions(
                    task_instruction=snapshot.task_instruction,
                    operations=permission_inputs,
                )

        identity_results, permissions = await asyncio.gather(
            asyncio.gather(*(
                compile_identity(identity) for identity in identities.values()
            )),
            compile_permissions(),
        )
        evidence_by_id = dict(identity_results)
        if set(permissions) != {str(item["person_code"]) for item in permission_inputs}:
            raise ValueError("permission compiler must cover every person exactly once")

        coverage_by_person: dict[str, list[SourceOperationReferenceCoverage]] = {
            mapping.person_id: [] for mapping in snapshot.mappings
        }

        async def compile_coverage(
            *,
            person: FormalPersonSnapshot,
            source: FormalSourceSnapshot,
            occurrences: list[SourceOccurrenceEvidence],
            target_identity_id: str,
            evidence: CompiledIdentityEvidence,
        ) -> tuple[str, SourceOperationReferenceCoverage]:
            try:
                async with compilation_semaphore:
                    coverage = await compiler.compile_source_reference_coverage(
                        source_asset_id=source.id,
                        source_asset_code=source.code,
                        source_uri=(
                            resolver.for_remote(source.storage_uri, source.media_type)
                            if resolver
                            else source.storage_uri
                        ),
                        person_id=person.id,
                        person_code=person.code,
                        occurrences=occurrences,
                        target_profile=evidence.profile,
                        reference_assessment=evidence.reference_assessment,
                        references=remote_refs[target_identity_id],
                    )
            except Exception:
                coverage = SourceOperationReferenceCoverage(
                    source_asset_id=source.id,
                    source_asset_code=source.code,
                    person_id=person.id,
                    person_code=person.code,
                    occurrences=occurrences,
                    occurrence_assessments=[
                        SourceOccurrenceReferenceAssessment(
                            face_instance_id=item.face_instance_id,
                            missing_coverage=["compiler assessment unavailable"],
                            sufficient=False,
                            requires_manual_review=True,
                            summary=(
                                "Reference-to-occurrence coverage could not be "
                                "confirmed; manual review is required."
                            ),
                        )
                        for item in occurrences
                    ],
                    sufficient=False,
                    summary=(
                        "Reference coverage compiler failed closed for this source person."
                    ),
                )
            if (
                coverage.source_asset_id,
                coverage.source_asset_code,
                coverage.person_id,
                coverage.person_code,
            ) != (source.id, source.code, person.id, person.code):
                raise ValueError(
                    "reference coverage compiler returned mismatched source or person"
                )
            return person.id, coverage

        coverage_jobs: list[
            Awaitable[tuple[str, SourceOperationReferenceCoverage]]
        ] = []
        for mapping in snapshot.mappings:
            if mapping.target_identity_id is None:
                continue
            evidence = evidence_by_id.get(mapping.target_identity_id)
            if evidence is None:
                continue
            person = people[mapping.person_id]
            for source in snapshot.sources:
                occurrences = [
                    SourceOccurrenceEvidence(
                        face_instance_id=face.id,
                        bbox=face.bbox,
                        face_area_pixels=face.bbox.width * face.bbox.height,
                        pose=face.appearance.pose or "unclear",
                        lighting_occlusion_blur=(
                            face.appearance.lighting_occlusion or "unclear"
                        ),
                    )
                    for face in source.faces
                    if face.person_id == person.id
                ]
                if occurrences:
                    coverage_jobs.append(compile_coverage(
                        person=person,
                        source=source,
                        occurrences=occurrences,
                        target_identity_id=mapping.target_identity_id,
                        evidence=evidence,
                    ))
        for person_id, coverage in await asyncio.gather(*coverage_jobs):
            coverage_by_person[person_id].append(coverage)

        operations: list[CompiledOperation] = []
        for mapping in snapshot.mappings:
            person = people[mapping.person_id]
            target_identity_id = mapping.target_identity_id
            evidence = (
                evidence_by_id.get(target_identity_id)
                if target_identity_id is not None
                else None
            )
            coverages = coverage_by_person[person.id]
            operation_review_reasons: ReviewReasonSet = frozenset()
            operations.append(CompiledOperation(
                person_id=person.id, person_code=person.code, action=mapping.action,
                target_identity_id=mapping.target_identity_id,
                target_identity_code=evidence.profile.identity_code if evidence else None,
                instruction=mapping.instruction, permissions=permissions[person.code],
                target_profile=evidence.profile if evidence else None,
                reference_assessment=evidence.reference_assessment if evidence else None,
                reference_assets=evidence.reference_assets if evidence else [],
                source_reference_coverage=coverages,
                review_reasons=operation_review_reasons,
            ))
        if resolver is None:
            raise ValueError("formal semantic masks require a configured local asset resolver")
        operations_by_person = {operation.person_id: operation for operation in operations}
        source_assets: list[SourceAssetEvidence | None] = [None] * len(snapshot.sources)
        compiled_masks: list[CompiledMaskArtifact] = []
        mask_compile_semaphore = asyncio.Semaphore(self.concurrency)

        async def compile_masked_source(
            *,
            source_index: int,
            source: FormalSourceSnapshot,
            source_path: Path,
            compiled_faces: list[CompiledFaceEvidence],
            editable_scopes: list[FormalMaskFaceScope],
            protected_faces: list[BoundingBox],
            editable_face_instance_ids: list[str],
            face_payloads: list[dict[str, object]],
            protected_people: list[dict[str, str]],
            identity_payloads: list[dict[str, object]],
        ) -> tuple[int, SourceAssetEvidence, CompiledMaskArtifact]:
            async with mask_compile_semaphore:
                proposal = await mask_compiler.compile_semantic_mask_proposal(
                    source_asset_id=source.id,
                    source_asset_code=source.code,
                    source_uri=resolver.for_remote(source.storage_uri, source.media_type),
                    faces=face_payloads,
                    protected_people=protected_people,
                    identity_evidence=identity_payloads,
                )
            rasterized = validate_and_rasterize_semantic_mask(
                source_path=source_path,
                proposal=proposal,
                source_asset_id=source.id,
                source_asset_code=source.code,
                editable_faces=editable_scopes,
                protected_faces=protected_faces,
            )
            mask_evidence = CanonicalMaskEvidence(
                source_asset_id=source.id,
                source_asset_code=source.code,
                width=rasterized.width,
                height=rasterized.height,
                sha256=rasterized.sha256,
                proposal=rasterized.proposal,
                review_reasons=rasterized.review_reasons,
            )
            return (
                source_index,
                SourceAssetEvidence(
                    asset_id=source.id,
                    code=source.code,
                    sha256=source.sha256,
                    faces=compiled_faces,
                    editable_face_instance_ids=editable_face_instance_ids,
                    authorization_mask=mask_evidence,
                ),
                CompiledMaskArtifact(
                    evidence=mask_evidence,
                    png_bytes=rasterized.png_bytes,
                ),
            )

        mask_jobs: list[
            Awaitable[tuple[int, SourceAssetEvidence, CompiledMaskArtifact]]
        ] = []
        for source_index, source in enumerate(snapshot.sources):
            source_path = resolver.local_path(source.storage_uri)
            if source_path is None or not source_path.is_file():
                raise ValueError("formal semantic mask source must be locally available")
            with Image.open(source_path) as source_image:
                source_width, source_height = source_image.size
            compiled_faces = _compiled_face_evidence(source, people)
            editable_scopes: list[FormalMaskFaceScope] = []
            protected_faces: list[BoundingBox] = []
            face_payloads: list[dict[str, object]] = []
            for face in source.faces:
                person = people[face.person_id]
                operation = operations_by_person[person.id]
                editable = operation.action is not MappingAction.KEEP
                face_payloads.append(
                    {
                        "face_instance_id": face.id,
                        "person_id": person.id,
                        "person_code": person.code,
                        "bbox": face.bbox.model_dump(mode="json"),
                        "normalized_bbox": {
                            "x": face.bbox.x / source_width,
                            "y": face.bbox.y / source_height,
                            "width": face.bbox.width / source_width,
                            "height": face.bbox.height / source_height,
                        },
                        "source_dimensions": {
                            "width": source_width,
                            "height": source_height,
                        },
                        "action": operation.action.value,
                        "editable_attributes": list(operation.permissions.editable),
                        "protected_attributes": list(operation.permissions.protected),
                    }
                )
                if editable:
                    editable_scopes.append(
                        FormalMaskFaceScope(
                            face_instance_id=face.id,
                            person_id=person.id,
                            person_code=person.code,
                            bbox=face.bbox,
                            permissions=operation.permissions,
                        )
                    )
                else:
                    protected_faces.append(face.bbox)
            editable_face_instance_ids = [
                scope.face_instance_id for scope in editable_scopes
            ]
            if not editable_scopes:
                source_assets[source_index] = SourceAssetEvidence(
                    asset_id=source.id,
                    code=source.code,
                    sha256=source.sha256,
                    faces=compiled_faces,
                    editable_face_instance_ids=[],
                )
                continue
            protected_people = [
                {
                    "person_id": operation.person_id,
                    "person_code": operation.person_code,
                }
                for operation in operations
                if operation.action is MappingAction.KEEP
            ]
            identity_payloads: list[dict[str, object]] = []
            for scope in editable_scopes:
                operation = operations_by_person[scope.person_id]
                target_profile = operation.target_profile
                reference_assessment = operation.reference_assessment
                identity_payloads.append(
                    {
                        "person_id": scope.person_id,
                        "person_code": scope.person_code,
                        "target_profile": (
                            target_profile.model_dump(mode="json")
                            if target_profile is not None
                            else None
                        ),
                        "reference_assessment": (
                            reference_assessment.model_dump(mode="json")
                            if reference_assessment is not None
                            else None
                        ),
                        "source_reference_coverage": [
                            coverage.model_dump(mode="json")
                            for coverage in operation.source_reference_coverage
                            if coverage.source_asset_id == source.id
                        ],
                    }
                )
            mask_jobs.append(
                compile_masked_source(
                    source_index=source_index,
                    source=source,
                    source_path=source_path,
                    compiled_faces=compiled_faces,
                    editable_scopes=editable_scopes,
                    protected_faces=protected_faces,
                    editable_face_instance_ids=editable_face_instance_ids,
                    face_payloads=face_payloads,
                    protected_people=protected_people,
                    identity_payloads=identity_payloads,
                )
            )
        for source_index, source_evidence, mask_artifact in await asyncio.gather(*mask_jobs):
            source_assets[source_index] = source_evidence
            compiled_masks.append(mask_artifact)
        if any(source is None for source in source_assets):
            raise RuntimeError("formal source compilation returned incomplete evidence")
        compiled_source_assets = [
            source for source in source_assets if source is not None
        ]
        contract = CompiledBatchContract(
            task_instruction=snapshot.task_instruction,
            max_iterations=snapshot.max_iterations,
            operations=operations,
            identities=list(evidence_by_id.values()),
            source_assets=compiled_source_assets,
        )
        return FormalCompilationResult(contract=contract, masks=tuple(compiled_masks))
