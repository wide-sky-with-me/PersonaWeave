"""Pluggable ONNX face-swap model adapters."""

import os
from dataclasses import dataclass

import cv2
import numpy as np
import onnxruntime as ort
from insightface.model_zoo import get_model

from utils.gpu_helper import get_optimal_providers


@dataclass(frozen=True)
class SwapperSpec:
    """Declarative metadata required to run and audit a face-swap model."""

    name: str
    model_filename: str
    input_size: tuple[int, int]
    crop_template: str
    source_encoder: str
    license_status: str
    sha256: str | None = None


INSWAPPER_128_SPEC = SwapperSpec(
    name="inswapper_128",
    model_filename="inswapper_128.onnx",
    input_size=(128, 128),
    crop_template="inswapper_128",
    source_encoder="arcface_w600k_r50",
    license_status="non-commercial-unless-separately-licensed",
)

HYPERSWAP_1C_256_SPEC = SwapperSpec(
    name="hyperswap_1c_256",
    model_filename="hyperswap_1c_256.onnx",
    input_size=(256, 256),
    crop_template="arcface_128",
    source_encoder="arcface_w600k_r50_normed",
    license_status="ResearchRAIL-evaluation-only",
    sha256="5528c2d76fe9986c99d829278987ef9f3a630cb606db7628d02b57b330f406a5",
)
GHOST_1_256_SPEC = SwapperSpec(
    name="ghost_1_256",
    model_filename="ghost_1_256.onnx",
    input_size=(256, 256),
    crop_template="arcface_112_v1",
    source_encoder="crossface_ghost",
    license_status="Apache-2.0",
    sha256="4eac28021ae80128e9262080cf0da850e3190e41862650c9e805f9a516b3e924",
)

SWAPPER_SPECS = {spec.name: spec for spec in (INSWAPPER_128_SPEC, HYPERSWAP_1C_256_SPEC, GHOST_1_256_SPEC)}

# FaceFusion's normalized ArcFace-128 alignment template.
ARCFACE_128_TEMPLATE = np.array(
    [
        [0.36167656, 0.40387734],
        [0.63696719, 0.40235469],
        [0.50019687, 0.56044219],
        [0.38710391, 0.72160547],
        [0.61507734, 0.72034453],
    ],
    dtype=np.float32,
)


ARCFACE_112_V1_TEMPLATE = np.array(
    [
        [0.35473214, 0.45658929],
        [0.64526786, 0.45658929],
        [0.50000000, 0.61154464],
        [0.37913393, 0.77687500],
        [0.62086607, 0.77687500],
    ],
    dtype=np.float32,
)


def create_facefusion_box_mask(
    crop_size: tuple[int, int], blur: float = 0.3, padding: tuple[int, int, int, int] = (0, 0, 0, 0)
) -> np.ndarray:
    """Create FaceFusion-compatible crop mask (top, right, bottom, left padding)."""
    width, height = crop_size
    blur_amount = int(width * 0.5 * blur)
    blur_area = max(blur_amount // 2, 1)
    mask = np.ones((height, width), dtype=np.float32)
    top, right, bottom, left = padding
    mask[: max(blur_area, int(height * top / 100)), :] = 0
    mask[-max(blur_area, int(height * bottom / 100)) :, :] = 0
    mask[:, : max(blur_area, int(width * left / 100))] = 0
    mask[:, -max(blur_area, int(width * right / 100)) :] = 0
    if blur_amount > 0:
        mask = cv2.GaussianBlur(mask, (0, 0), blur_amount * 0.25)
    return np.clip(mask, 0.0, 1.0)


def implode_pixel_boost(crop: np.ndarray, total: int, model_size: tuple[int, int]) -> list[np.ndarray]:
    """Split an aligned high-resolution crop into FaceFusion interleaved model inputs."""
    if total == 1:
        return [crop]
    width, height = model_size
    tiled = crop.reshape(height, total, width, total, 3)
    return list(tiled.transpose(1, 3, 0, 2, 4).reshape(total**2, height, width, 3))


def explode_pixel_boost(frames: list[np.ndarray], total: int, model_size: tuple[int, int]) -> np.ndarray:
    """Reassemble interleaved model outputs into one high-resolution aligned crop."""
    if total == 1:
        return frames[0]
    width, height = model_size
    return (
        np.stack(frames)
        .reshape(total, total, height, width, 3)
        .transpose(2, 0, 3, 1, 4)
        .reshape(height * total, width * total, 3)
    )


class FaceSwapper:
    """InSwapper-128 inference adapter; the pipeline owns alignment and compositing."""

    spec = INSWAPPER_128_SPEC

    def __init__(self, model_path: str, providers: list | None = None):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"InSwapper model not found at: {model_path}")
        if providers is None:
            providers = get_optimal_providers()
        self.model = get_model(model_path, providers=providers)

    def infer_aligned(self, crop: np.ndarray, source_embedding: np.ndarray) -> np.ndarray:
        """Run InSwapper on an already aligned 128px crop without a second alignment."""
        blob = cv2.dnn.blobFromImage(
            crop,
            1.0 / self.model.input_std,
            self.model.input_size,
            (self.model.input_mean, self.model.input_mean, self.model.input_mean),
            swapRB=True,
        )
        latent = np.asarray(source_embedding, dtype=np.float32).reshape(1, -1)
        latent = np.dot(latent, self.model.emap)
        latent /= max(float(np.linalg.norm(latent)), 1e-6)
        prediction = self.model.session.run(
            self.model.output_names,
            {self.model.input_names[0]: blob, self.model.input_names[1]: latent},
        )[0]
        fake = prediction.transpose((0, 2, 3, 1))[0]
        return np.clip(255 * fake, 0, 255).astype(np.uint8)[:, :, ::-1]


