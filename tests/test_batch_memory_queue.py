"""Bounded image lifetimes and batch scheduling regressions, without native model or AI calls."""

import asyncio
import weakref
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np

from api.inference_limiter import InferenceLimiter
from api.schemas import BatchTaskCreateRequest, FaceClusterRequest, TaskStatus
from api.tasks.batch import TaskManager
from core.pipeline import SwapResult


def test_nine_batch_images_release_original_and_result_before_next_decode(monkeypatch, tmp_path):
    alive = []
    loads = []

    def load(source):
        assert not any(reference() is not None for reference in alive), "preceding pixel arrays are still retained"
        image = np.full((32, 48, 3), 55, dtype=np.uint8)
        alive.append(weakref.ref(image))
        loads.append(source)
        return image

    def process_image(**kwargs):
        output = kwargs["image"] + 1
        alive.append(weakref.ref(output))
        return SwapResult(output, 1, 1, [1.0], 0.01)

    monkeypatch.setattr("api.tasks.batch.load_image_from_source", load)
    manager = TaskManager(
        SimpleNamespace(process_image=process_image), output_base_dir=tmp_path / "output", db_path=tmp_path / "tasks.db"
    )

    async def exercise():
        request = BatchTaskCreateRequest(
            batch_images=[{"client_target_id": str(i), "image_url": f"offline-{i}"} for i in range(9)]
        )
        task_id, _ = manager.create_batch_task(request)
        await asyncio.gather(*manager._background_tasks)
        assert manager.get_task(task_id).status == TaskStatus.COMPLETED
        assert manager.get_task(task_id).processed_images == 9

    asyncio.run(exercise())
    assert len(loads) == 9
    assert not any(reference() is not None for reference in alive)


def test_later_batch_does_not_prepare_sources_until_prior_batch_finishes(tmp_path):
    manager = TaskManager(None, output_base_dir=tmp_path / "output", db_path=tmp_path / "tasks.db")
    order = []

    async def exercise():
        release = asyncio.Event()

        async def execute(task_id, request):
            order.append(task_id)
            if task_id == "first":
                await release.wait()

        manager._execute_batch_task = execute
        manager.get_request_snapshot = lambda task_id: BatchTaskCreateRequest(
            batch_images=[{"image_url": "offline", "client_target_id": "one"}]
        )
        first = asyncio.create_task(manager._run_batch_task("first"))
        second = asyncio.create_task(manager._run_batch_task("second"))
        await asyncio.sleep(0.01)
        assert order == ["first"]
        release.set()
        await asyncio.gather(first, second)
        assert order == ["first", "second"]

    asyncio.run(exercise())


def test_clustering_releases_frames_and_supplies_lossless_lazy_snapshots(monkeypatch):
    from api.routes import faces

    alive = []

    def load(source):
        assert not any(reference() is not None for reference in alive)
        image = np.full((32, 48, 3), int(source), dtype=np.uint8)
        alive.append(weakref.ref(image))
        return image

    async def review(**kwargs):
        assert kwargs["raw_images_bgr"] == []
        assert not any(reference() is not None for reference in alive)
        pixels = await kwargs["image_loader"](5)
        assert pixels.shape == (32, 48, 3) and np.all(pixels == 5)
        return []

    monkeypatch.setattr(faces, "load_image_from_source", load)
    monkeypatch.setattr(
        "core.ai.arbiter.VLMIdentityArbiter", lambda: SimpleNamespace(refine_clusters=AsyncMock(side_effect=review))
    )
    pipeline = SimpleNamespace(detector=SimpleNamespace(detect_faces=lambda *args, **kwargs: []))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(pipeline=pipeline, inference_limiter=InferenceLimiter(1)))
    )
    result = asyncio.run(faces.cluster_batch_faces(FaceClusterRequest(image_urls=[str(i) for i in range(9)]), request))
    assert len(result.data.images) == 9
    assert all(item.image_width == 48 and item.image_height == 32 for item in result.data.images)


