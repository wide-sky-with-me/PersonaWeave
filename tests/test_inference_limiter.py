"""Concurrency tests for shared local inference scheduling."""

import asyncio
import threading
import time
import weakref

import numpy as np
import pytest

from api.inference_limiter import InferenceLimiter, inference_concurrency_from_env
from core.memory_budget import MemoryPressureError


def test_inference_concurrency_defaults_to_one(monkeypatch):
    monkeypatch.delenv("PERSONAWEAVE_INFERENCE_CONCURRENCY", raising=False)
    assert inference_concurrency_from_env() == 1


def test_inference_concurrency_rejects_unproven_shared_pipeline_parallelism(monkeypatch):
    monkeypatch.setenv("PERSONAWEAVE_INFERENCE_CONCURRENCY", "2")
    with __import__("pytest").raises(ValueError, match="exactly 1"):
        inference_concurrency_from_env()


def test_inference_limiter_caps_blocking_local_calls():
    async def exercise():
        limiter = InferenceLimiter(2)
        lock = threading.Lock()
        active = 0
        max_active = 0

        def blocking_inference():
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1

        await asyncio.gather(*(limiter.run(blocking_inference) for _ in range(6)))
        return max_active

    assert asyncio.run(exercise()) == 2


def test_event_loop_remains_responsive_while_inference_blocks():
    async def exercise():
        limiter = InferenceLimiter(1)
        started = threading.Event()
        release = threading.Event()

        def blocking_inference():
            started.set()
            release.wait(timeout=1)

        inference_task = asyncio.create_task(limiter.run(blocking_inference))
        await asyncio.to_thread(started.wait, 1)
        before = asyncio.get_running_loop().time()
        await asyncio.sleep(0)
        elapsed = asyncio.get_running_loop().time() - before
        release.set()
        await inference_task
        return elapsed

    assert asyncio.run(exercise()) < 0.1


def test_cancelled_http_caller_does_not_release_a_running_native_worker():
    async def exercise():
        limiter = InferenceLimiter(1)
        started, release, next_started = threading.Event(), threading.Event(), threading.Event()

        def blocked():
            started.set()
            release.wait(timeout=2)

        first = asyncio.create_task(limiter.run(blocked))
        await asyncio.to_thread(started.wait, 1)
        first.cancel()
        second = asyncio.create_task(limiter.run(next_started.set))
        try:
            await asyncio.sleep(0.03)
            first.cancel()  # A repeated HTTP cancellation must not unwind temporary-file ownership early.
            assert not next_started.is_set()
            assert not first.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        assert next_started.is_set()

    asyncio.run(exercise())


def test_low_memory_waits_before_dispatch_and_resumes_without_duplicate_inference(monkeypatch):
    ready, calls, messages = False, [], []

    def check():
        if not ready:
            raise MemoryPressureError("内存不足")

    monkeypatch.setattr("api.inference_limiter.require_memory", check)

    async def exercise():
        nonlocal ready
        limiter = InferenceLimiter(1, poll_seconds=0.005)
        pending = asyncio.create_task(limiter.run(lambda: calls.append("inference"), on_memory_wait=messages.append))
        await asyncio.sleep(0.02)
        assert not calls and messages
        ready = True
        await pending
        assert calls == ["inference"] and messages[-1] is None

    asyncio.run(exercise())


def test_low_memory_timeout_does_not_attempt_inference(monkeypatch):
    def blocked():
        raise MemoryPressureError("内存不足")

    monkeypatch.setattr("api.inference_limiter.require_memory", blocked)
    with pytest.raises(MemoryPressureError, match="等待超时"):
        asyncio.run(InferenceLimiter(1, memory_wait_seconds=0).run(lambda: pytest.fail("must not run")))


def test_retry_drops_refused_frame_before_next_attempt():
    references = []

    def preflight_then_infer():
        if references:
            assert references[0]() is None, "failed preflight traceback retained the previous image"
            return "completed"
        frame = np.zeros((32, 32, 3), dtype=np.uint8)
        references.append(weakref.ref(frame))
        raise MemoryPressureError("暂时不足")

    assert asyncio.run(InferenceLimiter(1, poll_seconds=0.001).run(preflight_then_infer)) == "completed"


def test_queue_snapshot_identifies_cancelled_native_work_and_waiters(monkeypatch):
    monkeypatch.setattr("api.inference_limiter.require_memory", lambda: None)

    async def exercise():
        limiter = InferenceLimiter(1)
        started, release = threading.Event(), threading.Event()

        def execute_sync_swap():
            started.set()
            release.wait(timeout=2)

        first = asyncio.create_task(limiter.run(execute_sync_swap))
        await asyncio.to_thread(started.wait, 1)
        second = asyncio.create_task(limiter.run(lambda: None))
        await asyncio.sleep(0)
        assert limiter.snapshot()["queued"] == 1
        assert limiter.snapshot()["active"][0]["operation"] == "single_swap"
        first.cancel()
        await asyncio.sleep(0)
        assert limiter.snapshot()["active"][0]["phase"] == "finishing_cancelled_request"
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert limiter.snapshot()["queued"] == 0
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert limiter.snapshot() == {"queued": 0, "active": []}
        assert await limiter.run(lambda: "next") == "next"

    asyncio.run(exercise())


def test_queue_snapshot_reports_memory_wait_and_clears_on_cancel(monkeypatch):
    def insufficient():
        raise MemoryPressureError("等待可用内存")

    monkeypatch.setattr("api.inference_limiter.require_memory", insufficient)

    async def exercise():
        limiter = InferenceLimiter(1, poll_seconds=0.01)
        pending = asyncio.create_task(limiter.run(lambda: None))
        await asyncio.sleep(0.005)
        active = limiter.snapshot()["active"][0]
        assert active["phase"] == "waiting_memory"
        assert active["waiting_reason"] == "等待可用内存"
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert limiter.snapshot() == {"queued": 0, "active": []}

    asyncio.run(exercise())
