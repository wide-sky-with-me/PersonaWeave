"""OS memory headroom and explicit allocation budgets; no optional runtime dependency."""

import ctypes
import sys
from dataclasses import dataclass
from pathlib import Path

MIB = 1024 * 1024
SYSTEM_RESERVE = 512 * MIB


@dataclass(frozen=True)
class MemorySnapshot:
    total: int
    available: int


class MemoryPressureError(MemoryError):
    """A local allocation was withheld before inference because headroom was insufficient."""


def memory_snapshot() -> MemorySnapshot | None:
    """Read physical/commit headroom on Windows, MemAvailable and cgroup v2 on Linux.

    Unknown platforms keep serial execution, without inventing a memory reading.
    Windows uses the smaller of immediately available RAM and remaining commit capacity.
    """
    try:
        if sys.platform == "win32":

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
                    (name, ctypes.c_uint64)
                    for name in (
                        "total",
                        "available",
                        "commit_total",
                        "commit_available",
                        "virtual_total",
                        "virtual_available",
                        "extended",
                    )
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return MemorySnapshot(status.total, min(status.available, status.commit_available))
        if sys.platform.startswith("linux"):
            values = {
                line.split(":", 1)[0]: int(line.split()[1]) * 1024
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith(("MemTotal:", "MemAvailable:"))
            }
            total, available = values["MemTotal"], values["MemAvailable"]
            # Container limits can be tighter than the host RAM reported by /proc/meminfo.
            groups = Path("/proc/self/cgroup").read_text().splitlines()
            group = next((line[3:] for line in groups if line.startswith("0::")), None)
            base = Path("/sys/fs/cgroup")
            if group is not None:
                directory = base / group.lstrip("/")
                if directory.is_relative_to(base):
                    while directory.is_relative_to(base):
                        limit = directory / "memory.max"
                        current = directory / "memory.current"
                        if limit.is_file() and current.is_file():
                            value = limit.read_text().strip()
                            if value != "max":
                                cap = int(value)
                                total = min(total, cap)
                                available = min(available, max(0, cap - int(current.read_text())))
                        if directory == base:
                            break
                        directory = directory.parent
            return MemorySnapshot(total, available)
    except (OSError, ValueError, KeyError):
        # Reading host statistics can be denied in a container; never fabricate a capacity.
        return None
    return None


def require_memory(allocation_bytes: int = 0) -> None:
    """Refuse an allocation budget plus OS headroom; this is not a peak-memory prediction."""
    snapshot = memory_snapshot()
    if snapshot is None:
        return
    required = max(0, allocation_bytes) + SYSTEM_RESERVE
    if snapshot.available < required:
        raise MemoryPressureError(
            f"等待可用内存：当前约 {snapshot.available / MIB:.0f} MiB，"
            f"本步安全预算需约 {required / MIB:.0f} MiB（含系统预留）。请关闭其他占内存程序。"
        )


def require_swap_memory(image) -> None:
    """Budget 20 RGB buffers for the full-canvas float masks, blends and output copies.

    The original uint8 image is already resident. 20 × its byte count budgets another
    60 bytes/pixel before the pipeline allocates full-canvas float32 intermediates.
    This deliberately retains original resolution and is checked anew for every image.
    """
    require_memory(image.nbytes * 20)
