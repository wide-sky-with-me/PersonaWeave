"""Windows CUDA selection must check the driver and bundled libraries, not wheel capabilities alone."""

from types import SimpleNamespace

import pytest

from utils import gpu_helper


@pytest.mark.parametrize("driver,libraries,expected", [(False, True, False), (True, False, False), (True, True, True)])
def test_windows_cuda_requires_driver_and_runtime(monkeypatch, driver, libraries, expected):
    monkeypatch.setattr(gpu_helper.sys, "platform", "win32")
    monkeypatch.setattr(gpu_helper, "init_gpu_environment", lambda: None)
    monkeypatch.setattr(gpu_helper, "_real_gpu_driver_present", lambda: driver)
    monkeypatch.setattr(gpu_helper, "_windows_cuda_ready", lambda: libraries)
    monkeypatch.setitem(
        gpu_helper.sys.modules,
        "onnxruntime",
        SimpleNamespace(get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"]),
    )
    providers = gpu_helper.get_optimal_providers()
    assert (isinstance(providers[0], tuple) and providers[0][0] == "CUDAExecutionProvider") is expected
    assert providers[-1] == "CPUExecutionProvider"


def test_windows_missing_driver_is_safe_cpu_fallback(monkeypatch):
    monkeypatch.setattr(gpu_helper.sys, "platform", "win32")

    def missing(*args, **kwargs):
        raise OSError("not installed")

    monkeypatch.setattr(gpu_helper.ctypes, "WinDLL", missing, raising=False)
    assert not gpu_helper._real_gpu_driver_present()
