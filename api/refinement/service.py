"""One persistent session executor: diagnostic turns, single-image generation and human acceptance."""

import asyncio
import json
import re
from contextlib import suppress
from uuid import uuid4

from pydantic import ValidationError

from api.media import TrustedImagePath, load_image_from_source
from api.portraits.library import resolve_asset_ids, resolve_identity_asset_ids
from api.refinement.contracts import ModelUnavailable
from api.refinement.store import RefinementSessionStore, fingerprint
from core.ai.types import ModelConfigurationError, ModelRequestError
from core.refinement.contracts import MAX_EVIDENCE_PIXELS

_ARTIFACT_URL = re.compile(r"^/api/v1/refinement/sessions/([a-f0-9]{32})/artifacts/([a-f0-9]{64})$")
# Allow evidence encoding/validation around the default 300-second gateway request.
VISION_STAGE_TIMEOUT_SECONDS = 330


def _failure_reason(exc: Exception) -> str:
    """Expose only known failure kinds; raw exception text can contain secrets or image data."""
    if isinstance(exc, ModelConfigurationError):
        return ": local AI routing configuration invalid; check model provider prefixes and deployments"
    if isinstance(exc, ModelRequestError):
        status = f" HTTP {exc.status_code}" if exc.status_code is not None else ""
        return f": upstream{status} request error"
    if isinstance(exc, TimeoutError):
        return ": model request timed out"
    validation = exc if isinstance(exc, ValidationError) else exc.__cause__
    if isinstance(validation, ValidationError):
        # Error messages, input values, context and even extra-field names are untrusted.
        allowed = {
            "missing",
            "extra_forbidden",
            "json_invalid",
            "string_too_short",
            "string_too_long",
            "literal_error",
            "value_error",
        }
        codes = sorted(
            {e["type"] for e in validation.errors(include_input=False, include_context=False) if e["type"] in allowed}
        )
        return ": model response schema invalid" + (f" ({', '.join(codes)})" if codes else "")
    return ""


def _count_evidence_pixels(pixels, image):
    """Reject cumulative decoded evidence before retaining or snapshotting the next image."""
    pixels += int(image.shape[0]) * int(image.shape[1])
    if pixels > MAX_EVIDENCE_PIXELS:
        raise ValueError("Session image evidence exceeds the decoded pixel budget")
    return pixels


