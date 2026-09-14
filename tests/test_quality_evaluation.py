"""Historical SQLite evidence and human ratings do not require the retired generation worker."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.refinement.legacy import LegacyRefinementArchive
from api.schemas import BatchTaskCreateRequest, QualityEvaluationRequest, RefinementTaskData, RefinementTaskStatus
from api.tasks.provenance import batch_provenance


def manager(tmp_path):
    return LegacyRefinementArchive(tmp_path / "staging", tmp_path / "accepted", db_path=tmp_path / "tasks.sqlite3")


def image(value):
    return np.full((12, 12, 3), value, np.uint8)


def save_snapshot(archive, task):
    archive._store.save(
        "refinement",
        task.task_id,
        task.model_dump(mode="json"),
        {"people": [{"person_id": 1}], "swapped_image_url": "historical-only"},
    )


def evidence_blob(archive, task_id, value):
    """Create private synthetic history bytes directly, not through a retired production writer."""
    ok, encoded = cv2.imencode(".png", image(value))
    assert ok
    content = encoded.tobytes()
    digest = hashlib.sha256(content).hexdigest()
    path = archive.quality.root / task_id / f"{digest}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(content)
    assert path.read_bytes() == content
    return {"sha256": digest, "url": f"/api/v1/refinement/tasks/{task_id}/evidence/{digest}"}


def candidate_snapshot(archive, task=None, *, value=20, baseline=10, provenance=None, original=None):
    """Hand-seed the documented legacy JSON shape; this helper is test data, not a generation API."""
    if task is None:
        task = RefinementTaskData(
            task_id="rated",
            status="REVIEW_REQUIRED",
            created_at="now",
            revision=1,
            generation_count=0,
            quality_provenance=provenance,
            people=[{"person_id": 1, "face_index": 1}],
        )
        archive.tasks[task.task_id] = task
        archive._locks[task.task_id] = asyncio.Lock()
    task.generation_count += 1
    task.revision += 1
    task.status = RefinementTaskStatus.REVIEW_REQUIRED
    historical_initial = archive.quality.records("refinement_quality_initial", task.task_id)
    if historical_initial:
        initial = historical_initial[0]
    else:
        initial = {
            "task_id": task.task_id,
            "original": evidence_blob(archive, task.task_id, original) if original is not None else None,
            "initial_local": evidence_blob(archive, task.task_id, 10)
            if (task.quality_provenance or {}).get("lineage") == "batch-local"
            else None,
        }
        archive._store.save("refinement_quality_initial", task.task_id, initial)
    baseline_blob = evidence_blob(archive, task.task_id, baseline)
    candidate_blob = evidence_blob(archive, task.task_id, value)
    attempt_id = f"{task.task_id}:{task.generation_count}"
    archive._store.save(
        "refinement_quality_generation",
        attempt_id,
        {
            "task_id": task.task_id,
            "attempt_id": attempt_id,
            "generation_count": task.generation_count,
            "baseline_url": baseline_blob["url"],
            "baseline_digest": baseline_blob["sha256"],
            "model_alias": "historical-model",
            "status": "succeeded",
            "actual_model": f"model-{task.generation_count}",
            "elapsed_seconds": 0.1,
            "cost": None,
        },
    )
    task.candidate_digest = candidate_blob["sha256"]
    task.candidate_url = f"/api/v1/refinement/tasks/{task.task_id}/candidate"
    path = archive.staging_dir / task.task_id / "candidate.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((archive.quality.root / task.task_id / f"{task.candidate_digest}.png").read_bytes())
    if not any(
        row["candidate_digest"] == task.candidate_digest
        for row in archive.quality.records("refinement_quality_candidate", task.task_id)
    ):
        # Legacy records were already redacted by their original producer. No raw prompt is stored here.
        archive._store.save(
            "refinement_quality_candidate",
            f"{task.task_id}:{task.candidate_digest}",
            {
                "task_id": task.task_id,
                "candidate_digest": task.candidate_digest,
                "generation_count": task.generation_count,
                "original_url": (initial["original"] or {}).get("url"),
                "initial_local_url": (initial["initial_local"] or {}).get("url"),
                "baseline_url": baseline_blob["url"],
                "candidate_url": candidate_blob["url"],
                "hashes": {
                    "original": (initial["original"] or {}).get("sha256"),
                    "initial_local": (initial["initial_local"] or {}).get("sha256"),
                    "baseline": baseline_blob["sha256"],
                    "candidate": task.candidate_digest,
                },
                "model_alias": "historical-model",
                "audit": {
                    "model": f"model-{task.generation_count}",
                    "provider": "litellm",
                    "elapsed_seconds": 0.1,
                    "usage": None,
                },
                "cost": None,
                "cost_status": "unknown",
            },
        )
    save_snapshot(archive, task)
    return task


def rating(task, verdict="ready", **changes):
    return QualityEvaluationRequest.model_validate(
        {
            "expected_revision": task.revision,
            "candidate_digest": task.candidate_digest or task.accepted_digest,
            "verdict": verdict,
            "people": [{"person_id": 1}],
            **changes,
        }
    )


def test_rating_idempotent_acceptance_keeps_evidence_and_restart(tmp_path):
    async def run():
        archive = manager(tmp_path)
        task = candidate_snapshot(archive, provenance={"lineage": "batch-local"})
        revision = task.revision
        first, second = await asyncio.gather(
            archive.evaluate_quality(task.task_id, rating(task)), archive.evaluate_quality(task.task_id, rating(task))
        )
        assert first == second
        assert task.revision == revision and task.status == "REVIEW_REQUIRED"
        detail = archive.quality_detail(task.task_id)
        assert len(detail["evaluations"]) == 1
        candidate = detail["candidates"][0]
        assert "private" not in json.dumps(candidate)
        paths = [archive.quality.path(task.task_id, digest) for digest in candidate["hashes"].values() if digest]
        assert candidate["original_url"] is None and candidate["cost"] is None
        assert candidate["audit"]["elapsed_seconds"] >= 0
        assert all(path.is_file() for path in paths)
        await archive.accept(task.task_id, task.revision, task.candidate_digest)
        assert not (archive.staging_dir / task.task_id).exists()
        assert all(path.is_file() for path in paths)
        summary = archive.quality.summary([task])
        assert summary["first_pass_acceptance_rate"] == 1
        assert summary["identity_satisfied"]["rate_known_only"] is None
        restored = manager(tmp_path)
        assert restored.quality_detail(task.task_id)["evaluations"] == detail["evaluations"]
        assert restored.get(task.task_id).generation_count == 1

    asyncio.run(run())


def test_historical_rounds_preserve_old_rating_initial_local_and_baseline(tmp_path):
    async def run():
        archive = manager(tmp_path)
        task = candidate_snapshot(archive, provenance={"lineage": "batch-local"})
        old = rating(task, "needs_work")
        await archive.evaluate_quality(task.task_id, old)
        initial = archive.quality_detail(task.task_id)["candidates"][0]
        candidate_snapshot(archive, task, value=30, baseline=20)
        assert task.generation_count == 2
        with pytest.raises(ValueError, match="Stale"):
            await archive.evaluate_quality(task.task_id, old)
        with pytest.raises(ValueError, match="exactly"):
            await archive.evaluate_quality(task.task_id, rating(task, people=[{"person_id": 2}]))
        await archive.evaluate_quality(task.task_id, rating(task))
        detail = archive.quality_detail(task.task_id)
        assert len(detail["evaluations"]) == 2
        newer = detail["candidates"][1]
        assert newer["hashes"]["initial_local"] == initial["hashes"]["initial_local"]
        assert newer["hashes"]["baseline"] == initial["hashes"]["candidate"]
        assert cv2.imread(str(archive.quality.path(task.task_id, newer["hashes"]["initial_local"]))).mean() == 10
        summary = archive.quality.summary([task])
        assert summary["first_pass_denominator"] == 1 and summary["first_pass_acceptance_rate"] == 0

    asyncio.run(run())


def test_unrated_failed_and_legacy_unknown_are_not_zero_ratings(tmp_path):
    archive = manager(tmp_path)
    failed = RefinementTaskData(
        task_id="failed",
        status="FAILED",
        created_at="now",
        generation_count=1,
        quality_provenance={"lineage": "batch-local"},
    )
    baseline = evidence_blob(archive, failed.task_id, 10)
    archive._store.save(
        "refinement_quality_generation",
        f"{failed.task_id}:1",
        {
            "task_id": failed.task_id,
            "status": "failed",
            "generation_count": 1,
            "baseline_digest": baseline["sha256"],
            "baseline_url": baseline["url"],
            "cost": None,
        },
    )
    save_snapshot(archive, failed)
    legacy = failed.model_copy(update={"task_id": "legacy", "generation_count": None, "quality_provenance": None})
    summary = archive.quality.summary([failed, legacy])
    assert summary["total_samples"] == 2 and summary["unevaluated_samples"] == 2
    assert summary["generation_failed_total"] == 1 and summary["generation_count_unknown_samples"] == 1
    assert summary["first_pass_denominator"] == 0 and summary["first_pass_acceptance_rate"] is None
    assert summary["eligible_total"] == 1 and summary["identity_satisfied"]["rate_known_only"] is None


def test_stale_archive_instance_rejected_using_persisted_revision(tmp_path):
    async def run():
        archive = manager(tmp_path)
        task = candidate_snapshot(archive)
        stale = manager(tmp_path)
        old = rating(task)
        candidate_snapshot(archive, task, value=30, baseline=20)
        with pytest.raises(ValueError, match="Stale"):
            await stale.evaluate_quality(task.task_id, old)

    asyncio.run(run())


def test_endpoints_and_evidence_paths_do_not_need_ai(tmp_path, monkeypatch):
    from api.app import app

    archive = manager(tmp_path)
    task = candidate_snapshot(archive)
    monkeypatch.setattr(app.state, "legacy_refinement_archive", archive, raising=False)
    monkeypatch.setattr(app.state, "refinement_service", None, raising=False)
    client = TestClient(app)
    url = f"/api/v1/refinement/tasks/{task.task_id}/quality-evaluation"
    body = rating(task).model_dump(mode="json")
    assert client.put(url, json=body).status_code == 200
    assert client.put(url, json={**body, "expected_revision": 999}).status_code == 409
    assert client.put(url, json={**body, "people": [{"person_id": 999}]}).status_code == 409
    detail = client.get(url).json()["data"]
    image_url = detail["candidates"][0]["candidate_url"]
    assert client.get(image_url).status_code == 200
    assert client.get(image_url[:-64] + "0" * 64).status_code == 404
    assert archive.quality.path("../escape", "a" * 64) is None
    summary = client.get("/api/v1/refinement/quality-summary").json()["data"]
    assert summary["lineage_unknown_samples"] == 1 and summary["first_pass_acceptance_rate"] is None
    assert client.post("/api/v1/refinement/plan", json={}).status_code == 410


def test_private_batch_provenance_uses_server_original_not_reference_payloads():
    req = BatchTaskCreateRequest(
        batch_images=[{"client_target_id": "image", "image_url": "data:image/png;base64,SECRET"}],
        source_manual_images=["data:image/png;base64,PRIVATE"],
    )
    result = SimpleNamespace(
        status="SUCCESS",
        processed_url="/api/v1/files/batch/result.png",
        client_target_id="image",
        model_routing=[],
        original_url="/api/v1/files/batch/original.png",
    )
    batch = SimpleNamespace(task_id="batch", results=[result])
    archive = SimpleNamespace(tasks={"batch": batch}, _requests={"batch": req})
    evidence = batch_provenance(archive, result.processed_url)
    assert evidence["lineage"] == "batch-local"
    assert evidence["original_url"] == result.original_url
    assert "PRIVATE" not in json.dumps(evidence) and "data:image" not in json.dumps(evidence)
    assert batch_provenance(archive, "data:image/png;base64,UNRELATED") is None
    with pytest.raises(ValueError, match="valid file"):
        batch_provenance(archive, "/api/v1/files/refinement/accepted/missing-provenance-test.png")


def test_original_evidence_is_immutable_and_distinct_from_local_candidate(tmp_path):
    archive = manager(tmp_path)
    task = candidate_snapshot(archive, provenance={"lineage": "batch-local"}, original=7)
    record = archive.quality_detail(task.task_id)["candidates"][0]
    assert cv2.imread(str(archive.quality.path(task.task_id, record["hashes"]["original"]))).mean() == 7
    assert record["hashes"]["original"] != record["hashes"]["initial_local"]
    candidate_snapshot(archive, task, value=30, baseline=20, original=99)
    newer = archive.quality_detail(task.task_id)["candidates"][1]
    assert newer["hashes"]["original"] == record["hashes"]["original"]


def test_unknown_lineage_has_baseline_not_invented_initial_local(tmp_path):
    archive = manager(tmp_path)
    task = candidate_snapshot(archive)
    candidate = archive.quality_detail(task.task_id)["candidates"][0]
    assert candidate["baseline_url"] and candidate["initial_local_url"] is None
    state = task.model_dump(mode="json")
    state.pop("generation_count")
    archive._store.save("refinement", task.task_id, state)
    assert manager(tmp_path).get(task.task_id).generation_count is None


def test_identical_later_candidate_does_not_rewrite_first_pass_rating(tmp_path):
    async def run():
        archive = manager(tmp_path)
        task = candidate_snapshot(archive, provenance={"lineage": "batch-local"})
        await archive.evaluate_quality(task.task_id, rating(task, "needs_work"))
        first = task.candidate_digest
        candidate_snapshot(archive, task, value=20, baseline=20)
        assert task.candidate_digest == first
        await archive.evaluate_quality(task.task_id, rating(task))
        detail = archive.quality_detail(task.task_id)
        assert detail["evaluations"][0]["verdict"] == "ready"
        assert detail["evaluations"][0]["first_pass_verdict"] == "needs_work"
        assert detail["generations"][1]["actual_model"] == "model-2"
        assert archive.quality.summary([task])["first_pass_acceptance_rate"] == 0

    asyncio.run(run())


def test_tampered_or_unreferenced_evidence_is_unavailable(tmp_path):
    archive = manager(tmp_path)
    task = candidate_snapshot(archive)
    path = archive.quality.path(task.task_id, task.candidate_digest)
    path.write_bytes(b"tampered")
    assert archive.quality.path(task.task_id, task.candidate_digest) is None
    unrelated = evidence_blob(archive, task.task_id, 111)
    assert archive.quality.path(task.task_id, unrelated["sha256"]) is None
