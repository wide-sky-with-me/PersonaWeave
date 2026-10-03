"""Failed batch metadata releases inline pixels while SQLite preserves explicit recovery inputs."""

import asyncio
import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from api.projects.recovery import recover_record
from api.projects.store import ProjectStore
from api.schemas import BatchTaskCreateRequest, TaskStatus
from api.tasks.batch import TaskManager
from core.pipeline import SwapResult


@pytest.mark.parametrize("outcome", ["no_op", "inference_error", "invalid_image", "success"])
def test_failed_inline_results_preserve_request_and_explicit_project_recovery(tmp_path, outcome):
    stream = BytesIO()
    Image.new("RGB", (16, 16), "red").save(stream, "PNG")
    original = stream.getvalue()
    source = "data:image/png;base64," + base64.b64encode(original).decode()
    if outcome == "invalid_image":
        source = "data:image/png;base64,not-valid-image"
    calls = []

    def process_image(**kwargs):
        calls.append(kwargs["image"].shape)
        if outcome == "inference_error":
            raise RuntimeError("Fixture inference failure")
        return SwapResult(
            output_image=kwargs["image"],
            faces_detected=1,
            faces_swapped=int(outcome == "success"),
            matched_similarities=[],
            elapsed_time_sec=0.01,
        )

    output = tmp_path / "outputs"
    database = tmp_path / "tasks.sqlite3"
    manager = TaskManager(SimpleNamespace(process_image=process_image), output_base_dir=output, db_path=database)
    request = BatchTaskCreateRequest(batch_images=[{"client_target_id": "photo", "image_url": source}])

    async def run():
        task_id, _ = manager.create_batch_task(request)
        await asyncio.gather(*manager._background_tasks)
        return task_id

    task_id = asyncio.run(run())
    restored = TaskManager(None, output_base_dir=output, db_path=database)
    for current in (manager, restored):
        task = current.get_task(task_id)
        result = task.results[0]
        assert task.status == (TaskStatus.COMPLETED if outcome == "success" else TaskStatus.FAILED)
        assert task_id not in current._requests
        assert current.get_request_snapshot(task_id).batch_images[0].image_url == source
        assert "data:image" not in task.model_dump_json()
        if outcome == "success":
            assert result.original_url.startswith(f"/api/v1/files/{task_id}/original_")
            assert result.processed_url
            assert (output / task_id / "original_0001.png").is_file()
        else:
            assert result.original_url == ""
            assert result.processed_url is None
            assert result.error
            assert not list((output / task_id).iterdir())
    assert len(calls) == int(outcome != "invalid_image")

    if outcome != "success":
        projects = ProjectStore(tmp_path / "projects.sqlite3", tmp_path / "project-images")
        recovered = recover_record(projects, "batch", task_id, restored, None)
        target = recovered["document"]["studio"]["targetImages"][0]
        assert target["status"] == "FAILED"
        assert not target.get("processedUrl")
        if outcome == "invalid_image":
            assert target["url"] == ""
            assert any("原图文件不可用" in warning for warning in recovered["document"]["warnings"])
        else:
            assert projects.asset(recovered["id"], target["url"]["$asset"]) == original
