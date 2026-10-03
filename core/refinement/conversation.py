"""Single-turn evidence diagnosis, full-image generation and advisory candidate review."""

import numpy as np

from core.ai.gateway import AIGateway
from core.refinement.contracts import MAX_MESSAGE_CHARS, CandidateReview, Diagnosis
from core.refinement.evidence import (
    bounded_history,
    build_reference_board,
    checked_text,
    data_block,
    image_work,
    prepare_people,
    resolve_image,
    validate_evidence,
)
from core.refinement.image_ops import decode_image, encode_image, generation_parameters, normalize_candidate
from core.refinement.prompts import ANALYZE_SYSTEM, GENERATION_INSTRUCTIONS, PROMPT_VERSION, REVIEW_SYSTEM

# Application evidence budget, not a claim about any provider's maximum resolution.
VISION_MAX_SIDE = 3200


class ConversationRefinementService:
    """No agent loop or acceptance tools: the backend authorizes each explicit method call."""

    def __init__(self, gateway=None):
        self.gateway = gateway or AIGateway()

    @staticmethod
    def _append_image(content, label, image):
        content.append({"type": "text", "text": label})
        if image is None:
            content.append({"type": "text", "text": "UNAVAILABLE: no image supplied for this role."})
        else:
            image = resolve_image(image)
            # A 1600px overview lost small-face details in live tests. Bound the larger
            # overview instead of sending arbitrarily large photo payloads to providers.
            content.append({"type": "image_url", "image_url": {"url": encode_image(image, max_dim=VISION_MAX_SIDE)}})

    def _evidence(self, images, people, references):
        manifest = prepare_people(people, references)
        content = []
        for label, image in images:
            self._append_image(content, label, image)
        for person in manifest:
            person_id = person["person_id"]
            if person["reference_count"]:
                board = build_reference_board(person_id, references[person_id])
                self._append_image(
                    content, f"REFERENCE_BOARD_PERSON_{person_id}: desired identity for PERSON {person_id} only", board
                )
        return manifest, content

    @staticmethod
    def _validate_assessments(result, manifest):
        """Reject fabricated/missing IDs and identity claims lacking bound reference evidence."""
        by_id = {person["person_id"]: person for person in manifest}
        if {person.person_id for person in result.people} != set(by_id):
            raise ValueError("Assessment person IDs must exactly match the supplied stable person IDs")
        for person in result.people:
            source = by_id[person.person_id]
            if not source["reference_count"] and person.identity_status != "unavailable":
                raise ValueError("Missing reference evidence requires identity_status=unavailable")
        issues = result.issues if isinstance(result, Diagnosis) else result.remaining + result.new_issues
        for issue in issues:
            if issue.category == "identity" and not by_id[issue.person_id]["reference_count"]:
                raise ValueError("Identity defects require bound reference evidence")
        return result

    async def analyze(
        self, original, current, people, references, history, user_message, *, previous_diagnosis=None
    ) -> Diagnosis:
        """Combine same-base findings and current feedback with visible evidence; never generate.

        The server supplies the last completed diagnosis of this exact base, separately
        from bounded conversation history. Invalid or mismatched assessments are rejected.
        """
        validate_evidence(current)
        manifest, content = await image_work(
            self._evidence,
            [("ORIGINAL: scene/pose/count, NOT desired identity", original), ("CURRENT: image to assess", current)],
            people,
            references,
        )
        if previous_diagnosis is not None:
            previous_diagnosis = self._validate_assessments(Diagnosis.model_validate(previous_diagnosis), manifest)
        content.append(
            {
                "type": "text",
                "text": data_block(
                    {
                        "people": manifest,
                        "history": bounded_history(history),
                        "previous_diagnosis": previous_diagnosis.model_dump(mode="json")
                        if previous_diagnosis is not None
                        else None,
                        "current_user_message": checked_text(user_message, MAX_MESSAGE_CHARS),
                        "execution": "analysis_or_discussion_only; generation and acceptance are backend-controlled",
                    }
                ),
            }
        )
        _, result = await self.gateway.structured_vision_completion(
            [{"role": "system", "content": ANALYZE_SYSTEM}, {"role": "user", "content": content}],
            Diagnosis,
            alias=self.gateway.vision_alias("refinement"),
            max_text_repairs=0,
            response_format={"type": "json_object"},
        )
        return self._validate_assessments(Diagnosis.model_validate(result), manifest)

    async def generate(self, current, people, diagnosis, user_message, model_alias=None) -> tuple[np.ndarray, dict]:
        """Generate exactly one candidate from the selected full-resolution canvas, never publish it."""
        current = await image_work(resolve_image, current)
        diagnosis = Diagnosis.model_validate(diagnosis)
        manifest = prepare_people(people, {})
        if {person["person_id"] for person in manifest} != {person.person_id for person in diagnosis.people}:
            raise ValueError("Generation people must match the diagnosis stable person IDs")
        if diagnosis.mapping_uncertain:
            raise ValueError("Person mapping is uncertain; confirm the target before generating")
        if not diagnosis.needs_edit:
            raise ValueError("No edit is requested by the current diagnosis; revise the edit plan before generating")
        parameters, requested_size = generation_parameters(self.gateway, model_alias, current.shape)
        prompt = (
            GENERATION_INSTRUCTIONS
            + "\n"
            + data_block(
                {
                    "people": [
                        {"person_id": person.person_id, "person_locator": person.person_locator}
                        for person in diagnosis.people
                    ],
                    "issues": [issue.model_dump(mode="json") for issue in diagnosis.issues],
                    "edit_plan": diagnosis.edit_plan.model_dump(mode="json"),
                    "current_user_message": checked_text(user_message, MAX_MESSAGE_CHARS),
                }
            )
        )
        # Lossless encoding keeps the explicitly selected canvas intact; reference boards have their own bounded layout.
        result = await self.gateway.edit_image([current], prompt, alias=model_alias, parameters=parameters)
        candidate, normalization = normalize_candidate(decode_image(result.image_bytes), current.shape)
        alias = model_alias or self.gateway.default_image_alias
        return candidate, {
            "model_alias": alias,
            "model": result.model or alias,
            "provider": result.provider,
            "usage": result.usage or {},
            "request_id": result.request_id,
            "generation_mode": "whole-image-single-call",
            "input_image_count": 1,
            "requested_output_size": requested_size,
            "people_count": len(people),
            "prompt_version": PROMPT_VERSION,
            "prompt": prompt,
            **normalization,
        }

    async def review(self, original, before, candidate, people, references, diagnosis, user_message) -> CandidateReview:
        """Review once; propagate review failures so callers retain the already generated candidate."""
        validate_evidence(before)
        validate_evidence(candidate)
        diagnosis = Diagnosis.model_validate(diagnosis)
        manifest, content = await image_work(
            self._evidence,
            [
                ("ORIGINAL: scene/pose/count, NOT desired identity", original),
                ("BEFORE: explicitly selected base for this turn", before),
                ("CANDIDATE: complete generated image to assess", candidate),
            ],
            people,
            references,
        )
        if {person.person_id for person in diagnosis.people} != {person["person_id"] for person in manifest}:
            raise ValueError("Review people must match the diagnosis stable person IDs")
        content.append(
            {
                "type": "text",
                "text": data_block(
                    {
                        "people": manifest,
                        "diagnosis_hypothesis": diagnosis.model_dump(mode="json"),
                        "current_user_message": checked_text(user_message, MAX_MESSAGE_CHARS),
                        "execution": "advisory_review_only; no generation or acceptance",
                    }
                ),
            }
        )
        _, result = await self.gateway.structured_vision_completion(
            [{"role": "system", "content": REVIEW_SYSTEM}, {"role": "user", "content": content}],
            CandidateReview,
            alias=self.gateway.vision_alias("refinement"),
            max_text_repairs=0,
            response_format={"type": "json_object"},
        )
        return self._validate_assessments(CandidateReview.model_validate(result), manifest)
