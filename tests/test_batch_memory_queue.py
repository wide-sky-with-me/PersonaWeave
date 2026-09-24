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
        first = asyncio.create_task(manager._run_batch_task("first", None))
        second = asyncio.create_task(manager._run_batch_task("second", None))
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
