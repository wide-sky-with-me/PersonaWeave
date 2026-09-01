from types import SimpleNamespace

import numpy as np
import pytest

from api.schemas import SyncSwapRequest
from api.task_manager import TaskManager, describe_skipped_people
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
    monkeypatch.setattr("api.task_manager.resolve_source_locations", lambda *args: ["source"])
    monkeypatch.setattr(
        "api.task_manager.build_source_references",
        lambda *args: [{"embedding": np.ones(512, dtype=np.float32), "quality_score": 1.0}],
    )
    monkeypatch.setattr(
        "api.task_manager.load_image_from_source",
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