class HyperSwapAdapter:
    """HyperSwap 1C 256 adapter based on FaceFusion's published preprocessing contract."""

    spec = HYPERSWAP_1C_256_SPEC

    def __init__(self, model_path: str, providers: list | None = None):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"HyperSwap model not found at: {model_path}")
        if providers is None:
            providers = get_optimal_providers()
        self.session = ort.InferenceSession(model_path, providers=providers)

    def infer_aligned(self, crop: np.ndarray, source_embedding: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Run HyperSwap on an already aligned 256px crop."""
        target = crop[:, :, ::-1].astype(np.float32) / 255.0
        target = ((target - 0.5) / 0.5).transpose(2, 0, 1)[np.newaxis]
        source = np.asarray(source_embedding, dtype=np.float32).reshape(1, 512)
        source /= max(float(np.linalg.norm(source)), 1e-6)
        output, model_mask = self.session.run(None, {"source": source, "target": target})
        swapped = output[0].transpose(1, 2, 0)
        swapped = np.clip(swapped * 0.5 + 0.5, 0.0, 1.0)[:, :, ::-1]
        return np.rint(swapped * 255.0).astype(np.uint8), np.clip(model_mask[0, 0], 0.0, 1.0)


class GhostSwapAdapter:
    """Ghost 1 256 adapter using the Apache-2.0 model and converter."""

    spec = GHOST_1_256_SPEC
    converter_sha256 = "9ec5862d9ff1f723a7380ea89baf87cddec9c56670d4db766702657939284957"

    def __init__(self, model_path: str, converter_path: str, providers: list | None = None):
        if providers is None:
            providers = get_optimal_providers()
        if not os.path.exists(model_path) or not os.path.exists(converter_path):
            raise FileNotFoundError("Ghost swapper and crossface converter are both required")
        self.session = ort.InferenceSession(model_path, providers=providers)
        self.converter = ort.InferenceSession(converter_path, providers=providers)

    def infer_aligned(self, crop: np.ndarray, source_embedding: np.ndarray) -> np.ndarray:
        """Run Ghost on an already aligned 256px crop."""
        target = crop[:, :, ::-1].astype(np.float32) / 255.0
        target = ((target - 0.5) / 0.5).transpose(2, 0, 1)[np.newaxis]
        raw_source = np.asarray(source_embedding, dtype=np.float32).reshape(1, 512)
        source = self.converter.run(None, {"input": raw_source})[0].astype(np.float32)
        output = self.session.run(None, {"target": target, "source": source})[0][0]
        swapped = output.transpose(1, 2, 0)
        swapped = np.clip(swapped * 0.5 + 0.5, 0.0, 1.0)[:, :, ::-1]
        return np.rint(swapped * 255.0).astype(np.uint8)


def create_face_swapper(name: str, models_dir: str, providers: list | None = None):
    """Create a configured swapper adapter by stable model name."""
    if name not in SWAPPER_SPECS:
        raise ValueError(f"Unsupported face swapper model: {name}")
    spec = SWAPPER_SPECS[name]
    model_path = os.path.join(models_dir, spec.model_filename)
    if name == HYPERSWAP_1C_256_SPEC.name:
        return HyperSwapAdapter(model_path, providers=providers)
    if name == GHOST_1_256_SPEC.name:
        converter_path = os.path.join(models_dir, "crossface_ghost.onnx")
        return GhostSwapAdapter(model_path, converter_path, providers=providers)
    return FaceSwapper(model_path, providers=providers)
