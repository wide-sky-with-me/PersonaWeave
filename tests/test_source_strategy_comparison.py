"""Three-arm experiment uses real selection, assignment and pose guards with fake pixels."""

import copy
from functools import wraps
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from core.face_metrics import calculate_yaw_ratio
from core.pipeline import PersonaWeavePipeline
from core.recognizer import FaceRecognizer
from core.swap_diagnostics import select_source_embedding
from scripts import compare_source_strategies as experiment


@pytest.fixture
def case(tmp_path, monkeypatch):
    paths = []
    for name, value in (("target", 20), ("first", 40), ("best", 80)):
        path = tmp_path / f"{name}.png"
        assert cv2.imwrite(str(path), np.full((32, 32, 3), value, np.uint8))
        paths.append(path)
    pipeline = object.__new__(PersonaWeavePipeline)
    pipeline.swapper_model = "inswapper_128"
    pipeline.providers = ["CPUExecutionProvider"]
    pipeline.detector = SimpleNamespace(
        detect_faces=lambda image, **_: [
            SimpleNamespace(
                bbox=np.array([4.0, 4.0, 28.0, 28.0]),
                kps=np.array([[10.0, 10.0], [20.0, 10.0], [15.0, 15.0], [10.0, 20.0], [20.0, 20.0]]),
                det_score=0.99,
            )
        ]
    )

    def embedding(image, face):
        value = int(image[0, 0, 0])
        face.embedding = np.array({20: [0.6, 0.8], 40: [1.0, 0.0], 80: [0.0, 1.0]}[value], np.float32)
        return face.embedding

    pipeline.recognizer = SimpleNamespace(
        extract_embedding=embedding,
        build_gallery_embedding=FaceRecognizer.build_gallery_embedding,
        compute_similarity=FaceRecognizer.compute_similarity,
    )
    pipeline.route_swapper = lambda face, model, research: SimpleNamespace(
        requested=model, selected=model, reason="stub", fallback=False
    )
    pipeline.get_swapper = lambda name: object()
    pipeline.swap_vectors = []

    def swap(**kwargs):
        pipeline.swap_vectors.append(kwargs["source_embedding"].copy())
        image = kwargs["canvas"].copy()
        image[4:28, 4:28] += 20
        return image

    pipeline.swap_single_face_facefusion = swap
    pipeline.calls = []
    real_process = pipeline.process_image_multi_mapping

    @wraps(real_process)
    def process(*args, **kwargs):
        pipeline.calls.append(copy.deepcopy(kwargs))
        return real_process(*args, **kwargs)

    pipeline.process_image_multi_mapping = process
    monkeypatch.setattr(
        experiment, "calculate_reference_quality", lambda image, face: 0.2 if image[0, 0, 0] == 40 else 0.9
    )
    return pipeline, paths[0], paths[1:], tmp_path / "outputs"


def test_fair_parameters_real_selection_and_mixture(case):
    pipeline, target, sources, output = case
    result = experiment.compare_source_strategies(
        pipeline,
        target,
        sources,
        1,
        output,
        parameters={"identity_blend_strength": 0.9, "color_transfer_ring_strength": 0.32, "similarity_threshold": 0.42},
    )
    assert result["successful_strategy_count"] == result["declared_strategy_count"] == 3
    assert result["production_best_selection"]["selected_reference_index"] == 2
    assert [arm["reference_indices"] for arm in result["strategies"]] == [[2], [1, 2], [1, 2]]
    assert [call["mappings"][0]["source_mode"] for call in pipeline.calls] == ["gallery", "gallery", "best-match"]
    common = [
        {key: value for key, value in call.items() if key not in ("image", "mappings")} for call in pipeline.calls
    ]
    assert all(value == common[0] for value in common)
    assert common[0]["enable_dense_mask"] is True
    for call in pipeline.calls:
        np.testing.assert_allclose(call["mappings"][0]["target_embedding"], [0.6, 0.8])
        assert call["mappings"][0]["similarity_threshold"] == 0.42
        assert np.all(call["image"] == 20)
    refs = pipeline.calls[1]["mappings"][0]["source_references"]
    gallery = FaceRecognizer.build_gallery_embedding([ref["embedding"] for ref in refs], [0.2, 0.9])
    blend, _ = select_source_embedding(0, refs, gallery, "best-match", [0.2, 0.9])
    np.testing.assert_allclose(pipeline.swap_vectors[0], [0, 1])
    np.testing.assert_allclose(pipeline.swap_vectors[1], gallery)
    np.testing.assert_allclose(pipeline.swap_vectors[2], blend)
    assert cv2.imread(str(output / "comparison.png")).shape == (424, 1152, 3)
    assert len(result["sources"]) == 2
    assert "embedding" not in (output / "manifest.json").read_text().replace("independent_valid_embedding_count", "")
    assert not result.get("winner")


def test_noop_preserves_three_failed_slots_and_no_output(case):
    pipeline, target, sources, output = case
    pipeline.swap_single_face_facefusion = lambda **kw: kw["canvas"].copy()
    result = experiment.compare_source_strategies(pipeline, target, sources, 1, output)
    assert result["successful_strategy_count"] == 0
    assert len(result["strategies"]) == 3
    assert all(arm["status"] == "failed" and arm["output"] is None for arm in result["strategies"])
    assert all(arm["person_outcomes"][0]["reason"] == "no_visible_change" for arm in result["strategies"])
    assert not list(output.glob("target_*.png"))
    assert (output / "comparison.png").exists()


