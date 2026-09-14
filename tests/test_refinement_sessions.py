"""Offline integration of real SQLite, image snapshots, worker transitions and HTTP contracts."""

import asyncio
import hashlib
import json
import shutil
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import cv2
import httpx
import numpy as np
import pytest
from fastapi import FastAPI, Request
from pydantic import ValidationError

from api.media import encode_image_to_base64
from api.refinement.contracts import (
    AcceptRequest,
    ArtifactUnavailable,
    ModelUnavailable,
    SessionConflict,
    SessionCreateRequest,
    SessionDetail,
    SessionNotFound,
    SessionPersonInput,
    TurnCreateRequest,
)
from api.refinement.service import RefinementSessionService
from api.refinement.store import RefinementSessionStore, fingerprint
from api.routes.refinement_sessions import router, session_events
from core.refinement.contracts import CandidateReview, Diagnosis


def image(value):
    return np.full((32, 48, 3), value, dtype=np.uint8)


def uri(value):
    return encode_image_to_base64(image(value), ".png")


def creation(**overrides):
    values = {
        "request_id": uuid4().hex,
        "target_id": "target-a",
        "title": "合影精修",
        "original_image_url": uri(10),
        "baseline_image_url": uri(20),
        "people": [
            {"person_id": 7, "face_index": 2, "person_locator": "右侧穿蓝衣的人物", "source_manual_images": [uri(30)]}
        ],
    }
    values.update(overrides)
    return SessionCreateRequest(**values)


def turn(detail, mode="generate", message="Repair the visible eye edge", base=None, **overrides):
    values = {
        "request_id": uuid4().hex,
        "expected_revision": detail["revision"],
        "base_version_id": base or detail["baseline_version_id"],
        "mode": mode,
        "message": message,
    }
    values.update(overrides)
    return TurnCreateRequest(**values)


class ModelBoundary:
    """Only external AI is substituted; all application orchestration and I/O stay real."""

    def __init__(self, needs_edit=True, uncertain=False, failure=None):
        self.gateway = SimpleNamespace(
            default_image_alias="test-edit",
            image_edits={"test-edit": SimpleNamespace(generation={"size": "auto"})},
            validate_environment=lambda: None,
        )
        self.needs_edit, self.uncertain, self.failure = needs_edit, uncertain, failure
        self.analysis_calls, self.generated_inputs, self.review_calls = [], [], []
        self.previous_diagnoses = []
        self.generation_started, self.review_started = asyncio.Event(), asyncio.Event()
        self.generation_gate, self.review_gate = None, None

    async def analyze(self, original, current, people, references, history, user_message, *, previous_diagnosis=None):
        self.previous_diagnoses.append(previous_diagnosis)
        self.analysis_calls.append((original.copy(), current.copy(), people, references, history, user_message))
        if self.failure == "analysis":
            raise RuntimeError("SECRET https://private.provider/v1?api_key=secret")
        assessments = [
            {
                "person_id": person["person_id"],
                "person_locator": person["person_locator"],
                "mapping_confident": not self.uncertain,
                "identity_status": "uncertain" if self.uncertain else "unavailable",
                "evidence": "The target is visible on the right side",
                "uncertainty": "Identity cannot be established from this view",
            }
            for person in people
        ]
        return Diagnosis.model_validate(
            {
                "assistant_message": "Please inspect the eye boundary"
                if self.needs_edit
                else "No actionable defect was found",
                "needs_edit": self.needs_edit,
                "mapping_uncertain": self.uncertain,
                "people": assessments,
                "issues": [
                    {
                        "category": "blending",
                        "person_id": people[0]["person_id"],
                        "region": "right eye edge",
                        "description": "The eye border contains a duplicated dark line",
                        "evidence": "The current image has two edges where the original has one",
                        "suggestion": "Remove the extra eye border while retaining the eyelid",
                        "severity": "medium",
                        "confidence": "high",
                    }
                ]
                if self.needs_edit
                else [],
                "edit_plan": {
                    "changes": ["Repair the duplicated eye boundary"] if self.needs_edit else [],
                    "preserve": ["Preserve the scene and identity"],
                    "instructions": "Repair the visible eye border" if self.needs_edit else "",
                },
            }
        )

    async def generate(self, current, people, diagnosis, user_message, model_alias=None):
        self.generated_inputs.append(current.copy())
        self.generation_started.set()
        if self.generation_gate:
            await self.generation_gate.wait()
        if self.failure == "generation":
            raise RuntimeError("SECRET https://private.provider/v1?api_key=secret")
        return current + np.uint8(1), {
            "model": "actual-upstream-model",
            "model_alias": model_alias,
            "provider": "litellm",
            "request_id": "upstream-request-42",
            "usage": {"input_tokens": 8, "output_tokens": 2, "private_body": "SECRET"},
            "prompt_version": "test-prompt-v1",
            "prompt": "private composed prompt",
            "generation_mode": "whole-image-single-call",
            "input_image_count": 1,
            "requested_output_size": None,
            "provider_output_size": [48, 32],
            "target_canvas_size": [48, 32],
            "canvas_normalization": "resize-only",
            "api_key": "SECRET",
        }

    async def review(self, original, before, candidate, people, references, diagnosis, user_message):
        self.review_calls.append((original.copy(), before.copy(), candidate.copy(), references))
        self.review_started.set()
        if self.review_gate:
            await self.review_gate.wait()
        if self.failure == "review":
            raise RuntimeError("SECRET https://private.provider/v1?api_key=secret")
        return CandidateReview(summary="Candidate retained for human inspection")


@pytest.fixture
def store(tmp_path):
    return RefinementSessionStore(tmp_path / "state.sqlite3", tmp_path / "private", tmp_path / "public")


