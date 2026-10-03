"""Non-production evaluation adapters for swap models the production router does not offer.

The weights come from FaceFusion's model releases (CRC32 verified against its ``.hash`` files, SHA-256 pinned
below) and are research, non-commercial or Apache-2.0 licensed. They are never loaded by the API, CLI or
packages: these adapters duck-type the production contract (``spec`` + ``infer_aligned``) and are injected into
one pipeline instance by the offline audit scripts, so production code stays free of unapproved models.
"""

import os

import numpy as np
import onnxruntime as ort

from core.swapper import GhostSwapAdapter, HyperSwapAdapter, SwapperSpec
from utils.gpu_helper import local_session_options

EVAL_SHA256 = {
    "hyperswap_1a_256": "c0e98a8a03a238f461ed3d2570e426b49f46745ee400854a60dceeb70c246add",
    "hyperswap_1b_256": "5124031789c42f71b9558fb71954ef7aedb6da7ed9fac79293e23c61a792a73e",
    "alphaface_256": "efcca3ffa1c28b75a007f689b39f7d4716c02810e7fa72d892e175f0b058a2e2",
    "ghost_2_256": "27ae9d0b174b22c0426393e8239ca8d75e6ff47ec5dc596b002b182832a9454e",
    "ghost_3_256": "308d87f565e881b8872a5cbe711f97faeda6643e5d2b95ef757cc92a58662abd",
}
EVAL_MODELS = tuple(EVAL_SHA256)


def _spec(name, template, encoder, license_status):
    return SwapperSpec(
        name=name,
        model_filename=f"{name}.onnx",
        input_size=(256, 256),
        crop_template=template,
        source_encoder=encoder,
        license_status=license_status,
        sha256=EVAL_SHA256[name],
    )


class EvalHyperSwap(HyperSwapAdapter):
    """HyperSwap 1a/1b share 1c's tensor contract (verified with onnxruntime); only the spec differs."""

    def __init__(self, name, model_path, providers):
        self.spec = _spec(name, "arcface_128", "arcface_w600k_r50_normed", "ResearchRAIL-evaluation-only")
        super().__init__(model_path, providers=providers)


class EvalGhost(GhostSwapAdapter):
    """Ghost 2/3 (ai-forever, Apache-2.0) use Ghost 1's contract and the same crossface converter."""

    def __init__(self, name, model_path, converter_path, providers):
        self.spec = _spec(name, "arcface_112_v1", "crossface_ghost", "Apache-2.0")
        super().__init__(model_path, converter_path, providers=providers)


class AlphaFaceAdapter:
    """AlphaFace 256: ``target`` RGB in [0, 1] plus ``source`` embedding; RGB [0, 1] output and no mask.

    FaceFusion feeds AlphaFace the raw (un-normalized) ArcFace embedding while the production pipeline carries
    unit vectors, so ``embedding_scale`` restores a raw norm (measured from the actual references).
    """

    def __init__(self, model_path, providers, embedding_scale):
        self.spec = _spec("alphaface_256", "arcface_128", "arcface_w600k_r50_raw", "Non-Commercial-evaluation-only")
        self.session = ort.InferenceSession(model_path, providers=providers, sess_options=local_session_options())
        self.embedding_scale = float(embedding_scale)

    def infer_aligned(self, crop, source_embedding):
        target = (crop[:, :, ::-1].astype(np.float32) / 255.0).transpose(2, 0, 1)[np.newaxis]
        source = np.asarray(source_embedding, dtype=np.float32).reshape(1, 512)
        source = source / max(float(np.linalg.norm(source)), 1e-6) * self.embedding_scale
        output = self.session.run(None, {"target": target, "source": source})[0][0]
        swapped = np.clip(output.transpose(1, 2, 0), 0.0, 1.0)[:, :, ::-1]
        return np.rint(swapped * 255.0).astype(np.uint8)


def register(pipeline, eval_dir, names, embedding_scale=1.0):
    """Make the named evaluation models selectable on this pipeline instance only."""
    for name in names:
        if name not in EVAL_SHA256:
            raise ValueError(f"Unknown evaluation model: {name}")
        path = os.path.join(eval_dir, f"{name}.onnx")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Evaluation weights missing: {path}")
        if name == "alphaface_256":
            adapter = AlphaFaceAdapter(path, pipeline.providers, embedding_scale)
        elif name.startswith("ghost"):
            adapter = EvalGhost(
                name, path, os.path.join(pipeline.models_dir, "crossface_ghost.onnx"), pipeline.providers
            )
        else:
            adapter = EvalHyperSwap(name, path, pipeline.providers)
        pipeline._swapper_cache[name] = adapter
        pipeline.model_router.available_models.add(name)
