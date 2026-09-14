"""Lightweight execution-evidence tests: no model loading or network access."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from core.pipeline import PersonaWeavePipeline
from core.recognizer import FaceRecognizer
from core.swap_diagnostics import normalized_gallery_weights, select_source_embedding


def face():
    return SimpleNamespace(
        bbox=np.array([130.0, 120.0, 250.0, 260.0]),
        kps=np.array([[160, 170], [220, 170], [190, 205], [168, 235], [212, 235]], dtype=np.float32),
        embedding=None,
    )


def refs():
    return [
        {"embedding": np.array([1.0, 0.0]), "quality_score": 0.2, "yaw": 0.0, "path": "/private/a.png"},
        {"embedding": np.array([0.0, 1.0]), "quality_score": 1.8, "yaw": 0.0, "image": "data:secret"},
    ]


def test_source_recipe_matches_existing_embedding_and_actual_normalization():
    references = refs()
    raw_weights = [0.2, 1.8]
    gallery = FaceRecognizer.build_gallery_embedding([r["embedding"] for r in references], raw_weights)
    result, recipe = select_source_embedding(0.0, references, gallery, "best-match", raw_weights)
    expected = 0.8 * gallery + 0.2 * references[1]["embedding"]
    expected /= np.linalg.norm(expected)
    np.testing.assert_allclose(result, expected)
    assert recipe["selected_reference_index"] == 2
    assert recipe["gallery_weight"] == 0.8
    assert recipe["best_reference_weight"] == pytest.approx(0.2)
    # Gallery aggregation does not apply the best-reference quality clipping.
    np.testing.assert_allclose(recipe["gallery_reference_weights"], [0.1, 0.9])
    reconstructed = FaceRecognizer.build_gallery_embedding(
        [r["embedding"] for r in references], recipe["gallery_reference_weights"]
    )
    np.testing.assert_allclose(reconstructed, gallery)
    text = json.dumps(recipe, allow_nan=False)
    assert all(forbidden not in text for forbidden in ("/private", "data:", "embedding", "image", "path"))


def test_external_gallery_has_unknown_provenance_and_selection_keeps_ndarray_contract():
    pipeline = object.__new__(PersonaWeavePipeline)
    collector = {}
    output = pipeline.select_best_reference_embedding(face(), refs(), np.array([1.0, 0.0]), diagnostics=collector)
    assert isinstance(output, np.ndarray)
    assert collector["gallery_reference_weights"] is None
    assert collector["gallery_weights_known"] is False
    assert collector["reference_count"] == 2
    assert collector["selected_reference_index"] == 2


@pytest.mark.parametrize("mode,count", [("gallery", 2), ("best-match", 1)])
def test_gallery_only_recipe_does_not_claim_single_best_contribution(mode, count):
    gallery = np.array([0.6, 0.8])
    output, recipe = select_source_embedding(0.0, refs()[:count], gallery, mode, [1.0] * count)
    assert output is gallery
    assert recipe["selected_reference_index"] is None
    assert recipe["gallery_weight"] == 1.0
    assert recipe["best_reference_weight"] == 0.0
    assert recipe["gallery_weights_known"] is True


def test_gallery_normalization_preserves_float32_rule_and_no_quality_clipping():
    np.testing.assert_allclose(
        normalized_gallery_weights(3, [0.01, 2.0, 0.0]), np.array([0.01, 2.0, 0.0], dtype=np.float32) / np.float32(2.01)
    )
    assert normalized_gallery_weights(2, None) is None


class FlatSwapper:
    spec = SimpleNamespace(name="inswapper_128", input_size=(128, 128), crop_template="inswapper_128")

    @staticmethod
    def infer_aligned(crop, _embedding):
        return np.full_like(crop, 200)


def mask_pipeline():
    pipeline = object.__new__(PersonaWeavePipeline)
    pipeline.parser = None
    pipeline.occluder = None
    pipeline.region_parser = None
    return pipeline


def swap(pipeline, collector, **kwargs):
    return pipeline.swap_single_face_facefusion(
        np.full((400, 400, 3), 100, np.uint8),
        face(),
        np.ones(2),
        swapper=FlatSwapper(),
        enable_dense_mask=True,
        manual_mask=kwargs.pop("manual_mask", None),
        enable_color_transfer=False,
        identity_blend_strength=0.95,
        diagnostics=collector,
        **kwargs,
    )


def test_missing_masks_are_visible_and_collector_contains_only_safe_scalars():
    collector = {}
    output = swap(mask_pipeline(), collector)
    assert output.shape == (400, 400, 3)
    components = {item["name"]: item for item in collector["components"]}
    for name in ("dense", "occlusion", "glasses"):
        assert components[name]["status"] == "missing"
        assert components[name]["failure_code"] == name + "_model_unavailable"
    assert components["box"]["status"] == "applied"
    assert 0 < collector["bbox_alpha_mean"] <= 0.95
    assert 0 < collector["intersection_mean"] <= 1
    assert collector["requested_strength"] == 0.95
    json.dumps(collector, allow_nan=False)


def fail(*_args, **_kwargs):
    raise RuntimeError("/private/secret.png token=secret")


def test_mask_exceptions_are_sanitized_not_printed_or_exposed(capsys):
    pipeline = mask_pipeline()
    pipeline.parser = SimpleNamespace(extract_106_landmarks=fail)
    pipeline.occluder = SimpleNamespace(create_occlusion_mask=fail)
    pipeline.region_parser = SimpleNamespace(create_region_protection_mask=fail)
    collector = {}
    swap(pipeline, collector)
    components = {item["name"]: item for item in collector["components"]}
    for name in ("dense", "occlusion", "glasses"):
        assert components[name] == {"name": name, "status": "failed", "failure_code": name + "_mask_failed"}
    assert "secret" not in json.dumps(collector) + capsys.readouterr().out


@pytest.mark.parametrize("component", ["occlusion", "glasses", "manual"])
def test_protection_remains_effective_at_high_strength(component):
    pipeline = mask_pipeline()
    if component == "occlusion":
        pipeline.occluder = SimpleNamespace(create_occlusion_mask=lambda crop: np.zeros(crop.shape[:2], np.float32))
    if component == "glasses":
        pipeline.region_parser = SimpleNamespace(
            create_region_protection_mask=lambda crop, **_kwargs: np.zeros(crop.shape[:2], np.float32)
        )
    collector = {}
    output = swap(pipeline, collector, manual_mask=np.ones((400, 400)) if component == "manual" else None)
    assert np.all(output == 100)
    assert collector["bbox_alpha_mean"] == 0
    assert next(item for item in collector["components"] if item["name"] == component)["status"] == "applied"


def matching_pipeline(change=0, faces=None):
    pipeline = mask_pipeline()
    pipeline.swapper_model = "inswapper_128"
    pipeline.detector = SimpleNamespace(detect_faces=lambda _image: [face()] if faces is None else faces)

    def extract(_image, detected):
        detected.embedding = np.array([1.0, 0.0])
        return detected.embedding

    pipeline.recognizer = SimpleNamespace(
        extract_embedding=extract,
        compute_similarity=lambda _face, target: float(target[0]),
        build_gallery_embedding=FaceRecognizer.build_gallery_embedding,
    )
    pipeline.route_swapper = lambda *_args: SimpleNamespace(
        requested="auto", selected="inswapper_128", reason="test", fallback=False
    )
    pipeline.get_swapper = lambda _name: FlatSwapper()

    def pretend_swap(**kwargs):
        kwargs["diagnostics"].update(components=[], intersection_mean=0.5, bbox_alpha_mean=0.4, requested_strength=0.9)
        return kwargs["canvas"] + change

    pipeline.swap_single_face_facefusion = pretend_swap
    return pipeline


def mapping(person, similarity=None):
    value = {
        "target_index": person,
        "source_references": refs(),
        "gallery_embedding": np.array([1.0, 0.0]),
        "gallery_reference_weights": [0.2, 1.8],
        "similarity_threshold": 0.35,
    }
    if similarity is not None:
        value["target_embedding"] = np.array([similarity, 0.0])
    return value


def test_competition_missing_reference_and_threshold_have_distinct_reasons():
    result = matching_pipeline().process_image_multi_mapping(
        np.full((400, 400, 3), 100, np.uint8), [mapping(1, 0.8), mapping(2, 0.9), mapping(3), mapping(4, 0.2)]
    )
    outcomes = result.person_outcomes
    assert [item["reason"] for item in outcomes] == [
        "assignment_conflict",
        "no_visible_change",
        "target_reference_unavailable",
        "identity_match_below_threshold",
    ]
    assert result.faces_swapped == 0
    assert outcomes[0]["diagnostics"]["matching"]["best_similarity"] == pytest.approx(0.8)
    matching = outcomes[1]["diagnostics"]["matching"]
    assert matching["assigned_similarity"] == pytest.approx(0.9)
    assert matching["face_index"] == 1
    assert matching["threshold"] == 0.35
    assert outcomes[1]["diagnostics"]["source_selection"]["selected_reference_index"] == 2
    assert outcomes[1]["diagnostics"]["mask"]["bbox_alpha_mean"] == 0.4
    assert outcomes[2]["diagnostics"]["matching"]["best_similarity"] is None
    json.dumps(outcomes, allow_nan=False)


def test_zero_detected_faces_preserves_reason_and_matching_diagnostics():
    result = matching_pipeline(faces=[]).process_image_multi_mapping(np.zeros((40, 40, 3), np.uint8), [mapping(9, 1)])
    assert result.person_outcomes[0]["reason"] == "no_faces_detected"
    assert result.person_outcomes[0]["diagnostics"]["matching"]["face_index"] is None


def test_single_role_no_visible_change_keeps_source_mask_and_matching_evidence():
    result = matching_pipeline().process_image(
        np.full((400, 400, 3), 100, np.uint8),
        refs(),
        np.array([1.0, 0.0]),
        target_embedding=np.array([0.8, 0.0]),
        gallery_reference_weights=[0.2, 1.8],
    )
    assert result.faces_swapped == 0
    outcome = result.person_outcomes[0]
    assert outcome["reason"] == "no_visible_change"
    assert outcome["diagnostics"]["matching"]["assigned_similarity"] == pytest.approx(0.8)
    assert outcome["diagnostics"]["source_selection"]["gallery_weights_known"] is True
    assert outcome["diagnostics"]["mask"]["bbox_alpha_mean"] == 0.4


def test_single_role_without_target_reference_is_intentionally_unfiltered():
    result = matching_pipeline(change=10).process_image(
        np.full((400, 400, 3), 100, np.uint8),
        refs(),
        np.array([1.0, 0.0]),
    )
    assert result.faces_swapped == 1
    assert result.person_outcomes[0]["reason"] == "visible_change_confirmed"
    matching = result.person_outcomes[0]["diagnostics"]["matching"]
    assert matching == {"best_similarity": None, "assigned_similarity": None, "threshold": None, "face_index": 1}


@pytest.mark.parametrize("bad_mask", [np.full((256, 256), np.nan), np.zeros((7, 9))])
def test_malformed_optional_mask_reports_fixed_failure_and_does_not_pollute_alpha(bad_mask):
    pipeline = mask_pipeline()
    pipeline.occluder = SimpleNamespace(create_occlusion_mask=lambda _crop: bad_mask)
    collector = {}
    result = swap(pipeline, collector)
    component = next(item for item in collector["components"] if item["name"] == "occlusion")
    assert component == {"name": "occlusion", "status": "failed", "failure_code": "occlusion_mask_failed"}
    assert result.max() > 100
    json.dumps(collector, allow_nan=False)


def test_single_reference_gallery_ignores_zero_weight_like_recognizer():
    embedding = np.array([1.0, 0.0])
    gallery = FaceRecognizer.build_gallery_embedding([embedding], [0.0])
    output, recipe = select_source_embedding(0.0, refs()[:1], gallery, "best-match", [0.0])
    np.testing.assert_array_equal(output, gallery)
    assert recipe["gallery_reference_weights"] == [1.0]


def test_internal_uniform_gallery_has_known_provenance():
    value = mapping(1, 0.9)
    value.pop("gallery_embedding")
    value.pop("gallery_reference_weights")
    result = matching_pipeline().process_image_multi_mapping(np.zeros((400, 400, 3), np.uint8), [value])
    recipe = result.person_outcomes[0]["diagnostics"]["source_selection"]
    assert recipe["gallery_reference_weights"] == [0.5, 0.5]
    assert recipe["gallery_weights_known"] is True


def test_single_target_does_not_report_unrelated_faces_as_failures():
    wanted, unrelated = face(), face()
    unrelated.bbox = unrelated.bbox + np.array([100.0, 0, 100.0, 0])
    pipeline = matching_pipeline(change=10, faces=[unrelated, wanted])
    pipeline.recognizer.compute_similarity = lambda embedding, _target: float(embedding[0])

    def extract(_image, detected):
        detected.embedding = np.array([0.9 if detected is wanted else 0.1, 0.0])
        return detected.embedding

    pipeline.recognizer.extract_embedding = extract
    result = pipeline.process_image(
        np.zeros((400, 400, 3), np.uint8), refs(), np.array([1.0, 0.0]), target_embedding=np.array([1.0, 0.0])
    )
    assert result.faces_detected == 2
    assert result.faces_swapped == 1
    assert result.requested_person_indices == [1]
    assert len(result.person_outcomes) == 1
    assert result.person_outcomes[0]["status"] == "SWAPPED"
    assert result.person_outcomes[0]["diagnostics"]["matching"]["face_index"] == 1


def test_single_target_below_threshold_has_matching_evidence_without_swap():
    result = matching_pipeline().process_image(
        np.zeros((400, 400, 3), np.uint8), refs(), np.array([1.0, 0.0]), target_embedding=np.array([0.2, 0.0])
    )
    assert result.faces_swapped == 0
    outcome = result.person_outcomes[0]
    assert outcome["reason"] == "identity_match_below_threshold"
    assert outcome["diagnostics"]["matching"]["best_similarity"] == pytest.approx(0.2)
    assert outcome["diagnostics"]["matching"]["assigned_similarity"] is None
