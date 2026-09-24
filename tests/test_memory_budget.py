"""Memory pressure is withheld before native allocations, without relying on the test host's RAM."""

from types import SimpleNamespace

import numpy as np
import pytest

from core import memory_budget as memory
from core.pipeline import composite_in_rows


def test_memory_budget_reserves_os_headroom(monkeypatch):
    monkeypatch.setattr(memory, "memory_snapshot", lambda: memory.MemorySnapshot(8 * 1024**3, 600 * memory.MIB))
    memory.require_memory(80 * memory.MIB)
    with pytest.raises(memory.MemoryPressureError, match="等待可用内存"):
        memory.require_memory(100 * memory.MIB)


def test_unknown_memory_is_not_reported_as_zero(monkeypatch):
    monkeypatch.setattr(memory, "memory_snapshot", lambda: None)
    memory.require_memory(1024**3)


def test_windows_checks_commit_limit_as_well_as_physical_memory(monkeypatch):
    def status(pointer):
        data = pointer._obj
        assert data.length == 64
        data.total = 16 * 1024**3
        data.available = 8 * 1024**3
        data.commit_available = 256 * memory.MIB
        return 1

    monkeypatch.setattr(memory.sys, "platform", "win32")
    monkeypatch.setattr(
        memory.ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(GlobalMemoryStatusEx=status)), raising=False
    )
    assert memory.memory_snapshot() == memory.MemorySnapshot(16 * 1024**3, 256 * memory.MIB)


@pytest.mark.parametrize("height", [1, 255, 256, 257, 601])
def test_row_compositing_is_pixel_identical_to_full_canvas_math(height):
    rng = np.random.default_rng(1234)
    canvas = rng.integers(0, 256, (height, 333, 3), dtype=np.uint8)
    swapped = rng.integers(0, 256, canvas.shape, dtype=np.uint8)
    mask = rng.random((height, 333), dtype=np.float32)
    original = canvas.copy()
    alpha = mask[:, :, None]
    expected = np.clip(canvas.astype(np.float32) * (1.0 - alpha) + swapped.astype(np.float32) * alpha, 0, 255).astype(
        np.uint8
    )
    np.testing.assert_array_equal(composite_in_rows(canvas, swapped, mask), expected)
    np.testing.assert_array_equal(canvas, original)