async def settle(service, session_id):
    worker = service._workers.get(session_id)
    if worker:
        await asyncio.wait_for(worker, timeout=5)
    return service.get(session_id)


async def generate(service, detail, **kwargs):
    await service.submit_turn(detail["id"], turn(detail, **kwargs))
    return await settle(service, detail["id"])


def test_snapshot_create_idempotency_and_offline_restore(store):
    async def scenario():
        service = RefinementSessionService(store=store)
        command = creation()
        detail = await service.create_session(command)
        assert SessionDetail.model_validate(detail).people[0].reference_status == "available"
        assert detail["people"][0]["person_id"] == 7
        assert await service.create_session(command) == detail
        with pytest.raises(SessionConflict):
            await service.create_session(command.model_copy(update={"title": "different"}))
        reloaded = RefinementSessionStore(store.db_path, store.artifact_root, store.accepted_root)
        assert reloaded.get(detail["id"]) == detail
        assert len(reloaded.list()) == 1
        assert "data:image" not in json.dumps(detail)
        assert str(store.artifact_root) not in json.dumps(detail)
        reference_digest = detail["people"][0]["reference_urls"][0].split("/")[-1]
        np.testing.assert_array_equal(reloaded.image(detail["id"], reference_digest), image(30))

    asyncio.run(scenario())


@pytest.mark.parametrize("reference_groups", [[0], [1], [1, 1], [2]])
def test_creation_pixel_budget_counts_all_evidence_and_cleans_failed_attempt(store, monkeypatch, reference_groups):
    async def scenario():
        service = RefinementSessionService(store=store)
        existing = await service.create_session(creation())
        existing_dirs = set(store.artifact_root.iterdir())
        people = [
            {
                "person_id": index + 1,
                "face_index": index + 1,
                "source_manual_images": [uri(30)] * count,
            }
            for index, count in enumerate(reference_groups)
        ]
        command = creation(people=people)
        required = (2 + sum(reference_groups)) * 32 * 48
        monkeypatch.setattr("api.refinement.service.MAX_EVIDENCE_PIXELS", required - 1)
        with pytest.raises(ValueError, match="decoded pixel budget"):
            await service.create_session(command)
        assert len(store.list()) == 1
        assert store.get(existing["id"]) == existing
        assert set(store.artifact_root.iterdir()) == existing_dirs

        # A rejected request must not consume its idempotency key; the exact boundary is legal.
        monkeypatch.setattr("api.refinement.service.MAX_EVIDENCE_PIXELS", required)
        detail = await service.create_session(command)
        assert await service.create_session(command) == detail
        assert len(store.list()) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("reference_count", [0, 1])
def test_turn_rechecks_pixel_budget_including_canvases_without_references(store, monkeypatch, reference_count):
    async def scenario():
        service = RefinementSessionService(store=store)
        detail = await service.create_session(
            creation(people=[{"person_id": 1, "face_index": 1, "source_manual_images": [uri(30)] * reference_count}])
        )
        request = turn(detail)
        turn_id, _ = store.queue_turn(
            detail["id"],
            request,
            fingerprint(request.model_dump(exclude={"request_id", "expected_revision"})),
            "test-edit",
        )
        monkeypatch.setattr("api.refinement.service.MAX_EVIDENCE_PIXELS", (2 + reference_count) * 32 * 48 - 1)
        with pytest.raises(ValueError, match="decoded pixel budget"):
            service._inputs(detail["id"], turn_id)

    asyncio.run(scenario())


def test_missing_references_are_explicit_and_mixed_explicit_sources_are_legal():
    person = SessionPersonInput(person_id=1, face_index=1, source_asset_ids=["opaque"], source_manual_images=[uri(20)])
    assert len(person.source_asset_ids) == 1
    with pytest.raises(ValidationError):
        SessionPersonInput(person_id=1, face_index=1, source_identity_id="identity", source_manual_images=[uri(20)])
    with pytest.raises(ValidationError):
        SessionPersonInput(
            person_id=1, face_index=1, source_asset_ids=[str(i) for i in range(8)], source_manual_images=[uri(20)]
        )