def test_requests_live_only_during_execution_and_recover_lazily(tmp_path, monkeypatch):
    """Queued/terminal history never owns the caller's full image recipes."""
    import gc

    from api.schemas import ImageSwapResult
    from api.tasks.persistence import SQLiteTaskStore
    from api.tasks.provenance import batch_provenance

    manager = TaskManager(None, output_base_dir=tmp_path / "output", db_path=tmp_path / "tasks.db")
    payload = "data:image/png;base64," + "A" * 100_000
    ids = []
    references = []

    async def execute(task_id, req):
        references.append(weakref.ref(req))
        assert list(manager._requests) == [task_id]
        task = manager.tasks[task_id]
        task.status = TaskStatus.COMPLETED
        task.results = [
            ImageSwapResult(
                client_target_id="one",
                original_url="/api/v1/files/batch/original.png",
                processed_url=f"/api/v1/files/{task_id}/result.jpg",
            )
        ]
        manager._persist(task_id)

    async def exercise():
        manager._execute_batch_task = execute
        for _ in range(3):
            req = BatchTaskCreateRequest(
                batch_images=[{"client_target_id": "one", "image_url": payload}], source_manual_images=[payload]
            )
            reference = weakref.ref(req)
            ids.append(manager.create_batch_task(req)[0])
            del req
            assert reference() is None
        assert manager._requests == {}
        await asyncio.gather(*manager._background_tasks)

    asyncio.run(exercise())
    gc.collect()
    assert manager._requests == {}
    assert all(reference() is None for reference in references)
    # Startup must not even read request_json. An explicit accessor loads one recipe.
    with monkeypatch.context() as patch:
        patch.setattr(
            SQLiteTaskStore, "load_request", lambda *args: (_ for _ in ()).throw(AssertionError("eager request"))
        )
        recovered = TaskManager(None, output_base_dir=tmp_path / "output", db_path=tmp_path / "tasks.db")
    assert recovered._requests == {}
    for task_id in ids:
        req = recovered.get_request_snapshot(task_id)
        assert req.batch_images[0].image_url == payload
        req.source_manual_images.clear()
        assert recovered.get_request_snapshot(task_id).source_manual_images == [payload]
        result = recovered.get_task(task_id).results[0]
        assert batch_provenance(recovered, result.processed_url)["sources"][0]["manual_count"] == 1
    assert recovered._requests == {}


def test_worker_exception_releases_request(tmp_path):
    manager = TaskManager(None, output_base_dir=tmp_path / "output", db_path=tmp_path / "tasks.db")
    references = []

    async def execute(task_id, req):
        references.append(weakref.ref(req))
        raise RuntimeError("worker failed")

    async def exercise():
        manager._execute_batch_task = execute
        task_id, _ = manager.create_batch_task(
            BatchTaskCreateRequest(batch_images=[{"client_target_id": "one", "image_url": "input"}])
        )
        await asyncio.gather(*manager._background_tasks)
        assert manager.get_task(task_id).status == TaskStatus.FAILED

    asyncio.run(exercise())
    assert manager._requests == {}


def test_recipe_load_failure_terminates_dead_queued_task_and_releases_next(tmp_path, monkeypatch):
    manager = TaskManager(None, output_base_dir=tmp_path / "output", db_path=tmp_path / "tasks.db")
    original = manager.get_request_snapshot
    first_id = None

    def read(task_id):
        if task_id == first_id:
            raise ValueError("corrupt private request")
        return original(task_id)

    monkeypatch.setattr(manager, "get_request_snapshot", read)

    async def exercise():
        nonlocal first_id
        request = BatchTaskCreateRequest(batch_images=[{"image_url": "offline", "client_target_id": "one"}])
        first_id, _ = manager.create_batch_task(request)
        second_id, _ = manager.create_batch_task(request)
        seen = []

        async def execute(task_id, _request):
            seen.append(task_id)
            manager.tasks[task_id].status = TaskStatus.COMPLETED

        monkeypatch.setattr(manager, "_execute_batch_task", execute)
        await asyncio.gather(*manager._background_tasks)
        assert seen == [second_id]
        failed = manager.get_task(first_id)
        assert failed.status == TaskStatus.FAILED
        assert "private" not in failed.error
        assert failed.completed_at
        restarted = TaskManager(None, output_base_dir=tmp_path / "output", db_path=tmp_path / "tasks.db")
        assert restarted.get_task(first_id).status == TaskStatus.FAILED

    asyncio.run(exercise())
