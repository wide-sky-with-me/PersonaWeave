import re
from typing import Any

from personaweave.assets import AssetResolver
from personaweave.domain import (
    AnalysisRequest,
    AssetKind,
    BatchStatus,
    BoundingBox,
    CompiledBatchContract,
    JobStage,
    JobStatus,
    MappingAction,
    MappingDecision,
    PersonAppearance,
    SwapResult,
    ValidationPeer,
    ValidationRequest,
    ValidationResult,
    ValidationSubject,
)
from personaweave.formal import (
    ConfirmedBatchSnapshot,
    FormalCompilationSnapshot,
    FormalContractCompiler,
    FormalFaceSnapshot,
    FormalIdentitySnapshot,
    FormalMappingSnapshot,
    FormalPersonSnapshot,
    FormalReferenceSnapshot,
    FormalSourceSnapshot,
    FormalSwapRequestFactory,
    FormalSwapRequestSnapshot,
    GenerationProfile,
    TaskContractCompiler,
    explicit_instruction_mappings,
    instruction_requests_all_people_edit,
    validate_swap_request_scope,
)
from personaweave.storage import Repository


class InvalidTransition(ValueError):
    pass



class WorkflowService:
    def __init__(
        self,
        repository: Repository,
        image_model: str = "gpt-image-2",
        image_api_mode: str = "multipart_edits",
        image_api_path: str = "/images/edits",
        image_output_size: str = "1024x1024",
        image_response_format: str | None = None,
        *,
        contract_compiler: TaskContractCompiler | None = None,
        semantic_mask_compiler: Any | None = None,
        asset_resolver: AssetResolver | None = None,
        contract_compilation_concurrency: int = 4,
    ):
        self.repository = repository
        self.image_model = image_model
        self.image_api_mode = image_api_mode
        self.image_api_path = image_api_path
        self.image_output_size = image_output_size
        self.image_response_format = image_response_format
        self.contract_compiler = contract_compiler
        self.semantic_mask_compiler = semantic_mask_compiler
        self.asset_resolver = asset_resolver
        self.formal_contract_compiler = FormalContractCompiler(
            contract_compilation_concurrency
        )
        self.swap_request_factory = FormalSwapRequestFactory()

    def _build_formal_sources(self, batch_id: str) -> tuple[FormalSourceSnapshot, ...]:
        source_records = self.repository.list_assets(batch_id, AssetKind.SOURCE)
        mask_records = self.repository.list_formal_masks(batch_id)
        masks_by_source: dict[str, dict[str, Any]] = {}
        for mask in mask_records:
            if mask.get("status") == "confirmed":
                masks_by_source[mask["source_asset_id"]] = mask
        return tuple(
            FormalSourceSnapshot(
                id=source["id"],
                code=source["display_code"],
                storage_uri=source["storage_uri"],
                media_type=source["media_type"],
                sha256=source["sha256"],
                faces=tuple(
                    FormalFaceSnapshot(
                        id=face["id"],
                        person_id=face["person_id"],
                        bbox=BoundingBox.model_validate(face["bbox"]),
                        appearance=PersonAppearance.model_validate(
                            face.get("appearance") or {}
                        ),
                    )
                    for face in self.repository.list_faces_for_asset(source["id"])
                ),
                persisted_authorization_mask_uri=masks_by_source.get(source["id"], {}).get("storage_uri"),
                persisted_authorization_mask_sha256=masks_by_source.get(source["id"], {}).get("sha256"),
                persisted_authorization_mask_width=masks_by_source.get(source["id"], {}).get("width"),
                persisted_authorization_mask_height=masks_by_source.get(source["id"], {}).get("height"),
            )
            for source in source_records
        )

    def start_analysis(self, batch_id: str) -> list[dict]:
        batch = self.repository.get_batch(batch_id)
        if batch["status"] not in {BatchStatus.DRAFT, BatchStatus.FAILED}:
            raise InvalidTransition(f"cannot analyze batch from {batch['status']}")
        assets = self.repository.list_assets(batch_id, AssetKind.SOURCE)
        if not assets:
            raise InvalidTransition("at least one source image is required")

        self.repository.set_batch_status(batch_id, BatchStatus.ANALYZING)
        return [
            self.repository.create_job(
                batch_id,
                JobStage.ANALYZE,
                asset["id"],
                AnalysisRequest(
                    batch_id=batch_id,
                    asset_id=asset["id"],
                    asset_uri=asset["storage_uri"],
                ).model_dump(mode="json"),
            )
            for asset in assets
        ]

    def complete_analysis(self, batch_id: str, results: list[dict]) -> None:
        batch = self.repository.get_batch(batch_id)
        if batch["status"] != BatchStatus.ANALYZING:
            raise InvalidTransition(f"cannot complete analysis from {batch['status']}")
        source_ids = {
            asset["id"]
            for asset in self.repository.list_assets(batch_id, AssetKind.SOURCE)
        }
        result_ids = {result["asset_id"] for result in results}
        if source_ids != result_ids:
            raise ValueError(
                f"analysis must cover every source exactly once; missing={source_ids - result_ids}, "
                f"unknown={result_ids - source_ids}"
            )
        for result in results:
            for face in result["faces"]:
                face["needs_confirmation"] = True
            self.repository.record_analysis(batch_id, result["asset_id"], result["faces"])
            job = self.repository.get_job_for_item(batch_id, JobStage.ANALYZE, result["asset_id"])
            self.repository.complete_job(
                job["id"],
                status=JobStatus.SUCCEEDED,
                provider=result["provider"],
                output=result,
            )
        self.repository.set_batch_status(batch_id, BatchStatus.AWAITING_CONFIRMATION)

    def reset_analysis(self, batch_id: str) -> list[dict]:
        batch = self.repository.get_batch(batch_id)
        if batch["status"] != BatchStatus.AWAITING_CONFIRMATION:
            raise InvalidTransition(
                f"cannot restart person analysis from {batch['status']}"
            )
        cleaned_instruction = re.sub(
            r"@P\d+\b",
            "",
            batch["task_instruction"],
            flags=re.IGNORECASE,
        )
        cleaned_instruction = re.sub(r"[ \t]{2,}", " ", cleaned_instruction).strip()
        self.repository.reset_analysis(batch_id)
        if cleaned_instruction != batch["task_instruction"]:
            self.repository.update_batch_spec(
                batch_id,
                cleaned_instruction,
                batch["max_iterations"],
            )
        return self.start_analysis(batch_id)

    def _derive_instruction_mappings(self, batch_id: str) -> list[MappingDecision]:
        """Compile explicit task references without persisting or confirming them."""
        batch = self.repository.get_batch(batch_id)
        if batch["status"] != BatchStatus.AWAITING_CONFIRMATION:
            raise InvalidTransition(f"cannot compile task mappings from {batch['status']}")
        instruction = batch["task_instruction"].strip().upper()
        if not instruction:
            raise ValueError("请先填写任务要求")

        people = self.repository.list_persons(batch_id)
        identities = self.repository.list_identities(batch_id)
        people_by_code = {person["display_code"].upper(): person for person in people}
        identities_by_code = {
            identity["display_code"].upper(): identity for identity in identities
        }
        replacements: dict[str, str] = {}

        def add_replacement(person_code: str, identity_code: str) -> None:
            if person_code not in people_by_code or identity_code not in identities_by_code:
                return
            previous = replacements.get(person_code)
            if previous and previous != identity_code:
                raise ValueError(
                    f"@{person_code} 同时指向 @{previous} 和 @{identity_code}，请明确唯一目标身份"
                )
            replacements[person_code] = identity_code

        for person_code, identity_code in explicit_instruction_mappings(instruction).items():
            add_replacement(person_code, identity_code)
        all_people_requested = instruction_requests_all_people_edit(instruction)
        mentioned_people = set(re.findall(r"@(P\d+)\b", instruction))
        kept_people = {
            code
            for pair in re.findall(
                r"(?:保留|不要修改|不修改|保持原样)\s*@(P\d+)|@(P\d+)\s*(?:保留|不要修改|不修改|保持原样)",
                instruction,
            )
            for code in pair
            if code
        }
        mentioned_targets = list(dict.fromkeys(re.findall(r"@(T\d+)\b", instruction)))
        if all_people_requested:
            if len(mentioned_targets) > 1:
                raise ValueError("处理所有人物时最多只能指定一个统一的 @T 目标身份")
            if mentioned_targets:
                for person_code in people_by_code:
                    add_replacement(person_code, mentioned_targets[0])
        elif mentioned_targets and not replacements:
            raise ValueError("使用 @T 参考身份时，请同时指定 @P 人物或明确写出“所有人物”")
        text_edit_people = (
            set(people_by_code)
            if all_people_requested or not mentioned_people
            else mentioned_people - kept_people
        )
        decisions = []
        for person_code, person in people_by_code.items():
            identity_code = replacements.get(person_code)
            if identity_code is None:
                decisions.append(
                    MappingDecision(
                        person_id=person["id"],
                        action=(
                            MappingAction.EDIT
                            if person_code in text_edit_people
                            else MappingAction.KEEP
                        ),
                    )
                )
            else:
                decisions.append(
                    MappingDecision(
                        person_id=person["id"],
                        action=MappingAction.REPLACE,
                        target_identity_id=identities_by_code[identity_code]["id"],
                    )
                )
        return decisions

    def compile_instruction_mappings(self, batch_id: str) -> list[MappingDecision]:
        """Return task-derived mappings without persisting or confirming them."""
        return self._derive_instruction_mappings(batch_id)

    async def compile_task_contract(
        self,
        batch_id: str,
        decisions: list[MappingDecision] | None = None,
    ) -> dict:
        if self.image_api_mode != "multipart_edits":
            raise ValueError("formal generation requires multipart_edits")
        batch = self.repository.get_batch(batch_id)
        decisions = (
            self._derive_instruction_mappings(batch_id)
            if decisions is None
            else decisions
        )
        compiler = self.contract_compiler
        if compiler is None:
            raise ValueError("task contract compiler is not configured")
        mask_compiler = self.semantic_mask_compiler
        if mask_compiler is None:
            raise ValueError("semantic mask proposal compiler is not configured")
        people = self.repository.list_persons(batch_id)
        identities = self.repository.list_identities(batch_id)
        sources = self._build_formal_sources(batch_id)
        snapshot = FormalCompilationSnapshot(
            batch_id=batch_id,
            task_instruction=batch["task_instruction"],
            max_iterations=batch["max_iterations"],
            people=tuple(
                FormalPersonSnapshot(id=person["id"], code=person["display_code"])
                for person in people
            ),
            identities=tuple(
                FormalIdentitySnapshot(
                    id=identity["id"],
                    code=identity["display_code"],
                    references=tuple(
                        FormalReferenceSnapshot(
                            code=reference["display_code"],
                            storage_uri=reference["storage_uri"],
                            media_type=reference["media_type"],
                            sha256=reference["sha256"],
                        )
                        for reference in identity["references"]
                    ),
                )
                for identity in identities
            ),
            sources=sources,
            mappings=tuple(
                FormalMappingSnapshot(
                    person_id=decision.person_id,
                    action=decision.action,
                    target_identity_id=decision.target_identity_id,
                    instruction=decision.instruction,
                )
                for decision in decisions
            ),
        )
        compilation = await self.formal_contract_compiler.compile(
            snapshot,
            compiler,
            mask_compiler,
            self.asset_resolver,
        )
        return self.repository.store_compiled_contract(batch_id, compilation)

    def confirm_task_contract(
        self,
        batch_id: str,
        contract_hash: str,
        decisions: list[MappingDecision] | None = None,
        acknowledged_mask_sha256s: list[str] | None = None,
    ) -> dict:
        return self.repository.confirm_compiled_contract(
            batch_id,
            contract_hash,
            decisions,
            acknowledged_mask_sha256s,
        )

    def start_swap(self, batch_id: str) -> list[dict]:
        batch = self.repository.get_batch(batch_id)
        if batch["status"] != BatchStatus.READY:
            raise InvalidTransition(f"cannot swap batch from {batch['status']}")
        if not batch["compiled_contract"] or not batch["contract_hash"] or not batch["confirmed_at"]:
            raise InvalidTransition("batch has no confirmed task contract")
        contract = CompiledBatchContract.model_validate(batch["compiled_contract"])
        people_records = self.repository.list_persons(batch_id)
        identity_records = self.repository.list_identities(batch_id)
        people = tuple(
            FormalPersonSnapshot(id=person["id"], code=person["display_code"])
            for person in people_records
        )
        identities = tuple(
            FormalIdentitySnapshot(
                id=identity["id"],
                code=identity["display_code"],
                references=tuple(
                    FormalReferenceSnapshot(
                        code=reference["display_code"],
                        storage_uri=reference["storage_uri"],
                        media_type=reference["media_type"],
                        sha256=reference["sha256"],
                    )
                    for reference in identity["references"]
                ),
            )
            for identity in identity_records
        )
        sources = self._build_formal_sources(batch_id)
        try:
            mappings = tuple(
                FormalMappingSnapshot(
                    person_id=person["id"],
                    action=MappingAction(person["mapping"]["action"]),
                    target_identity_id=person["mapping"]["target_identity_id"],
                    instruction=person["mapping"].get("instruction", ""),
                )
                for person in people_records
                if person["mapping"] is not None
            )
            confirmed = ConfirmedBatchSnapshot(
                id=batch_id,
                task_instruction=batch["task_instruction"],
                max_iterations=batch["max_iterations"],
                contract=contract,
                contract_hash=batch["contract_hash"],
                confirmed_at=batch["confirmed_at"],
            )
            profile = GenerationProfile(
                image_model=self.image_model,
                image_api_mode=self.image_api_mode,
                image_api_path=self.image_api_path,
                image_output_size=self.image_output_size,
                image_response_format=self.image_response_format,
            )
            job_inputs = []
            sources_by_id = {source.id: source for source in sources}
            for source_evidence in contract.source_assets:
                if not source_evidence.editable_face_instance_ids:
                    continue
                source = sources_by_id[source_evidence.asset_id]
                request = self.swap_request_factory.create(
                    FormalSwapRequestSnapshot(
                        batch=confirmed,
                        source_id=source.id,
                        people=people,
                        identities=identities,
                        sources=sources,
                        mappings=mappings,
                    ),
                    profile,
                )
                job_inputs.append((
                    source.id,
                    request.model_dump(
                        mode="json",
                        exclude_computed_fields=True,
                    ),
                ))
            if not job_inputs:
                raise InvalidTransition("batch has no source images to edit")
            return self.repository.claim_ready_and_create_swap_jobs(
                batch_id,
                contract_hash=batch["contract_hash"],
                confirmed_at=batch["confirmed_at"],
                jobs=job_inputs,
            )
        except ValueError as error:
            raise InvalidTransition(str(error)) from error

    def complete_swap(self, batch_id: str, results: list[SwapResult]) -> list[dict]:
        batch = self.repository.get_batch(batch_id)
        if batch["status"] != BatchStatus.SWAPPING:
            raise InvalidTransition(f"cannot complete swap from {batch['status']}")
        swap_jobs = self.repository.current_contract_stage_jobs(
            batch_id,
            JobStage.SWAP,
        )
        swap_jobs_by_item = {job["item_key"]: job for job in swap_jobs}
        expected_asset_ids = set(swap_jobs_by_item)
        result_asset_ids = {result.source_asset_id for result in results}
        if expected_asset_ids != result_asset_ids:
            raise ValueError(
                f"swap results must cover every job; missing={expected_asset_ids - result_asset_ids}, "
                f"unknown={result_asset_ids - expected_asset_ids}"
            )

        result_records: list[tuple[SwapResult, dict, dict]] = []
        for result in results:
            source = self.repository.get_asset(result.source_asset_id)
            swap_job = swap_jobs_by_item[result.source_asset_id]
            result = result.model_copy(
                update={
                    "review_reasons": frozenset(
                        swap_job["input"].get("review_reasons", [])
                    ),
                }
            )
            self.repository.complete_job(
                swap_job["id"],
                status=JobStatus.SUCCEEDED,
                provider=result.provider,
                output=result.model_dump(mode="json"),
            )
            result_asset = self.repository.upsert_swap_result(
                batch_id=batch_id,
                source_asset_id=source["id"],
                storage_uri=result.output_uri,
                sha256=result.output_sha256,
            )
            result_records.append((result, source, result_asset))

        validation_jobs = []
        peer_data = []
        for _, peer_source, peer_result in result_records:
            peer_job = swap_jobs_by_item[peer_source["id"]]
            peer_data.append((
                peer_result,
                peer_source,
                sorted({
                    operation.get("target_identity_code")
                    for operation in peer_job["input"]["replacements"]
                    if operation.get("target_identity_code")
                }),
            ))

        for _, source, result_asset in result_records:
            swap_job = swap_jobs_by_item[source["id"]]
            protected_ids = swap_job["input"]["protected_face_instance_ids"]
            operations = swap_job["input"]["replacements"]
            expected_ids = [operation["face_instance_id"] for operation in operations]
            target_codes = {
                operation.get("target_identity_code")
                for operation in operations
                if operation.get("target_identity_code")
            }
            peer_anchors: dict[str, tuple[dict, dict, list[str]]] = {}
            for target_code in sorted(target_codes):
                anchor = next(
                    (
                        (peer_result, peer_source, peer_codes)
                        for peer_result, peer_source, peer_codes in peer_data
                        if peer_result["id"] != result_asset["id"]
                        and target_code in peer_codes
                    ),
                    None,
                )
                if anchor is not None:
                    peer_anchors[anchor[0]["id"]] = anchor
            validation = ValidationRequest(
                batch_id=batch_id,
                contract_hash=swap_job["input"]["contract_hash"],
                result_asset_id=result_asset["id"],
                source_uri=source["storage_uri"],
                result_uri=result_asset["storage_uri"],
                expected_face_instance_ids=expected_ids,
                protected_face_instance_ids=protected_ids,
                source_image_code=source["display_code"],
                task_instruction=swap_job["input"].get("task_instruction", ""),
                compiled_instruction=swap_job["input"].get("compiled_instruction", ""),
                expected_subjects=[
                    ValidationSubject(
                        face_instance_id=operation["face_instance_id"],
                        source_person_code=operation["source_person_code"],
                        source_reference_coverage=operation.get(
                            "source_reference_coverage"
                        ),
                        target_identity_code=operation.get("target_identity_code"),
                        bbox=operation["bbox"],
                        reference_uris=operation["reference_uris"],
                        permissions=operation.get("permissions"),
                        target_profile=operation.get("target_profile"),
                        reference_assessment=operation.get("reference_assessment"),
                        contract_hash=operation.get("contract_hash"),
                        review_reasons=operation.get("review_reasons", []),
                    )
                    for operation in operations
                    if operation.get("source_person_code")
                ],
                peer_results=[
                    ValidationPeer(
                        result_asset_id=peer_result["id"],
                        result_uri=peer_result["storage_uri"],
                        source_image_code=peer_source["display_code"],
                        target_identity_codes=peer_codes,
                    )
                    for peer_result, peer_source, peer_codes in peer_anchors.values()
                ],
                review_reasons=swap_job["input"].get("review_reasons", []),
            )
            validation_jobs.append(
                self.repository.create_job(
                    batch_id,
                    JobStage.VALIDATE,
                    result_asset["id"],
                    validation.model_dump(mode="json"),
                )
            )
        self.repository.set_batch_status(batch_id, BatchStatus.VALIDATING)
        return validation_jobs

    def complete_validation(self, batch_id: str, results: list[ValidationResult]) -> None:
        batch = self.repository.get_batch(batch_id)
        if batch["status"] != BatchStatus.VALIDATING:
            raise InvalidTransition(f"cannot complete validation from {batch['status']}")
        jobs = self.repository.current_contract_stage_jobs(
            batch_id,
            JobStage.VALIDATE,
        )
        jobs_by_item = {job["item_key"]: job for job in jobs}
        expected_ids = set(jobs_by_item)
        result_ids = {result.result_asset_id for result in results}
        if expected_ids != result_ids:
            raise ValueError(
                f"validation must cover every result; missing={expected_ids - result_ids}, "
                f"unknown={result_ids - expected_ids}"
            )
        for result in results:
            if result.passed and {
                verdict.reviewer_id for verdict in result.reviewer_verdicts
            } != {"reviewer_a", "reviewer_b"}:
                raise ValueError(
                    "passing validation requires reviewer A and reviewer B verdicts"
                )
        for result in results:
            job = jobs_by_item[result.result_asset_id]
            result = result.model_copy(
                update={
                    "review_reasons": frozenset(
                        job["input"].get("review_reasons", [])
                    )
                }
            )
            self.repository.complete_job(
                job["id"],
                status=(
                    JobStatus.SUCCEEDED
                    if result.passed and not result.review_reasons
                    else JobStatus.NEEDS_REVIEW
                ),
                provider=result.provider,
                output=result.model_dump(mode="json"),
            )
        self.repository.set_batch_status(batch_id, BatchStatus.REVIEW)

    def approve_batch(self, batch_id: str) -> None:
        batch = self.repository.get_batch(batch_id)
        if batch["status"] != BatchStatus.REVIEW:
            raise InvalidTransition(f"cannot approve batch from {batch['status']}")
        failed_validation = [
            job
            for job in self.repository.current_contract_stage_jobs(
                batch_id,
                JobStage.VALIDATE,
            )
            if job["status"] != JobStatus.SUCCEEDED
        ]
        if failed_validation:
            raise InvalidTransition("validation issues must be resolved before final approval")
        self.repository.set_batch_status(batch_id, BatchStatus.COMPLETED)