def test_reference_id_paths_are_snapshotted_and_do_not_drift(store, monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / "managed.png"
        cv2.imwrite(str(path), image(30))
        monkeypatch.setattr("api.refinement.service.resolve_asset_ids", lambda ids: [path] if ids else [])
        service = RefinementSessionService(store=store)
        detail = await service.create_session(
            creation(
                people=[
                    {"person_id": 7, "face_index": 2, "source_asset_ids": ["asset"], "source_manual_images": [uri(40)]}
                ]
            )
        )
        cv2.imwrite(str(path), image(99))
        references = detail["people"][0]["reference_urls"]
        assert len(references) == 2
        np.testing.assert_array_equal(store.image(detail["id"], references[0].split("/")[-1]), image(30))
        np.testing.assert_array_equal(store.image(detail["id"], references[1].split("/")[-1]), image(40))

    asyncio.run(scenario())


@pytest.mark.parametrize("source", ["/etc/passwd", "../outside.png", "file:///etc/passwd", "http://127.0.0.1/private"])
def test_untrusted_file_or_private_network_sources_are_rejected(store, source):
    async def scenario():
        service = RefinementSessionService(store=store)
        with pytest.raises(ValueError):
            await service.create_session(creation(original_image_url=source))
        assert store.list() == []

    asyncio.run(scenario())


def test_generation_branching_and_prompt_audit_survive_restart(store):
    async def scenario():
        ai = ModelBoundary()
        service = RefinementSessionService(ai=ai, store=store)
        detail = await service.create_session(creation())
        first = await generate(service, detail, message="first applied edit")
        first_id = first["versions"][-1]["id"]
        second = await generate(service, first, base=first_id, message="second applied edit")
        second_id = second["versions"][-1]["id"]
        unrelated = await generate(service, second, message="unrelated branch edit")
        discussed = await generate(
            service, unrelated, base=first_id, mode="discuss", message="discussion on selected base"
        )
        final = await generate(service, discussed, base=first_id, message="new current edit")
        assert final["versions"][-1]["parent_version_id"] == first_id
        assert final["versions"][-1]["parent_version_id"] != second_id
        np.testing.assert_array_equal(ai.generated_inputs[-1], image(21))
        history = json.dumps(ai.analysis_calls[-1][4], ensure_ascii=False)
        assert "first applied edit" in history and "discussion on selected base" in history
        assert "second applied edit" not in history and "unrelated branch edit" not in history
        assert "不是本轮授权" in history
        assert ai.previous_diagnoses[-1] == discussed["turns"][-1]["diagnosis"]
        # A new candidate base has no diagnosis yet; its parent's findings cannot be reused.
        assert ai.previous_diagnoses[1] is None
        audit = final["turns"][-1]["generation_metadata"]
        assert audit["input_image_count"] == 1 and audit["request_id"] == "upstream-request-42"
        assert audit["prompt_digest"] == hashlib.sha256(b"private composed prompt").hexdigest()
        assert audit["usage"] == {"input_tokens": 8, "output_tokens": 2}
        assert "private composed prompt" not in json.dumps(final) and "SECRET" not in json.dumps(final)
        with store._connect() as conn:
            raw = json.loads(
                conn.execute("SELECT data FROM turns WHERE id=?", (final["turns"][-1]["id"],)).fetchone()[0]
            )
        assert raw["generation_prompt"] == "private composed prompt"
        assert len(list(store.accepted_root.glob("*.png"))) == 0
        restarted = RefinementSessionStore(store.db_path, store.artifact_root, store.accepted_root)
        assert restarted.get(final["id"])["turns"][-1]["generation_metadata"] == audit

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode,needs_edit,uncertain",
    [("discuss", True, False), ("auto", False, False), ("generate", False, False), ("generate", True, True)],
)
def test_discussion_noop_and_uncertain_mapping_never_generate(store, mode, needs_edit, uncertain):
    async def scenario():
        ai = ModelBoundary(needs_edit=needs_edit, uncertain=uncertain)
        service = RefinementSessionService(ai=ai, store=store)
        detail = await service.create_session(creation())
        result = await generate(service, detail, mode=mode)
        assert not ai.generated_inputs and not ai.review_calls
        assert len(result["versions"]) == 1
        assert result["status"] == "idle"
        assert result["turns"][-1]["status"] == "completed"
        assert result["turns"][-1]["message"] == "Repair the visible eye edge"

    asyncio.run(scenario())


def test_turn_idempotency_busy_revision_and_wrong_parent(store):
    async def scenario():
        ai = ModelBoundary()
        ai.generation_gate = asyncio.Event()
        service = RefinementSessionService(ai=ai, store=store)
        detail = await service.create_session(creation())
        command = turn(detail)
        first, repeated = await asyncio.gather(
            service.submit_turn(detail["id"], command), service.submit_turn(detail["id"], command)
        )
        assert first["active_turn_id"] == repeated["active_turn_id"]
        with pytest.raises(SessionConflict):
            await service.submit_turn(detail["id"], command.model_copy(update={"message": "changed"}))
        with pytest.raises(SessionConflict):
            await service.submit_turn(detail["id"], turn(service.get(detail["id"])))
        await asyncio.wait_for(ai.generation_started.wait(), 3)
        assert len(ai.generated_inputs) == 1
        ai.generation_gate.set()
        done = await settle(service, detail["id"])
        service.ai = None
        assert (await service.submit_turn(detail["id"], command))["revision"] == done["revision"]
        with pytest.raises(ModelUnavailable):
            await service.submit_turn(detail["id"], turn(done))
        service.ai = ai
        with pytest.raises(SessionConflict):
            await service.submit_turn(detail["id"], turn(detail))
        with pytest.raises(SessionNotFound):
            await service.submit_turn(detail["id"], turn(done, base=uuid4().hex))

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["analysis", "generation", "review"])
def test_safe_errors_and_review_failure_preserves_candidate(store, failure):
    async def scenario():
        ai = ModelBoundary(failure=failure)
        service = RefinementSessionService(ai=ai, store=store)
        detail = await service.create_session(creation())
        result = await generate(service, detail)
        assert "SECRET" not in json.dumps(result) and "private.provider" not in json.dumps(result)
        if failure == "review":
            assert result["turns"][-1]["status"] == "review_required"
            assert result["turns"][-1]["review_error"]
            assert len(result["versions"]) == 2
            version = result["versions"][-1]
            service.ai = None
            accepted = await service.accept(
                result["id"],
                AcceptRequest(
                    expected_revision=result["revision"], version_id=version["id"], candidate_digest=version["digest"]
                ),
            )
            assert accepted["accepted_version_id"] == version["id"]
        else:
            assert result["turns"][-1]["status"] == "failed" and len(result["versions"]) == 1

    asyncio.run(scenario())


