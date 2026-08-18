import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from personaweave.assets import AssetResolver
from personaweave.domain import (
    AttributePermission,
    BoundingBox,
    CompiledBatchContract,
    CompiledFaceEvidence,
    CompiledIdentityEvidence,
    CompiledOperation,
    MappingAction,
    PersonAppearance,
    ReferenceAssetEvidence,
    ReferenceSufficiency,
    ReviewReasonSet,
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


class TaskContractCompiler(Protocol):
    async def compile_target_identity(self, *, identity_id: str, identity_code: str, references: list[dict[str, str]]) -> tuple[TargetIdentityProfile, ReferenceSufficiency]: ...
    async def compile_attribute_permissions(self, *, task_instruction: str, operations: list[dict[str, str | None]]) -> dict[str, AttributePermission]: ...
    async def compile_source_reference_coverage(
        self, *, source_asset_id: str, source_asset_code: str, source_uri: str,
        person_id: str, person_code: str, occurrences: list[SourceOccurrenceEvidence],
        target_profile: TargetIdentityProfile, reference_assessment: ReferenceSufficiency,
        references: list[dict[str, str]],
    ) -> SourceOperationReferenceCoverage: ...


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
    image_response_format: Literal["url", "b64_json"]




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
            authorization_mask_sha256=source.persisted_authorization_mask_sha256,
            authorization_mask_width=source.persisted_authorization_mask_width,
            authorization_mask_height=source.persisted_authorization_mask_height,
            preserve_outside_mask=True,
        )
        validate_swap_request_scope(request)
        return request


class FormalContractCompiler:
    """Compile one typed immutable snapshot into a complete formal contract."""

    async def compile(
        self,
        snapshot: FormalCompilationSnapshot,
        compiler: TaskContractCompiler,
        resolver: AssetResolver | None,
    ) -> CompiledBatchContract:
        people = {person.id: person for person in snapshot.people}
        mappings = {mapping.person_id: mapping for mapping in snapshot.mappings}
        if len(mappings) != len(snapshot.mappings) or set(mappings) != set(people):
            raise ValueError("manual mappings must cover every current person exactly once")
        target_ids = {mapping.target_identity_id for mapping in snapshot.mappings if mapping.action is MappingAction.REPLACE and mapping.target_identity_id}
        identities = {item.id: item for item in snapshot.identities if item.id in target_ids}
        if set(identities) != target_ids:
            raise ValueError("manual mappings reference an unknown target identity")
        evidence_by_id: dict[str, CompiledIdentityEvidence] = {}
        remote_refs: dict[str, list[dict[str, str]]] = {}
        for identity in identities.values():
            references = [{"code": ref.code, "uri": resolver.for_remote(ref.storage_uri, ref.media_type) if resolver else ref.storage_uri} for ref in identity.references]
            remote_refs[identity.id] = references
            profile, assessment = await compiler.compile_target_identity(identity_id=identity.id, identity_code=identity.code, references=references)
            board = [{"code": ref.code, "sha256": ref.sha256} for ref in identity.references]
            profile = profile.model_copy(update={
                "reference_board_codes": [item["code"] for item in board],
                "reference_board_digest": hashlib.sha256(json.dumps(board, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            })
            evidence_by_id[identity.id] = CompiledIdentityEvidence(
                profile=profile, reference_assessment=assessment,
                reference_assets=[ReferenceAssetEvidence(**item) for item in board],
            )
        permission_inputs: list[dict[str, str | None]] = []
        for mapping in snapshot.mappings:
            evidence = (
                evidence_by_id.get(mapping.target_identity_id)
                if mapping.target_identity_id is not None
                else None
            )
            permission_inputs.append({
                "person_code": people[mapping.person_id].code,
                "action": mapping.action.value,
                "target_identity_code": (
                    evidence.profile.identity_code if evidence is not None else None
                ),
            })
        permissions = await compiler.compile_attribute_permissions(task_instruction=snapshot.task_instruction, operations=permission_inputs)
        if set(permissions) != {str(item["person_code"]) for item in permission_inputs}:
            raise ValueError("permission compiler must cover every person exactly once")
        operations: list[CompiledOperation] = []
        for mapping in snapshot.mappings:
            person = people[mapping.person_id]
            target_identity_id = mapping.target_identity_id
            evidence = (
                evidence_by_id.get(target_identity_id)
                if target_identity_id is not None
                else None
            )
            coverages: list[SourceOperationReferenceCoverage] = []
            if evidence is not None and target_identity_id is not None:
                for source in snapshot.sources:
                    occurrences = [SourceOccurrenceEvidence(
                        face_instance_id=face.id, bbox=face.bbox,
                        face_area_pixels=face.bbox.width * face.bbox.height,
                        pose=face.appearance.pose or "unclear",
                        lighting_occlusion_blur=face.appearance.lighting_occlusion or "unclear",
                    ) for face in source.faces if face.person_id == person.id]
                    if not occurrences:
                        continue
                    try:
                        coverage = await compiler.compile_source_reference_coverage(
                            source_asset_id=source.id, source_asset_code=source.code,
                            source_uri=resolver.for_remote(source.storage_uri, source.media_type) if resolver else source.storage_uri,
                            person_id=person.id, person_code=person.code, occurrences=occurrences,
                            target_profile=evidence.profile, reference_assessment=evidence.reference_assessment,
                            references=remote_refs[target_identity_id],
                        )
                    except Exception:
                        coverage = SourceOperationReferenceCoverage(
                            source_asset_id=source.id, source_asset_code=source.code,
                            person_id=person.id, person_code=person.code, occurrences=occurrences,
                            occurrence_assessments=[SourceOccurrenceReferenceAssessment(
                                face_instance_id=item.face_instance_id,
                                missing_coverage=["compiler assessment unavailable"],
                                sufficient=False, requires_manual_review=True,
                                summary="Reference-to-occurrence coverage could not be confirmed; manual review is required.",
                            ) for item in occurrences], sufficient=False,
                            summary="Reference coverage compiler failed closed for this source person.",
                        )
                    if (coverage.source_asset_id, coverage.source_asset_code, coverage.person_id, coverage.person_code) != (source.id, source.code, person.id, person.code):
                        raise ValueError(
                            "reference coverage compiler returned mismatched source or person"
                        )
                    coverages.append(coverage)
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
        operations_by_person = {operation.person_id: operation for operation in operations}
        source_assets: list[SourceAssetEvidence] = []
        for source in snapshot.sources:
            compiled_faces = _compiled_face_evidence(source, people)
            editable_face_instance_ids = [
                face.id
                for face in source.faces
                if operations_by_person[face.person_id].action is not MappingAction.KEEP
            ]
            source_assets.append(
                SourceAssetEvidence(
                    asset_id=source.id,
                    code=source.code,
                    sha256=source.sha256,
                    faces=compiled_faces,
                    editable_face_instance_ids=editable_face_instance_ids,
                )
            )
        contract = CompiledBatchContract(
            task_instruction=snapshot.task_instruction,
            max_iterations=snapshot.max_iterations,
            operations=operations,
            identities=list(evidence_by_id.values()),
            source_assets=source_assets,
        )
        return contract