class RefinementSessionService:
    """Persist commands before scheduling; the server, never a browser, owns running turns."""

    def __init__(self, ai=None, store=None, max_concurrent=1):
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        self.ai = ai
        self.store = store or RefinementSessionStore()
        self._workers = {}
        self._slots = asyncio.Semaphore(max_concurrent)
        self._closing = False

    def get(self, session_id):
        return self.store.get(session_id)

    def list(self, limit=30, offset=0):
        return self.store.list(limit, offset)

    def artifact_bytes(self, session_id, digest):
        return self.store.artifact_bytes(session_id, digest)

    def events(self, session_id, after=0):
        return self.store.events(session_id, after)

    async def delete(self, session_id, expected_revision):
        """Remove a revision-checked idle record without invoking models."""
        await asyncio.to_thread(self.store.delete, session_id, expected_revision)

    def _load_source(self, source):
        match = _ARTIFACT_URL.fullmatch(source) if isinstance(source, str) else None
        if match:
            return self.store.image(*match.groups())
        return load_image_from_source(source)

    def _snapshot_creation(self, request, request_hash):
        existing = self.store.find_creation(request.request_id, request_hash)
        if existing:
            return existing
        session_id = uuid4().hex
        try:
            original = self._load_source(request.original_image_url)
            pixels = _count_evidence_pixels(0, original)
            baseline = self._load_source(request.baseline_image_url)
            pixels = _count_evidence_pixels(pixels, baseline)
            if original.shape[:2] != baseline.shape[:2]:
                raise ValueError("Original and baseline must share the same image canvas")
            original_digest = self.store.snapshot(session_id, original)
            baseline_digest = self.store.snapshot(session_id, baseline)
            people, reference_count = [], 0
            for person in request.people:
                asset_ids = person.source_asset_ids
                if person.source_identity_id:
                    asset_ids = resolve_identity_asset_ids(person.source_identity_id)
                    if asset_ids is None:
                        raise ValueError("Reference identity is unavailable")
                locations = [
                    TrustedImagePath(path) for path in resolve_asset_ids(asset_ids)
                ] + person.source_manual_images
                if len(locations) > 8:
                    raise ValueError("Select at most eight reference images for each person")
                reference_count += len(locations)
                if reference_count > 64:
                    raise ValueError("A session may contain at most 64 reference images")
                digests = []
                for location in locations:
                    reference = self._load_source(location)
                    pixels = _count_evidence_pixels(pixels, reference)
                    digests.append(self.store.snapshot(session_id, reference))
                # Position is frozen from the caller's original-image binding, never
                # re-detected or sorted from a generated image or reference-board order.
                locator = person.person_locator.strip() or f"原图中从左到右第{person.face_index}位人物"
                people.append(
                    {
                        "person_id": person.person_id,
                        "face_index": person.face_index,
                        "person_locator": locator,
                        "reference_digests": digests,
                        "reference_status": "available" if digests else "missing",
                    }
                )
            return self.store.create(session_id, request, request_hash, original_digest, baseline_digest, people)
        finally:
            # Only this attempt's random directory is eligible; an idempotency winner
            # already registered in SQLite (including another request) is never removed.
            self.store.discard_unregistered(session_id)

    async def create_session(self, request):
        """Freeze image evidence without requiring a model service or exposing source locations."""
        request_hash = fingerprint(request.model_dump(exclude={"request_id"}))
        return await asyncio.to_thread(self._snapshot_creation, request, request_hash)

    async def submit_turn(self, session_id, request):
        """Deduplicate before validation/scheduling so reconnects never repeat a paid operation."""
        request_hash = fingerprint(request.model_dump(exclude={"request_id", "expected_revision"}))
        existing = self.store.existing_turn(session_id, request.request_id, request_hash)
        if existing:
            return existing
        if self._closing or self.ai is None:
            raise ModelUnavailable(
                "AI model services are not configured; sessions and existing versions remain available"
            )
        try:
            self.ai.gateway.validate_environment()
        except ValueError as exc:
            raise ModelUnavailable("AI model service configuration is incomplete") from exc
        model_alias = request.model_alias or self.ai.gateway.default_image_alias
        if request.mode != "discuss" and model_alias not in self.ai.gateway.image_edits:
            raise ValueError("Unknown image model alias")
        turn_id, created = self.store.queue_turn(session_id, request, request_hash, model_alias)
        if created:
            worker = asyncio.create_task(self._run(session_id, turn_id), name=f"refinement-session-{session_id}")
            self._workers[session_id] = worker

            def finished(task):
                if self._workers.get(session_id) is task:
                    self._workers.pop(session_id, None)
                if not task.cancelled():
                    task.exception()

            worker.add_done_callback(finished)
        return self.store.get(session_id)

    async def wait_until_idle(self, session_id):
        """Join an already scheduled turn for batch backpressure; never submit or repeat model work."""
        worker = self._workers.get(session_id)
        if worker is not None:
            await asyncio.shield(worker)

    def _inputs(self, session_id, turn_id):
        session, turn, base_digest = self.store.inputs(session_id, turn_id)
        original = self.store.image(session_id, session["original_digest"])
        pixels = _count_evidence_pixels(0, original)
        current = self.store.image(session_id, base_digest)
        pixels = _count_evidence_pixels(pixels, current)
        people = json.loads(session["people"])
        references = {}
        for person in people:
            images = []
            for digest in person.pop("reference_digests"):
                image = self.store.image(session_id, digest)
                pixels = _count_evidence_pixels(pixels, image)
                images.append(image)
            references[person["person_id"]] = images
        detail = self.store.get(session_id)
        versions = {version["id"]: version for version in detail["versions"]}
        ancestry = set()
        version_id = turn["base_version_id"]
        while version_id is not None and version_id not in ancestry:
            ancestry.add(version_id)
            version_id = versions[version_id]["parent_version_id"]
        relevant = [
            previous
            for previous in detail["turns"]
            if previous["id"] != turn_id
            and (
                previous["candidate_version_id"] in ancestry
                or (previous["candidate_version_id"] is None and previous["base_version_id"] == turn["base_version_id"])
            )
        ]
        history = []
        for previous in relevant[-20:]:
            context = (
                "历史已执行编辑，不是本轮授权：" if previous["candidate_version_id"] else "历史讨论，不是本轮授权："
            )
            if previous["message"]:
                history.append({"role": "user", "content": context + previous["message"]})
            if previous["assistant_message"]:
                history.append({"role": "assistant", "content": context + previous["assistant_message"]})
        # Only the exact immutable base can supply prior findings. Keep the complete
        # structured diagnosis outside bounded prose so specific evidence is not lost.
        previous_diagnosis = next(
            (
                previous["diagnosis"]
                for previous in reversed(detail["turns"])
                if previous["id"] != turn_id
                and (not turn.get("diagnosis_turn_id") or previous["id"] == turn["diagnosis_turn_id"])
                and previous["base_version_id"] == turn["base_version_id"]
                and previous["status"] in {"completed", "review_required"}
                and previous["diagnosis"] is not None
            ),
            None,
        )
        return turn, original, current, people, references, history, previous_diagnosis

    def _transition(self, session_id, turn_id, **changes):
        if not self.store.update_turn(session_id, turn_id, **changes):
            raise asyncio.CancelledError

    def _register_generated(self, session_id, turn_id, candidate, metadata):
        digest = self.store.snapshot(session_id, candidate)
        return self.store.add_candidate(session_id, turn_id, digest, metadata, allow_cancel=True)

    async def _durable_candidate(self, session_id, turn_id, candidate, metadata):
        """Finish the short local write/register phase even if cancellation is requested.

        Shield alone is insufficient: a cancelled await would leave its write thread
        running and lose the registration. Drain this phase before making the turn idle.
        """
        phase = asyncio.create_task(
            asyncio.to_thread(self._register_generated, session_id, turn_id, candidate, metadata)
        )
        interrupted = False
        while not phase.done():
            try:
                await asyncio.shield(phase)
            except asyncio.CancelledError:
                interrupted = True
        return phase.result(), interrupted

    async def _run(self, session_id, turn_id):
        stage = "queued"
        candidate_id = None
        try:
            async with self._slots:
                turn, original, current, people, references, history, previous_diagnosis = await asyncio.to_thread(
                    self._inputs, session_id, turn_id
                )
                stage = "analysis"
                self._transition(session_id, turn_id, status="analyzing")
                async with asyncio.timeout(VISION_STAGE_TIMEOUT_SECONDS):
                    diagnosis = await self.ai.analyze(
                        original,
                        current,
                        people,
                        references,
                        history,
                        turn["message"],
                        previous_diagnosis=previous_diagnosis,
                    )
                self._transition(
                    session_id,
                    turn_id,
                    diagnosis=diagnosis.model_dump(mode="json"),
                    plan=diagnosis.edit_plan.model_dump(mode="json"),
                    assistant_message=diagnosis.assistant_message,
                )
                # Discussion cannot reach generation. Uncertain person assignment always
                # requires clarification, even when the user selected generate.
                if (
                    turn["mode"] == "discuss"
                    or diagnosis.mapping_uncertain
                    or not diagnosis.needs_edit
                    or (turn["mode"] == "auto" and not diagnosis.issues)
                ):
                    self._transition(session_id, turn_id, status="completed", terminal=True)
                    return
                stage = "generation"
                self._transition(session_id, turn_id, status="generating")
                async with asyncio.timeout(900):
                    candidate, metadata = await self.ai.generate(
                        current, people, diagnosis, turn["message"], model_alias=turn["model_alias"]
                    )
                metadata = {
                    **metadata,
                    "generation_policy": dict(self.ai.gateway.image_edits[turn["model_alias"]].generation),
                }
                # Core returns normalized whole-image pixels. Snapshot those directly; no
                # crop, mask, reference image or previous candidate enters this operation.
                candidate_id, interrupted = await self._durable_candidate(session_id, turn_id, candidate, metadata)
                if interrupted or candidate_id is None:
                    raise asyncio.CancelledError
                stage = "review"
                try:
                    async with asyncio.timeout(VISION_STAGE_TIMEOUT_SECONDS):
                        review = await self.ai.review(
                            original, current, candidate, people, references, diagnosis, turn["message"]
                        )
                    self._transition(
                        session_id,
                        turn_id,
                        status="review_required",
                        review=review.model_dump(mode="json"),
                        terminal=True,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._transition(
                        session_id,
                        turn_id,
                        status="review_required",
                        review_error=f"AI review failed{_failure_reason(exc)}; the generated candidate is preserved for human review",
                        terminal=True,
                    )
        except asyncio.CancelledError:
            if self._closing and candidate_id:
                self.store.update_turn(
                    session_id,
                    turn_id,
                    status="review_required",
                    review_error="Review interrupted by shutdown; candidate preserved",
                    terminal=True,
                    allow_cancel=True,
                )
            else:
                self.store.update_turn(
                    session_id,
                    turn_id,
                    status="failed" if self._closing else "cancelled",
                    error="Execution interrupted; no automatic retry was performed"
                    if self._closing
                    else "Cancelled; requests already sent upstream may still incur charges",
                    terminal=True,
                    allow_cancel=True,
                )
            raise
        except Exception as exc:
            # Never persist provider exception bodies, paths, credentials or image payloads.
            self.store.update_turn(
                session_id,
                turn_id,
                status="failed",
                error=f"Refinement {stage} failed{_failure_reason(exc)}; no model request was automatically repeated",
                terminal=True,
                allow_cancel=True,
            )

    async def cancel(self, session_id, expected_revision):
        turn_id = self.store.request_cancel(session_id, expected_revision)
        if turn_id:
            worker = self._workers.get(session_id)
            if worker is not None:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
            # A queued task cancelled before its first await never enters its cleanup.
            self.store.update_turn(
                session_id,
                turn_id,
                status="cancelled",
                error="Cancelled; upstream requests may still incur charges",
                terminal=True,
                allow_cancel=True,
            )
        return self.store.get(session_id)

    async def accept(self, session_id, request):
        return await asyncio.to_thread(
            self.store.accept, session_id, request.expected_revision, request.version_id, request.candidate_digest
        )

    async def shutdown(self):
        """Stop owned workers without scheduling retries; persisted state remains inspectable."""
        self._closing = True
        workers = list(self._workers.values())
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        # Queued coroutines may not have started; recovery marks these interrupted.
        await asyncio.to_thread(self.store.recover)