def test_cancel_inflight_and_queued_session_keep_evidence(store):
    async def scenario():
        ai = ModelBoundary()
        ai.generation_gate = asyncio.Event()
        service = RefinementSessionService(ai=ai, store=store)
        first = await service.create_session(creation())
        second = await service.create_session(creation(target_id="second"))
        await service.submit_turn(first["id"], turn(first))
        await asyncio.wait_for(ai.generation_started.wait(), 3)
        queued = await service.submit_turn(second["id"], turn(second))
        cancelled_queue = await service.cancel(second["id"], queued["revision"])
        assert cancelled_queue["turns"][-1]["status"] == "cancelled"
        active = service.get(first["id"])
        cancelled = await service.cancel(first["id"], active["revision"])
        assert cancelled["status"] == "idle" and cancelled["turns"][-1]["status"] == "cancelled"
        assert len(cancelled["versions"]) == 1 and len(ai.generated_inputs) == 1
        assert not list(store.accepted_root.glob("*.png"))
        assert (await service.cancel(first["id"], cancelled["revision"]))["revision"] == cancelled["revision"]

    asyncio.run(scenario())


def test_cancel_during_review_keeps_generated_version_acceptable(store):
    async def scenario():
        ai = ModelBoundary()
        ai.review_gate = asyncio.Event()
        service = RefinementSessionService(ai=ai, store=store)
        detail = await service.create_session(creation())
        await service.submit_turn(detail["id"], turn(detail))
        await asyncio.wait_for(ai.review_started.wait(), 3)
        active = service.get(detail["id"])
        version = active["versions"][-1]
        cancelled = await service.cancel(detail["id"], active["revision"])
        assert cancelled["turns"][-1]["candidate_version_id"] == version["id"]
        accepted = await service.accept(
            detail["id"],
            AcceptRequest(
                expected_revision=cancelled["revision"], version_id=version["id"], candidate_digest=version["digest"]
            ),
        )
        assert accepted["accepted_version_id"] == version["id"]

    asyncio.run(scenario())


@pytest.mark.parametrize("has_candidate", [False, True])
def test_restart_recovers_without_repeating_paid_operations(store, has_candidate):
    async def scenario():
        detail = await RefinementSessionService(store=store).create_session(creation())
        command = turn(detail)
        key = fingerprint(command.model_dump(exclude={"request_id", "expected_revision"}))
        turn_id, _ = store.queue_turn(detail["id"], command, key, "test-edit")
        if has_candidate:
            digest = store.snapshot(detail["id"], image(21))
            store.add_candidate(detail["id"], turn_id, digest, {"model": "model"})
        restored = RefinementSessionStore(store.db_path, store.artifact_root, store.accepted_root)
        snapshot = restored.get(detail["id"])
        assert snapshot["status"] == "idle" and snapshot["active_turn_id"] is None
        assert snapshot["turns"][-1]["status"] == ("review_required" if has_candidate else "failed")
        assert snapshot["revision"] > detail["revision"]
        offline = RefinementSessionService(store=restored)
        assert await offline.submit_turn(detail["id"], command) == snapshot

    asyncio.run(scenario())


def test_offline_accept_exact_bytes_revision_and_digest_guards(store):
    async def scenario():
        service = RefinementSessionService(store=store)
        detail = await service.create_session(creation())
        version = detail["versions"][0]
        command = AcceptRequest(
            expected_revision=detail["revision"], version_id=version["id"], candidate_digest=version["digest"]
        )
        with pytest.raises(SessionConflict):
            await service.accept(detail["id"], command.model_copy(update={"expected_revision": 99}))
        with pytest.raises(SessionConflict):
            await service.accept(detail["id"], command.model_copy(update={"candidate_digest": "0" * 64}))
        accepted = await service.accept(detail["id"], command)
        expected = store.artifact_bytes(detail["id"], version["digest"])
        assert (store.accepted_root / f"{detail['id']}-{version['id']}.png").read_bytes() == expected
        assert await service.accept(detail["id"], command) == accepted

    asyncio.run(scenario())


def test_corruption_and_cross_session_access_are_rejected(store):
    async def scenario():
        service = RefinementSessionService(store=store)
        first = await service.create_session(creation())
        second = await service.create_session(creation(original_image_url=uri(40), baseline_image_url=uri(50)))
        version = first["versions"][0]
        with pytest.raises(SessionNotFound):
            service.artifact_bytes(second["id"], version["digest"])
        with pytest.raises(SessionNotFound):
            service.artifact_bytes("../outside", version["digest"])
        with pytest.raises(SessionNotFound):
            await service.accept(
                second["id"],
                AcceptRequest(
                    expected_revision=second["revision"], version_id=version["id"], candidate_digest=version["digest"]
                ),
            )
        path = store.artifact_root / first["id"] / f"{version['digest']}.png"
        path.write_bytes(b"tampered")
        with pytest.raises(ArtifactUnavailable):
            service.artifact_bytes(first["id"], version["digest"])
        with pytest.raises(ArtifactUnavailable):
            await service.accept(
                first["id"],
                AcceptRequest(
                    expected_revision=first["revision"], version_id=version["id"], candidate_digest=version["digest"]
                ),
            )
        assert service.get(first["id"])["accepted_version_id"] is None
        assert not list(store.accepted_root.glob("*.png"))

    asyncio.run(scenario())


def test_publication_rolls_back_and_crash_orphans_are_removed(store, monkeypatch):
    async def scenario():
        service = RefinementSessionService(store=store)
        detail = await service.create_session(creation())
        version = detail["versions"][0]

        def fail(*args):
            raise OSError("disk write failed")

        with monkeypatch.context() as context:
            context.setattr(store, "_touch", fail)
            with pytest.raises(OSError):
                await service.accept(
                    detail["id"],
                    AcceptRequest(
                        expected_revision=detail["revision"],
                        version_id=version["id"],
                        candidate_digest=version["digest"],
                    ),
                )
        assert not list(store.accepted_root.glob("*.png"))
        assert service.get(detail["id"])["revision"] == detail["revision"]
        orphan = store.accepted_root / f"{detail['id']}-{version['id']}.png"
        orphan.write_bytes(store.artifact_bytes(detail["id"], version["digest"]))
        store.recover()
        assert not orphan.exists()

    asyncio.run(scenario())