def test_best_only_keeps_production_extreme_pose_guard(case):
    pipeline, target, sources, output = case
    original = pipeline.detector.detect_faces

    def detect(image, **kwargs):
        faces = original(image, **kwargs)
        if image[0, 0, 0] != 80:
            faces[0].kps[2, 0] = 20  # yaw=1; only the lower quality ref matches this pose.
        return faces

    pipeline.detector.detect_faces = detect
    result = experiment.compare_source_strategies(pipeline, target, sources, 1, output)
    assert result["production_best_selection"]["selected_reference_index"] == 2
    assert result["strategies"][0]["status"] == "failed"
    assert result["strategies"][0]["person_outcomes"][0]["reason"] == "extreme_pose_without_matching_reference"
    assert result["successful_strategy_count"] == 2
    assert calculate_yaw_ratio(pipeline.calls[0]["mappings"][0]["source_references"][0]["face"].kps) == 0


def test_multiface_source_rejected_without_hidden_leftmost_selection(case):
    pipeline, target, sources, output = case
    original = pipeline.detector.detect_faces
    pipeline.detector.detect_faces = lambda image, **kwargs: (
        original(image, **kwargs) * (2 if image[0, 0, 0] == 40 else 1)
    )
    with pytest.raises(ValueError, match="exactly one"):
        experiment.compare_source_strategies(pipeline, target, sources, 1, output)
    assert not pipeline.calls


@pytest.mark.parametrize(
    "parameters", [{"source_mode": "best_only"}, {"manual_mask": "payload"}, {"similarity_threshold": float("nan")}]
)
def test_unsupported_or_invalid_parameters_are_rejected(case, parameters):
    pipeline, target, sources, output = case
    with pytest.raises(ValueError):
        experiment.compare_source_strategies(pipeline, target, sources, 1, output, parameters=parameters)
    assert not pipeline.calls


def test_nonempty_output_and_invalid_index_fail_before_inference(case):
    pipeline, target, sources, output = case
    with pytest.raises(ValueError, match="person-index"):
        experiment.compare_source_strategies(pipeline, target, sources, 2, output)
    output.mkdir()
    (output / "old.txt").write_text("preserve")
    with pytest.raises(ValueError, match="empty"):
        experiment.compare_source_strategies(pipeline, target, sources, 1, output)
    assert (output / "old.txt").read_text() == "preserve"
    assert not pipeline.calls


def test_help_does_not_initialize_models(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("Help must not construct model sessions")

    monkeypatch.setattr(PersonaWeavePipeline, "__init__", forbidden)
    monkeypatch.setattr("sys.argv", ["compare_source_strategies", "--help"])
    with pytest.raises(SystemExit) as stopped:
        experiment.main()
    assert stopped.value.code == 0
    assert "--cpu" in capsys.readouterr().out


def test_cpu_flag_is_explicit_without_loading_models(case, monkeypatch):
    pipeline, target, sources, output = case
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return pipeline

    factory.process_image_multi_mapping = pipeline.process_image_multi_mapping
    monkeypatch.setattr("core.pipeline.PersonaWeavePipeline", factory)
    monkeypatch.setattr(
        "sys.argv",
        [
            "compare_source_strategies",
            "--target",
            str(target),
            "--source",
            str(sources[0]),
            "--person-index",
            "1",
            "--output-dir",
            str(output),
            "--cpu",
        ],
    )
    experiment.main()
    assert calls == [{"models_dir": None, "providers": ["CPUExecutionProvider"]}]


def test_single_reference_still_has_three_declared_strategies(case):
    pipeline, target, sources, output = case
    result = experiment.compare_source_strategies(pipeline, target, sources[:1], 1, output)
    assert result["declared_strategy_count"] == 3
    assert all(arm["reference_indices"] == [1] for arm in result["strategies"])
    for vector in pipeline.swap_vectors:
        np.testing.assert_allclose(vector, [1, 0])


def test_provider_specific_failure_retains_all_arms(case):
    pipeline, target, sources, output = case

    class ModelFailure(Exception):
        pass

    def fail(**kwargs):
        raise ModelFailure("synthetic inference failure")

    pipeline.swap_single_face_facefusion = fail
    result = experiment.compare_source_strategies(pipeline, target, sources, 1, output)
    assert len(result["strategies"]) == 3
    assert result["successful_strategy_count"] == 0
    assert all(arm["error_type"] == "ModelFailure" for arm in result["strategies"])


def test_source_mode_override_rejected_before_model_initialization(case, monkeypatch):
    _pipeline, target, sources, output = case
    parameters = target.parent / "parameters.json"
    parameters.write_text('{"source_mode": "best-match"}')

    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid parameters must not load models")

    monkeypatch.setattr(PersonaWeavePipeline, "__init__", forbidden)
    monkeypatch.setattr(
        "sys.argv",
        [
            "compare_source_strategies",
            "--target",
            str(target),
            "--source",
            str(sources[0]),
            "--person-index",
            "1",
            "--output-dir",
            str(output),
            "--parameters",
            str(parameters),
            "--cpu",
        ],
    )
    with pytest.raises(ValueError, match="variable controlled"):
        experiment.main()
