"""
GPU and Execution Provider helper for PersonaWeave.
Preloads isolated NVIDIA CUDA/cuDNN runtime libraries on Linux and Windows.
"""

import contextlib
import ctypes
import glob
import os
import site
import sys
from functools import lru_cache
from pathlib import Path

_DLL_DIRECTORIES = []


def init_gpu_environment():
    """Preloads NVIDIA pip wheel dynamic libraries for CUDAExecutionProvider."""
    with contextlib.suppress(Exception):
        import onnxruntime as ort

        ort.set_default_logger_severity(3)

    if sys.platform == "win32":
        return

    with contextlib.suppress(Exception):
        site_packages_list = site.getsitepackages()
        for site_pkg in site_packages_list:
            nvidia_base = os.path.join(site_pkg, "nvidia")
            if not os.path.exists(nvidia_base):
                continue
            lib_order = ["cuda_runtime", "cuda_nvrtc", "nvjitlink", "cublas", "cudnn", "cufft", "curand"]
            for name in lib_order:
                lib_dir = os.path.join(nvidia_base, name, "lib")
                if os.path.exists(lib_dir):
                    for so_file in sorted(glob.glob(os.path.join(lib_dir, "*.so*"))):
                        with contextlib.suppress(Exception):
                            ctypes.CDLL(so_file, mode=ctypes.RTLD_GLOBAL)


def _real_gpu_driver_present() -> bool:
    """Checks whether an actual NVIDIA driver (not just the pip CUDA runtime wheels) is usable.

    `onnxruntime-gpu` is built with CUDAExecutionProvider compiled in, so
    `ort.get_available_providers()` reports it regardless of the actual host - it only reflects
    what the package supports, not what hardware/driver is present. The `nvidia-*-cu12` pip
    wheels (installed as regular dependencies here) only provide the CUDA *runtime* libraries
    (libcudart/libcublas/libcudnn); they never include `libcuda.so`, which is the driver API
    library shipped exclusively by an actual NVIDIA driver install (bare metal, a container with
    the NVIDIA Container Toolkit, or WSL2 GPU passthrough). If that driver isn't present,
    letting onnxruntime create a CUDAExecutionProvider session anyway doesn't fail gracefully -
    it segfaults the whole process instead of raising a catchable Python exception. Loading
    (not calling into) `libcuda.so.1` is a safe, side-effect-free way to tell the two cases apart
    before onnxruntime ever gets a chance to use it.
    """
    if sys.platform == "win32":
        try:
            # Only the OS-installed driver may satisfy this check, never a bundled DLL.
            driver = ctypes.WinDLL("nvcuda.dll", winmode=0x00000800)
            driver.cuInit.argtypes = [ctypes.c_uint]
            driver.cuInit.restype = ctypes.c_int
            driver.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
            driver.cuDeviceGetCount.restype = ctypes.c_int
            count = ctypes.c_int()
            return driver.cuInit(0) == 0 and driver.cuDeviceGetCount(ctypes.byref(count)) == 0 and count.value > 0
        except OSError:
            return False
    with contextlib.suppress(Exception):
        ctypes.CDLL("libcuda.so.1")
        return True
    return False


@lru_cache(maxsize=1)
def _windows_cuda_ready():
    """Load bundled DLLs once; missing runtime dependencies must select CPU before session creation."""
    import onnxruntime as ort

    try:
        bins = sorted((Path(ort.__file__).parent.parent / "nvidia").glob("*/bin"))
        for directory in bins:
            # Keep handles alive: closing them removes the DLL search directory.
            _DLL_DIRECTORIES.append(os.add_dll_directory(str(directory)))
        os.environ["PATH"] = os.pathsep.join([*(str(path) for path in bins), os.environ.get("PATH", "")])
        ort.preload_dlls(directory="")
        # preload_dlls logs individual failures instead of raising. Check the provider's
        # complete static import chain so a missing DLL cannot masquerade as GPU readiness.
        ctypes.WinDLL(str(Path(ort.__file__).parent / "capi/onnxruntime_providers_cuda.dll"))
        return True
    except (OSError, RuntimeError) as exc:
        print(f"[PersonaWeave] Windows CUDA runtime unavailable ({type(exc).__name__}); using CPU.")
        return False


def get_optimal_providers() -> list:
    """Returns optimal ONNX execution providers available on this hardware."""
    init_gpu_environment()
    import onnxruntime as ort

    avail = ort.get_available_providers()
    providers = []
    if (
        "CUDAExecutionProvider" in avail
        and _real_gpu_driver_present()
        and (sys.platform != "win32" or _windows_cuda_ready())
    ):
        cuda_options = {
            "device_id": 0,
            "arena_extend_strategy": "kSameAsRequested",
            "gpu_mem_limit": 3 * 1024 * 1024 * 1024,
            "cudnn_conv_algo_search": "DEFAULT",
            "do_copy_in_default_stream": "1",
        }
        providers.append(("CUDAExecutionProvider", cuda_options))
    if "CPUExecutionProvider" in avail:
        providers.append("CPUExecutionProvider")
    return providers if providers else ["CPUExecutionProvider"]