def test_http_offline_browsing_artifacts_and_bounded_summary(store):
    async def scenario():
        app = FastAPI()
        app.state.refinement_sessions = RefinementSessionService(store=store)
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/refinement/sessions", json=creation().model_dump())
            assert response.status_code == 200, response.text
            detail = response.json()["data"]
            root = f"/api/v1/refinement/sessions/{detail['id']}"
            assert (await client.get(root)).json()["data"] == detail
            summaries = await client.get("/api/v1/refinement/sessions?limit=1&offset=0")
            assert len(summaries.json()["data"]) == 1
            assert all(
                field not in json.dumps(summaries.json())
                for field in ("image_url", "reference_urls", "turns", "data:image", "diagnosis")
            )
            assert (await client.get("/api/v1/refinement/sessions?limit=101")).status_code == 422
            model_response = await client.post(root + "/turns", json=turn(detail).model_dump())
            assert model_response.status_code == 503
            artifact = await client.get(detail["versions"][0]["image_url"])
            assert artifact.status_code == 200 and artifact.headers["content-type"] == "image/png"
            assert hashlib.sha256(artifact.content).hexdigest() == detail["versions"][0]["digest"]
            assert (await client.get(root + "/events", headers={"Last-Event-ID": "invalid"})).status_code == 422
            assert (await client.get(root + "/artifacts/" + "0" * 64)).status_code == 404

    asyncio.run(scenario())


def test_sse_uses_persisted_cursor_and_contains_no_image_or_chat_payload(store):
    async def scenario():
        service = RefinementSessionService(store=store)
        detail = await service.create_session(creation())
        app = FastAPI()
        app.state.refinement_sessions = service

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request({"type": "http", "app": app, "headers": [], "method": "GET", "path": "/"}, receive)
        response = await session_events(detail["id"], request, after=0, last_event_id=None)
        event = await anext(response.body_iterator)
        await response.body_iterator.aclose()
        assert "event: snapshot" in event and f"id: {detail['event_cursor']}" in event
        assert "image" not in event and "message" not in event
        assert store.events(detail["id"], detail["event_cursor"]) == []
        version = detail["versions"][0]
        accepted = await service.accept(
            detail["id"],
            AcceptRequest(
                expected_revision=detail["revision"], version_id=version["id"], candidate_digest=version["digest"]
            ),
        )
        resumed = store.events(detail["id"], detail["event_cursor"])
        assert len(resumed) == 1 and resumed[0]["revision"] == accepted["revision"]

    asyncio.run(scenario())


@pytest.mark.parametrize("missing_request", [False, True])
def test_known_batch_rejects_fake_original_before_any_source_loading(store, monkeypatch, tmp_path, missing_request):
    async def scenario():
        import core.image_security as security

        monkeypatch.setitem(security._MANAGED_URL_ROOTS, "/api/v1/files/", tmp_path)
        app = FastAPI()
        service = RefinementSessionService(store=store)
        app.state.refinement_sessions = service
        app.include_router(router)
        task_id = uuid4().hex
        result = SimpleNamespace(
            status="SUCCESS",
            processed_url="/api/v1/files/processed.png",
            original_url="/api/v1/files/original.png",
            client_target_id="target-a",
            model_routing={},
        )
        saved_request = SimpleNamespace(
            person_mappings=[],
            source_identity_id=None,
            source_asset_ids=[],
            source_manual_images=[],
            parameters=SimpleNamespace(model_dump=lambda **kwargs: {}),
        )
        app.state.task_manager = SimpleNamespace(
            tasks={task_id: SimpleNamespace(task_id=task_id, results=[result])},
            _requests={} if missing_request else {task_id: saved_request},
        )

        def forbidden(*args):
            raise AssertionError("Untrusted source loaded before canonical original validation")

        monkeypatch.setattr(service, "_snapshot_creation", forbidden)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            command = creation(baseline_image_url=result.processed_url)
            response = await client.post("/api/v1/refinement/sessions", json=command.model_dump())
            assert response.status_code == 422, response.text
            assert "immutable batch-result" in response.text
            command = command.model_copy(update={"original_image_url": result.original_url})
            response = await client.post("/api/v1/refinement/sessions", json=command.model_dump())
            assert response.status_code == 404, response.text
            cv2.imwrite(str(tmp_path / "processed.png"), image(20))
            alias = "http://localhost:9999/api/v1/files/pr%6Fcessed.png"
            response = await client.post(
                "/api/v1/refinement/sessions", json=creation(baseline_image_url=alias).model_dump()
            )
            assert response.status_code == 422, response.text
            result.original_url = None
            response = await client.post("/api/v1/refinement/sessions", json=command.model_dump())
            assert response.status_code == 404

    asyncio.run(scenario())


def test_api_input_limits_share_core_constants_and_reject_not_truncate():
    from core.refinement.contracts import MAX_LOCATOR_CHARS, MAX_MESSAGE_CHARS, MAX_PEOPLE

    assert SessionPersonInput.model_fields["person_locator"].metadata[0].max_length == MAX_LOCATOR_CHARS
    with pytest.raises(ValidationError):
        SessionPersonInput(person_id=1, face_index=1, person_locator="x" * (MAX_LOCATOR_CHARS + 1))
    with pytest.raises(ValidationError):
        creation(people=[{"person_id": i + 1, "face_index": i + 1} for i in range(MAX_PEOPLE + 1)])
    with pytest.raises(ValidationError):
        TurnCreateRequest(
            request_id="too-long",
            expected_revision=1,
            base_version_id=uuid4().hex,
            mode="generate",
            message="x" * (MAX_MESSAGE_CHARS + 1),
        )


