"""Reference boards release each full image before reading the next one."""

import asyncio
import threading
import weakref

import numpy as np
import pytest

from core.refinement.evidence import DeferredImage, build_reference_board, image_work


def test_reference_board_keeps_at_most_one_full_resolution_reference():
    loaded = []

    def load():
        assert all(reference() is None for reference in loaded)
        image = np.zeros((1000, 1200, 3), np.uint8)
        loaded.append(weakref.ref(image))
        return image

    board = build_reference_board(7, [DeferredImage((1000, 1200, 3), load) for _ in range(8)])
    assert board.shape == (1440, 640, 3)
    assert all(reference() is None for reference in loaded)


def test_cancelled_image_work_drains_thread_before_releasing_caller():
    async def scenario():
        started, release = threading.Event(), threading.Event()

        def blocked():
            started.set()
            assert release.wait(3)

        pending = asyncio.create_task(image_work(blocked))
        await asyncio.to_thread(started.wait, 2)
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()
        pending.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(scenario())
