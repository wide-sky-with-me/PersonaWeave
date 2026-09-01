"""Concurrency tests for shared local inference scheduling."""

import asyncio
import threading
import time

from api.inference_limiter import InferenceLimiter, inference_concurrency_from_env


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