def test_artifact_symlink_cannot_escape_managed_root(store, tmp_path):
    async def scenario():
        service = RefinementSessionService(store=store)
        detail = await service.create_session(creation())
        version = detail["versions"][0]
        path = store.artifact_root / detail["id"] / (version["digest"] + ".png")
        outside = tmp_path / "outside.png"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        with pytest.raises(ArtifactUnavailable):
            service.artifact_bytes(detail["id"], version["digest"])

    asyncio.run(scenario())


def test_rolled_back_publisher_cannot_delete_successful_concurrent_retry(store, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager

    detail = asyncio.run(RefinementSessionService(store=store).create_session(creation()))
    version = detail["versions"][0]
    args = (detail["id"], detail["revision"], version["id"], version["digest"])
    rolled_back, allow_cleanup = threading.Event(), threading.Event()
    original_connect, original_touch = store._connect, store._touch

    @contextmanager
    def delayed_rollback(write=False):
        try:
            with original_connect(write=write) as connection:
                yield connection
        except OSError:
            if threading.current_thread().name == "failing-publisher":
                rolled_back.set()
                assert allow_cleanup.wait(5)
            raise

    def fail_first(connection, session_id, kind):
        if threading.current_thread().name == "failing-publisher":
            raise OSError("injected transaction failure after publication")
        original_touch(connection, session_id, kind)

    monkeypatch.setattr(store, "_connect", delayed_rollback)
    monkeypatch.setattr(store, "_touch", fail_first)

    def first_accept():
        threading.current_thread().name = "failing-publisher"
        return store.accept(*args)

    with ThreadPoolExecutor(max_workers=2) as pool:
        failed = pool.submit(first_accept)
        try:
            assert rolled_back.wait(5)
            succeeded = pool.submit(store.accept, *args).result(timeout=5)
            assert succeeded["accepted_version_id"] == version["id"]
        finally:
            allow_cleanup.set()
        with pytest.raises(OSError):
            failed.result(timeout=5)
    published = store.accepted_root / (detail["id"] + "-" + version["id"] + ".png")
    assert hashlib.sha256(published.read_bytes()).hexdigest() == version["digest"]
    assert store.get(detail["id"])["accepted_version_id"] == version["id"]


@pytest.mark.parametrize("shutdown", [True, False])
def test_generated_candidate_registration_drains_before_stop(store, monkeypatch, shutdown):
    import threading

    async def scenario():
        ai = ModelBoundary()
        service = RefinementSessionService(ai=ai, store=store)
        detail = await service.create_session(creation())
        writing, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original_snapshot = store.snapshot

        def slow_snapshot(session_id, value):
            if np.all(value == 21):
                loop.call_soon_threadsafe(writing.set)
                assert release.wait(5)
            return original_snapshot(session_id, value)

        monkeypatch.setattr(store, "snapshot", slow_snapshot)
        await service.submit_turn(detail["id"], turn(detail))
        await asyncio.wait_for(writing.wait(), 3)
        current = service.get(detail["id"])
        operation = service.shutdown() if shutdown else service.cancel(detail["id"], current["revision"])
        stopping = asyncio.create_task(operation)
        await asyncio.sleep(0)  # Deliver cancellation while the file-writing thread is held.
        assert not stopping.done()
        release.set()
        await asyncio.wait_for(stopping, 5)
        restored = RefinementSessionStore(store.db_path, store.artifact_root, store.accepted_root).get(detail["id"])
        assert len(restored["versions"]) == 2
        assert restored["turns"][-1]["candidate_version_id"] == restored["versions"][-1]["id"]
        assert restored["turns"][-1]["status"] == ("review_required" if shutdown else "cancelled")
        assert len(ai.generated_inputs) == 1 and not ai.review_calls

    asyncio.run(scenario())


def test_concurrent_create_and_bad_reference_leave_no_private_orphans(store, monkeypatch):
    import threading

    async def scenario():
        service = RefinementSessionService(store=store)
        with pytest.raises(ValueError):
            await service.create_session(
                creation(people=[{"person_id": 1, "face_index": 1, "source_manual_images": ["not-an-image"]}])
            )
        assert not list(store.artifact_root.iterdir())
        barrier = threading.Barrier(2)
        original_create = store.create

        def together(*args):
            barrier.wait(timeout=5)
            return original_create(*args)

        monkeypatch.setattr(store, "create", together)
        command = creation()
        first, second = await asyncio.gather(service.create_session(command), service.create_session(command))
        assert first["id"] == second["id"]
        assert [path.name for path in store.artifact_root.iterdir()] == [first["id"]]

    asyncio.run(scenario())


def test_missing_locator_uses_original_binding_without_reordering_ids(store):
    async def scenario():
        ai = ModelBoundary()
        service = RefinementSessionService(ai=ai, store=store)
        detail = await service.create_session(
            creation(
                people=[
                    {"person_id": 7, "face_index": 2, "source_manual_images": [uri(30)]},
                    {"person_id": 3, "face_index": 1, "source_manual_images": [uri(40)]},
                ]
            )
        )
        await generate(service, detail)
        people, references = ai.analysis_calls[0][2:4]
        assert [person["person_id"] for person in people] == [7, 3]
        assert people[0]["person_locator"] == "原图中从左到右第2位人物"
        assert people[1]["person_locator"] == "原图中从左到右第1位人物"
        np.testing.assert_array_equal(references[7][0], image(30))
        np.testing.assert_array_equal(references[3][0], image(40))
        np.testing.assert_array_equal(ai.generated_inputs[0], image(20))

    asyncio.run(scenario())


def test_real_core_service_integrates_with_persistent_worker_without_paid_calls(store):
    from api.media import load_image_from_source
    from core.refinement.conversation import ConversationRefinementService

    async def scenario():
        decision = await ModelBoundary().analyze(
            image(10),
            image(20),
            [{"person_id": 7, "person_locator": "右侧穿蓝衣的人物"}],
            {7: [image(30)]},
            [],
            "修复眼缘",
        )
        calls = []

        class OfflineGateway:
            default_image_alias = "test-edit"
            image_edits = {"test-edit": SimpleNamespace(generation={"size": "auto"})}

            def validate_environment(self):
                return None

            def vision_alias(self, role):
                return "test-vision"

            async def structured_vision_completion(self, messages, schema, **kwargs):
                assert kwargs["max_text_repairs"] == 0
                calls.append(("vision", messages))
                result = (
                    decision
                    if schema is Diagnosis
                    else CandidateReview(summary="Candidate ready for human review", people=decision.people)
                )
                return result.model_dump_json(), result

            async def edit_image(self, images, prompt, *, alias, parameters):
                assert len(images) == 1
                np.testing.assert_array_equal(load_image_from_source(images[0]), image(20))
                assert alias == "test-edit" and parameters == {"size": "auto"}
                calls.append(("image", prompt))
                ok, encoded = cv2.imencode(".png", image(21))
                assert ok
                return SimpleNamespace(
                    image_bytes=encoded.tobytes(),
                    model="actual-native-model",
                    provider="litellm",
                    request_id="native-request",
                    usage={"input_tokens": 9},
                )

        service = RefinementSessionService(ai=ConversationRefinementService(OfflineGateway()), store=store)
        detail = await service.create_session(creation())
        result = await generate(service, detail, mode="auto")
        assert result["turns"][-1]["status"] == "review_required", result["turns"][-1]
        assert [kind for kind, _ in calls] == ["vision", "image", "vision"]
        assert len([part for part in calls[0][1][1]["content"] if part["type"] == "image_url"]) == 3
        assert len([part for part in calls[-1][1][1]["content"] if part["type"] == "image_url"]) == 4
        assert result["turns"][-1]["generation_metadata"]["generation_policy"] == {"size": "auto"}
        assert result["turns"][-1]["generation_metadata"]["request_id"] == "native-request"
        assert result["accepted_version_id"] is None
        np.testing.assert_array_equal(store.image(result["id"], result["versions"][-1]["digest"]), image(21))

    asyncio.run(scenario())


def test_delete_session_removes_versions_publications_and_database_rows_without_ai(store):
    async def scenario():
        service = RefinementSessionService(store=store)
        detail = await service.create_session(creation())
        other = await service.create_session(creation(target_id="keep"))
        version = detail["versions"][0]
        accepted = await service.accept(
            detail["id"],
            AcceptRequest(
                expected_revision=detail["revision"], version_id=version["id"], candidate_digest=version["digest"]
            ),
        )
        await service.delete(detail["id"], accepted["revision"])
        assert not (store.artifact_root / detail["id"]).exists()
        assert not list(store.accepted_root.glob(f"{detail['id']}-*.png"))
        with store._connect() as conn:
            for table in ("versions", "turns", "artifacts", "publications", "events"):
                assert (
                    conn.execute(f"SELECT count(*) FROM {table} WHERE session_id=?", (detail["id"],)).fetchone()[0] == 0
                )
        restarted = RefinementSessionStore(store.db_path, store.artifact_root, store.accepted_root)
        assert [item["id"] for item in restarted.list()] == [other["id"]]
        assert restarted.image(other["id"], other["versions"][0]["digest"]).size
        with pytest.raises(SessionNotFound):
            await service.delete(detail["id"], accepted["revision"])

    asyncio.run(scenario())


def test_delete_session_restores_images_and_rows_on_database_failure(store):
    async def scenario():
        service = RefinementSessionService(store=store)
        detail = await service.create_session(creation())
        with store._connect() as conn:
            conn.execute("CREATE TRIGGER block_delete BEFORE DELETE ON sessions BEGIN SELECT RAISE(ABORT, 'test'); END")
        with pytest.raises(sqlite3.IntegrityError):
            await service.delete(detail["id"], detail["revision"])
        assert service.get(detail["id"]) == detail
        assert store.image(detail["id"], detail["versions"][0]["digest"]).size

    asyncio.run(scenario())


@pytest.mark.parametrize("files", ["missing", "symlink"])
def test_delete_session_tolerates_missing_files_and_never_follows_directory_symlinks(store, tmp_path, files):
    async def scenario():
        service = RefinementSessionService(store=store)
        detail = await service.create_session(creation())
        directory = store.artifact_root / detail["id"]
        shutil.rmtree(directory)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        if files == "symlink":
            directory.symlink_to(outside, target_is_directory=True)
        await service.delete(detail["id"], detail["revision"])
        assert (outside / "keep.txt").read_text() == "keep"
        assert not directory.is_symlink()

    asyncio.run(scenario())


def test_delete_http_checks_revision_running_status_and_artifact_unavailability(store):
    async def scenario():
        service = RefinementSessionService(store=store)
        app = FastAPI()
        app.state.refinement_sessions = service
        app.include_router(router)
        detail = await service.create_session(creation())
        url = f"/api/v1/refinement/sessions/{detail['id']}"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.delete(url)).status_code == 422
            assert (await client.delete(url, params={"expected_revision": 999})).status_code == 409
            command = turn(detail)
            store.queue_turn(detail["id"], command, fingerprint(command.model_dump()), "test")
            running = service.get(detail["id"])
            assert (await client.delete(url, params={"expected_revision": running["revision"]})).status_code == 409
            store.recover()
            idle = service.get(detail["id"])
            assert (await client.delete(url, params={"expected_revision": idle["revision"]})).status_code == 204
            assert (await client.get(url)).status_code == 404
            assert (await client.get(detail["versions"][0]["image_url"])).status_code == 404

    asyncio.run(scenario())


