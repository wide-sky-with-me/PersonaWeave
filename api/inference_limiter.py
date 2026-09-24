"""Shared concurrency control for blocking local model inference."""

import asyncio
import contextvars
import os
import time
from collections.abc import Callable
from functools import partial
from typing import TypeVar

from core.memory_budget import MemoryPressureError, require_memory

T = TypeVar("T")


def inference_concurrency_from_env() -> int:
    """Return the safe concurrency for the process-wide shared local pipeline.

    The pipeline owns shared detector/localizer/ONNX state whose concurrent execution
    has not been proven safe. Rejecting rather than silently accepting values above one
    makes that production contract explicit until isolated pipeline instances exist.
    """
    raw = os.getenv("PERSONAWEAVE_INFERENCE_CONCURRENCY", "1")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("PERSONAWEAVE_INFERENCE_CONCURRENCY must be exactly 1") from exc
    if value != 1:
        raise ValueError("PERSONAWEAVE_INFERENCE_CONCURRENCY must be exactly 1 for the shared pipeline")
    return value


class InferenceLimiter:
    """Run blocking local inference in worker threads under one async semaphore."""

    def __init__(self, concurrency: int, *, memory_wait_seconds: float = 120, poll_seconds: float = 1):
        if concurrency < 1:
            raise ValueError("inference concurrency must be positive")
        self.concurrency = concurrency
        self._semaphore = asyncio.Semaphore(concurrency)
        self.memory_wait_seconds = memory_wait_seconds
        self.poll_seconds = poll_seconds

    async def run(self, function: Callable[..., T], /, *args, on_memory_wait=None, **kwargs) -> T:
        """Queue before decoding, wait for headroom, and keep cancelled worker threads serialized.

        Only explicit pre-allocation MemoryPressureError is retried, never model/provider errors.
        Cancelling an HTTP coroutine cannot cancel a native thread; its slot stays owned until done.
        """
        await self._semaphore.acquire()
        deadline = time.monotonic() + self.memory_wait_seconds
        try:
            while True:
                try:
                    require_memory()
                    context = contextvars.copy_context()
                    worker = asyncio.get_running_loop().run_in_executor(
                        None, partial(context.run, function, *args, **kwargs)
                    )
                    try:
                        result = await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        # The route may own temporary files used by this thread. Drain it before
                        # unwinding the route, including when a second cancellation arrives.
                        while not worker.done():
                            try:
                                await asyncio.shield(worker)
                            except asyncio.CancelledError:
                                continue
                            except Exception:
                                break  # The caller is cancelled; consume the worker failure below.
                        if not worker.cancelled():
                            worker.exception()
                        raise
                    if on_memory_wait:
                        on_memory_wait(None)
                    return result
                except MemoryPressureError as exc:
                    message = str(exc)
                    worker = None  # Drop a failed Task and its traceback before waiting for memory to recover.
                    if time.monotonic() >= deadline:
                        raise MemoryPressureError(
                            message + " 等待超时，本任务已停止；可释放内存或缩小单张图片后重新提交。"
                        ) from None
                    if on_memory_wait:
                        on_memory_wait(message)
                # Leave the exception handler before sleeping so tracebacks do not retain decoded images.
                await asyncio.sleep(self.poll_seconds)
        finally:
            self._semaphore.release()
