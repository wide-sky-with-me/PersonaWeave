"""Shared concurrency control for blocking local model inference."""

import asyncio
import os
from collections.abc import Callable
from functools import partial
from typing import TypeVar

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

    def __init__(self, concurrency: int):
        if concurrency < 1:
            raise ValueError("inference concurrency must be positive")
        self.concurrency = concurrency
        self._semaphore = asyncio.Semaphore(concurrency)

    async def run(self, function: Callable[..., T], /, *args, **kwargs) -> T:
        """Acquire before dispatching work, then release after its thread completes."""
        async with self._semaphore:
            return await asyncio.to_thread(partial(function, *args, **kwargs))