def test_sse_closes_with_deleted_notification_when_record_is_removed(store):
    async def scenario():
        service = RefinementSessionService(store=store)
        detail = await service.create_session(creation())
        app = FastAPI()
        app.state.refinement_sessions = service

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request({"type": "http", "app": app, "headers": [], "method": "GET", "path": "/"}, receive)
        response = await session_events(detail["id"], request, after=0, last_event_id=None)
        await service.delete(detail["id"], detail["revision"])
        assert "event: deleted" in await anext(response.body_iterator)
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)

    asyncio.run(scenario())


def test_confirmed_analysis_is_bound_to_exact_session_base_and_request(store):
    async def scenario():
        ai = ModelBoundary()
        service = RefinementSessionService(ai=ai, store=store)
        first = await service.create_session(creation())
        first = await generate(service, first, mode="discuss", message="first analysis")
        confirmed = first["turns"][-1]
        ai.needs_edit = False
        newer = await generate(service, first, mode="discuss", message="another analysis")
        assert newer["turns"][-1]["diagnosis"] != confirmed["diagnosis"]
        ai.needs_edit = True
        command = turn(newer, diagnosis_turn_id=confirmed["id"], message="human correction")
        await service.submit_turn(newer["id"], command)
        result = await settle(service, newer["id"])
        assert ai.previous_diagnoses[-1] == confirmed["diagnosis"]
        assert result["turns"][-1]["diagnosis_turn_id"] == confirmed["id"]
        assert ai.analysis_calls[-1][-1] == "human correction"
        calls = len(ai.analysis_calls)
        assert await service.submit_turn(newer["id"], command) == result
        assert len(ai.analysis_calls) == calls
        with pytest.raises(SessionConflict):
            await service.submit_turn(
                result["id"], command.model_copy(update={"diagnosis_turn_id": newer["turns"][-1]["id"]})
            )
        with pytest.raises(SessionConflict):
            await service.submit_turn(
                result["id"], turn(result, base=result["versions"][-1]["id"], diagnosis_turn_id=confirmed["id"])
            )
        with pytest.raises(SessionConflict):
            await service.submit_turn(result["id"], turn(result, diagnosis_turn_id=result["turns"][-1]["id"]))
        other = await service.create_session(creation())
        with pytest.raises(SessionNotFound):
            await service.submit_turn(other["id"], turn(other, diagnosis_turn_id=confirmed["id"]))
        assert len(ai.analysis_calls) == calls
        assert len(ai.generated_inputs) == 1

    asyncio.run(scenario())


