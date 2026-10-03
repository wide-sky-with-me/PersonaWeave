import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from api.schemas import BatchTaskCreateRequest, SyncSwapRequest, TaskDetailData, TaskStatus
from api.tasks.batch import TaskManager, describe_skipped_people
from core.pipeline import SwapResult


def test_batch_estimate_uses_recent_measured_costs():
    manager = TaskManager.__new__(TaskManager)
    manager.tasks = {
        "one": SimpleNamespace(
            results=[
                SimpleNamespace(cost_ms=900.0, status="SUCCESS"),
                SimpleNamespace(cost_ms=1100.0, status="PARTIAL"),
                SimpleNamespace(cost_ms=9999.0, status="FAILED"),
            ]
        )
    }

    assert manager._estimate_seconds(3) == 3.0
    manager.tasks = {}
    assert manager._estimate_seconds(3) == 0.0


def test_sync_swap_rejects_inference_no_op(monkeypatch, tmp_path):
    """A completed model call must not be reported as a successful face swap."""
    process_calls = []

    def process_image(**kwargs):
        process_calls.append(kwargs)
        return SwapResult(
            output_image=kwargs["image"].copy(),
            faces_detected=1,
            faces_swapped=0,
            matched_similarities=[],
            elapsed_time_sec=0.01,
        )

    pipeline = SimpleNamespace(
        recognizer=SimpleNamespace(build_gallery_embedding=lambda embeddings, qualities: embeddings[0]),
        process_image=process_image,
    )
    monkeypatch.setattr("api.tasks.batch.resolve_source_locations", lambda *args: ["source"])
    monkeypatch.setattr(
        "api.tasks.batch.build_source_references",
        lambda *args: [{"embedding": np.ones(512, dtype=np.float32), "quality_score": 1.0}],
    )
    monkeypatch.setattr(
        "api.tasks.batch.load_image_from_source",
        lambda *args, **kwargs: np.zeros((64, 64, 3), dtype=np.uint8),
    )
    manager = TaskManager(
        pipeline,
        output_base_dir=tmp_path / "outputs",
        db_path=tmp_path / "tasks.sqlite3",
    )
    request = SyncSwapRequest(
        source_manual_images=["data:image/png;base64,AA=="],
        target_image_url="data:image/png;base64,AA==",
        parameters={"identity_blend_strength": 0.88},
    )

    with pytest.raises(ValueError, match="No visible face swap"):
        manager.execute_sync_swap(request)

    assert process_calls[0]["identity_blend_strength"] == 0.88


def test_skipped_extreme_profile_has_actionable_user_warning():
    warning = describe_skipped_people(
        [
            {"person_index": 1, "status": "SKIPPED", "reason": "extreme_pose_without_matching_reference"},
            {"person_index": 2, "status": "SWAPPED", "reason": "visible_change_confirmed"},
        ]
    )

    assert warning == "人物 #1：目标为极端侧脸，但素材库缺少同方向侧脸参考"


def test_matching_conflict_warning_does_not_suggest_lowering_threshold():
    warning = describe_skipped_people(
        [
            {"person_index": 1, "status": "SKIPPED", "reason": "assignment_conflict"},
            {"person_index": 2, "status": "SKIPPED", "reason": "target_reference_unavailable"},
        ]
    )
    assert "另一人物" in warning
    assert "代表图" in warning
    assert "未找到达到身份阈值" not in warning


