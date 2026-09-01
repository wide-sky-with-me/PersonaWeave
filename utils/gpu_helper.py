"""
GPU and Execution Provider helper for PersonaWeave.
Automatically preloads NVIDIA CUDA/cuDNN runtime libraries on Linux
to enable seamless CUDAExecutionProvider in ONNXRuntime.
"""

import contextlib
import ctypes
import glob
import os
import site


def init_gpu_environment():
    """Preloads NVIDIA pip wheel dynamic libraries for CUDAExecutionProvider."""
    with contextlib.suppress(Exception):
        import onnxruntime as ort

        ort.set_default_logger_severity(3)

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


def get_optimal_providers() -> list:
    """Returns optimal ONNX execution providers available on this hardware."""
    init_gpu_environment()
    import onnxruntime as ort

    avail = ort.get_available_providers()
    providers = []
    if "CUDAExecutionProvider" in avail:
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