def test_confirmed_analysis_cannot_authorize_ambiguous_mapping(store):
    async def scenario():
        ai = ModelBoundary(uncertain=True)
        service = RefinementSessionService(ai=ai, store=store)
        detail = await service.create_session(creation())
        detail = await generate(service, detail, mode="discuss")
        with pytest.raises(SessionConflict):
            await service.submit_turn(detail["id"], turn(detail, diagnosis_turn_id=detail["turns"][-1]["id"]))
        assert len(ai.analysis_calls) == 1
        assert not ai.generated_inputs

    asyncio.run(scenario())
    with pytest.raises(ValidationError):
        turn({"revision": 1, "baseline_version_id": uuid4().hex}, mode="discuss", diagnosis_turn_id=uuid4().hex)


def test_model_failure_classification_never_exposes_provider_or_validation_values():
    from api.refinement.service import _failure_reason
    from core.ai.types import ModelConfigurationError, ModelRequestError

    assert _failure_reason(ModelConfigurationError()) == (
        ": local AI routing configuration invalid; check model provider prefixes and deployments"
    )
    assert _failure_reason(ModelRequestError("Vision", 502)) == ": upstream HTTP 502 request error"
    assert _failure_reason(ModelRequestError("Image", "SECRET")) == ": upstream request error"
    assert _failure_reason(TimeoutError("SECRET")) == ": model request timed out"
    try:
        Diagnosis.model_validate({"SECRET_FIELD": "SECRET https://private.provider?key=secret"})
    except ValidationError as exc:
        wrapped = ValueError("SECRET validation body")
        wrapped.__cause__ = exc
        result = _failure_reason(wrapped)
        assert "schema invalid" in result and "extra_forbidden" in result
        assert "SECRET" not in result and "private.provider" not in result
    assert _failure_reason(RuntimeError("SECRET")) == ""


def test_model_request_error_is_persisted_without_provider_body(store):
    from core.ai.types import ModelRequestError

    async def scenario():
        ai = ModelBoundary()

        async def unavailable(*args, **kwargs):
            raise ModelRequestError("Vision", 502)

        ai.analyze = unavailable
        service = RefinementSessionService(ai=ai, store=store)
        detail = await service.create_session(creation())
        result = await generate(service, detail, mode="discuss")
        assert "HTTP 502" in result["turns"][-1]["error"]
        assert result["turns"][-1]["status"] == "failed"
        assert not ai.generated_inputs

    asyncio.run(scenario())