@pytest.mark.parametrize("faces_detected", [0, 2])
@pytest.mark.parametrize("following_success", [False, True])
def test_batch_no_op_preserves_diagnostics_without_publishing(monkeypatch, tmp_path, faces_detected, following_success):
    """No-op metadata survives persistence; later failures cannot inherit that metadata."""
    image = np.zeros((64, 64, 3), dtype=np.uint8)
    outcomes = (
        [
            {"person_index": 3, "status": "SKIPPED", "reason": "no_visible_change", "model": "inswapper_128"},
            {"person_index": 6, "status": "SKIPPED", "reason": "identity_match_below_threshold"},
        ]
        if faces_detected
        else []
    )
    routing = [{"requested": "auto", "selected": "inswapper_128", "reason": "test route", "fallback": False}]
    no_op = SwapResult(
        output_image=image.copy(),
        faces_detected=faces_detected,
        faces_swapped=0,
        matched_similarities=[0.81234] if faces_detected else [],
        elapsed_time_sec=0.01,
        swapped_person_indices=[],
        requested_person_indices=[3, 6],
        person_outcomes=outcomes,
        model_routing=routing if faces_detected else [],
    )
    calls = []

    def process_image(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return no_op
        if not following_success:
            raise RuntimeError("inference failed before producing diagnostics")
        return SwapResult(
            output_image=image + 20,
            faces_detected=1,
            faces_swapped=1,
            matched_similarities=[0.9],
            elapsed_time_sec=0.01,
            swapped_person_indices=[1],
            requested_person_indices=[1],
            person_outcomes=[{"person_index": 1, "status": "SWAPPED", "reason": "visible_change_confirmed"}],
            model_routing=routing,
        )

    monkeypatch.setattr("api.tasks.batch.resolve_source_locations", lambda *args: [])
    monkeypatch.setattr("api.tasks.batch.load_image_from_source", lambda *args: image.copy())
    output_dir = tmp_path / "outputs"
    db_path = tmp_path / "tasks.sqlite3"
    manager = TaskManager(SimpleNamespace(process_image=process_image), output_base_dir=output_dir, db_path=db_path)
    request = BatchTaskCreateRequest(
        batch_images=[
            {"client_target_id": "no-op", "image_url": "https://example.com/first.png"},
            {"client_target_id": "following", "image_url": "https://example.com/second.png"},
        ]
    )
    task_id = "no-op-diagnostics"
    manager.tasks[task_id] = TaskDetailData(
        task_id=task_id, status=TaskStatus.QUEUED, total_images=2, created_at="2026-09-07T00:00:00+00:00"
    )
    manager._requests[task_id] = request
    asyncio.run(manager._run_batch_task(task_id))

    assert len(calls) == 2
    recovered = TaskManager(None, output_base_dir=output_dir, db_path=db_path)
    for task in (manager.get_task(task_id), recovered.get_task(task_id)):
        assert task.status == (TaskStatus.PARTIAL if following_success else TaskStatus.FAILED)
        assert task.processed_images == 2
        assert task.progress == 1.0
        assert task.success_count == int(following_success)
        assert task.failure_count == 2 - int(following_success)
        assert task.completed_at
        failed, following = task.results
        assert failed.status == "FAILED"
        assert failed.client_target_id == "no-op"
        assert failed.original_url == request.batch_images[0].image_url
        assert failed.processed_url is None
        assert failed.faces_detected == faces_detected
        assert failed.faces_swapped == 0
        assert failed.swapped_person_indices == []
        assert failed.requested_person_indices == [3, 6]
        assert failed.person_outcomes == outcomes
        assert failed.matched_similarities == ([0.8123] if faces_detected else [])
        assert failed.model_routing == (routing if faces_detected else [])
        assert failed.cost_ms >= 0
        assert failed.warning is None
        assert failed.error == (describe_skipped_people(outcomes) or "No visible face swap was produced from 0 faces")
        if following_success:
            assert following.status == "SUCCESS"
            assert following.faces_swapped == 1
            assert following.processed_url.endswith("res_0002.jpg")
            assert following.error is None
        else:
            assert following.status == "FAILED"
            assert following.error == "inference failed before producing diagnostics"
            assert following.faces_detected == 0
            assert following.person_outcomes == []
            assert following.requested_person_indices == []
            assert following.matched_similarities == []
            assert following.model_routing == []
            assert following.processed_url is None

    assert not (output_dir / task_id / "original_0001.png").exists()
    assert not (output_dir / task_id / "res_0001.jpg").exists()
    assert sorted(path.name for path in (output_dir / task_id).iterdir()) == (
        ["original_0002.png", "res_0002.jpg"] if following_success else []
    )
    assert recovered.list_task_summaries()[0].viewable_results == int(following_success)
