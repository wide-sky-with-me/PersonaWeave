"""Evaluation-only swap adapters: input contract and registration guards, with a stub ONNX session."""

import numpy as np
import pytest

from scripts.swap_eval_adapters import EVAL_MODELS, AlphaFaceAdapter, register


class StubSession:
    def __init__(self):
        self.seen = None

    def run(self, _names, inputs):
        self.seen = inputs
        return [np.full((1, 3, 256, 256), 0.5, dtype=np.float32)]


def test_alphaface_feeds_rgb_unit_range_and_rescaled_raw_embedding():
    adapter = object.__new__(AlphaFaceAdapter)
    adapter.session, adapter.embedding_scale = StubSession(), 15.0
    crop = np.zeros((256, 256, 3), np.uint8)
    crop[..., 2] = 255  # BGR red
    unit_vector = np.zeros(512, np.float32)
    unit_vector[0] = 1.0
    result = adapter.infer_aligned(crop, unit_vector * 4.0)  # any magnitude is normalized first
    target, source = adapter.session.seen["target"], adapter.session.seen["source"]
    assert target.shape == (1, 3, 256, 256) and target[0, 0].max() == pytest.approx(1.0)  # red is RGB channel 0
    assert target[0, 2].max() == 0.0
    assert np.linalg.norm(source) == pytest.approx(15.0)
    assert result.shape == (256, 256, 3) and result.dtype == np.uint8 and int(result[0, 0, 0]) == 128


def test_register_rejects_unknown_or_missing_weights_without_touching_the_pipeline(tmp_path):
    class Pipeline:
        providers, models_dir = ["CPUExecutionProvider"], str(tmp_path)
        _swapper_cache = {}
        model_router = type("Router", (), {"available_models": set()})()

    with pytest.raises(ValueError):
        register(Pipeline, str(tmp_path), ["inswapper_128"])
    with pytest.raises(FileNotFoundError):
        register(Pipeline, str(tmp_path), [EVAL_MODELS[0]])
    assert not Pipeline._swapper_cache and not Pipeline.model_router.available_models
