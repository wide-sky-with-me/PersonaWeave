"""Weight-free benchmark contract tests using synthetic canvases and fake models."""

import copy
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from scripts.compare_swap_variants import compare_variants
from scripts.evaluate_swap_quality import audit_image, evaluate_directories, load_references, validate_expected_map
from scripts.summarize_swap_benchmarks import parse_args, summarize_reports
from scripts.swap_benchmark_utils import file_sha256, image_paths, match_face_boxes


def face(box, vector=(1.0, 0.0)):
    return SimpleNamespace(bbox=np.array(box, dtype=np.float32), vector=np.array(vector, dtype=np.float32))


def canvas(marker, shape=(40, 80, 3)):
    return np.full(shape, marker, dtype=np.uint8)


def fake_models():
    detections = {
        10: [face([0, 0, 20, 20]), face([40, 0, 60, 20])],
        20: [face([40, 0, 60, 20], (0.0, 1.0))],
        30: [face([0, 0, 20, 20], (0.0, 1.0))],
        40: [face([0, 0, 20, 20], (1.0, 0.0))],
        50: [],
    }
    detector = SimpleNamespace(detect_faces=lambda image, **_kwargs: detections[int(image[0, 0, 0])])
    recognizer = SimpleNamespace(extract_embedding=lambda _image, item: item.vector)
    return detector, recognizer


def save_image(path, marker):
    assert cv2.imwrite(str(path), canvas(marker))
    return path


@pytest.fixture
def audit_report(tmp_path):
    original, result, reference = [tmp_path / name for name in ("original", "result", "reference")]
    for directory in (original, result, reference):
        directory.mkdir()
    save_image(original / "photo.png", 10)
    save_image(result / "photo_faceswapped.png", 20)
    save_image(reference / "alice.png", 30)
    save_image(reference / "bob.png", 40)
    detector, recognizer = fake_models()
    return evaluate_directories(
        original, result, reference, {"photo": {"1": "alice", "2": "alice", "3": "ghost"}}, detector, recognizer
    )


def test_geometric_matching_does_not_shift_when_left_face_disappears():
    detector, recognizer = fake_models()
    rows, image = audit_image(
        "photo", canvas(10), canvas(20), {"1": "alice", "2": "alice"}, {"alice": np.array([0, 1])}, detector, recognizer
    )
    assert len(rows) == 2
    assert rows[0]["status"] == "result_face_missing"
    assert rows[0]["expected_similarity"] is None
    assert rows[1]["face_index"] == 2
    assert rows[1]["result_face_index"] == 1
    assert rows[1]["expected_similarity"] == pytest.approx(1)
    assert image["unmatched_result_faces"] == []


def test_iou_matching_is_global_one_to_one_and_thresholded():
    result = match_face_boxes([[0, 0, 10, 10], [5, 0, 15, 10]], [[0, 0, 12, 10], [-3, 0, 7, 10]], 0.3)
    assert {key: value[0] for key, value in result.items()} == {0: 1, 1: 0}
    assert match_face_boxes([[0, 0, 10, 10]], [[20, 20, 30, 30]]) == {}
    assert match_face_boxes([], [[0, 0, 10, 10]]) == {}
    with pytest.raises(ValueError, match="minimum_iou"):
        match_face_boxes([], [], 0)


@pytest.mark.parametrize(
    "result,status",
    [(None, "result_missing"), (canvas(50), "result_face_missing"), (canvas(20, (41, 80, 3)), "canvas_mismatch")],
)
def test_missing_or_resized_result_retains_original_and_expected_denominator(result, status):
    detector, recognizer = fake_models()
    rows, _ = audit_image("photo", canvas(10), result, {"1": "a", "2": "a", "3": "a"}, {}, detector, recognizer)
    assert [row["status"] for row in rows] == [status, status, "original_face_missing"]
    assert all(row["expected_is_best"] is None for row in rows)


def test_unlabeled_or_missing_reference_is_never_implicit_success():
    detector, recognizer = fake_models()
    rows, _ = audit_image("photo", canvas(10), canvas(20), {}, {"a": np.array([0, 1])}, detector, recognizer)
    assert rows[1]["label_status"] == "unlabeled"
    assert rows[1]["expected_is_best"] is None
    rows, _ = audit_image("photo", canvas(10), canvas(20), {"2": "missing"}, {}, detector, recognizer)
    assert rows[1]["label_status"] == "reference_missing"
    assert rows[1]["expected_is_best"] is None


def test_wrong_best_reference_does_not_count_as_expected_success():
    detector, recognizer = fake_models()
    rows, _ = audit_image(
        "photo",
        canvas(10),
        canvas(20),
        {"2": "alice"},
        {"alice": np.array([1, 0]), "bob": np.array([0, 1])},
        detector,
        recognizer,
    )
    assert rows[1]["best_reference"] == "bob"
    assert rows[1]["best_similarity"] == pytest.approx(1)
    assert rows[1]["expected_similarity"] == pytest.approx(0)
    assert rows[1]["expected_is_best"] is False


def test_multi_face_reference_is_rejected_instead_of_selecting_leftmost(tmp_path):
    detector, recognizer = fake_models()
    path = save_image(tmp_path / "multi.png", 10)
    with pytest.raises(ValueError, match="single-face"):
        load_references({"multi": path}, detector, recognizer)


def test_missing_files_and_expected_original_images_are_retained(tmp_path):
    dirs = [tmp_path / name for name in ("original", "result", "ref")]
    for directory in dirs:
        directory.mkdir()
    save_image(dirs[0] / "existing.png", 10)
    detector, recognizer = fake_models()
    report = evaluate_directories(*dirs, {"absent": {"1": "a"}}, detector, recognizer)
    assert len(report["inputs"]) == 2
    assert len(report["cohort"]) == 3
    assert {row["status"] for row in report["rows"]} == {"original_face_missing", "result_missing"}
    assert next(item for item in report["inputs"] if item["image"] == "absent")["sha256"] is None


def test_ambiguous_stems_and_invalid_labels_fail_explicitly(tmp_path):
    save_image(tmp_path / "duplicate.png", 10)
    save_image(tmp_path / "duplicate.jpg", 10)
    with pytest.raises(ValueError, match="Ambiguous"):
        image_paths(tmp_path)
    for labels in ([1], {"a": {"0": "b"}}, {"a": {"01": "b"}}, {"a": {"1": None}}):
        with pytest.raises(ValueError):
            validate_expected_map(labels)


def test_summary_uses_stable_keys_and_preserves_full_denominator(audit_report):
    shuffled = copy.deepcopy(audit_report)
    shuffled["rows"].reverse()
    summary = summarize_reports({"original": audit_report, "shuffled": shuffled})
    first, second = summary["variants"]
    assert {k: v for k, v in first.items() if k != "variant"} == {k: v for k, v in second.items() if k != "variant"}
    assert first["cohort_faces"] == first["labeled_faces"] == 3
    assert first["matched_faces"] == first["expected_evaluable_faces"] == 1
    assert first["reference_missing_faces"] == 1
    assert first["expected_unique_top1_fraction_all_labeled"] == pytest.approx(1 / 3)
    assert first["labeled_without_measurement"] == 2


def test_removed_measurement_and_unknown_status_cannot_shrink_denominator(audit_report):
    missing = copy.deepcopy(audit_report)
    missing["rows"] = []
    missing["images"] = []
    result = summarize_reports({"missing": missing})["variants"][0]
    assert result["cohort_faces"] == 3
    assert result["status_counts"]["audit_row_missing"] == 3
    assert result["image_status_counts"]["audit_image_missing"] == 1
    assert result["expected_unique_top1_fraction_all_labeled"] == 0
    unknown = copy.deepcopy(audit_report)
    unknown["rows"][1]["status"] = "new_unknown_state"
    result = summarize_reports({"unknown": unknown})["variants"][0]
    assert result["status_counts"]["unknown"] == 1
    assert result["expected_evaluable_faces"] == 0


def test_summary_does_not_use_arbitrary_best_scores(audit_report):
    report = copy.deepcopy(audit_report)
    report["rows"][1].update(expected_similarity=0.1, expected_is_best=False, best_similarity=0.99)
    result = summarize_reports({"wrong_identity": report})["variants"][0]
    assert result["mean_expected_similarity_available_only"] == pytest.approx(0.1)
    assert result["expected_unique_top1_count"] == 0


def test_unlabeled_cohort_counts_unknown_without_assigning_score(audit_report):
    report = copy.deepcopy(audit_report)
    report["inputs"][0]["expected"] = {}
    for item in report["cohort"] + report["rows"]:
        item["expected_reference"] = None
    result = summarize_reports({"unlabeled": report})["variants"][0]
    assert result["cohort_faces"] == result["unlabeled_faces"] == 3
    assert result["expected_evaluable_faces"] == 0
    assert result["expected_unique_top1_fraction_all_labeled"] is None


def test_summary_rejects_cherry_picked_cohort_and_duplicate_or_legacy_rows(audit_report):
    with pytest.raises(ValueError, match="schema_version=2"):
        summarize_reports({"legacy": audit_report["rows"]})
    picked = copy.deepcopy(audit_report)
    picked["cohort"].pop()
    picked["rows"].pop()
    with pytest.raises(ValueError, match="Declared expected face"):
        summarize_reports({"picked": picked})
    duplicate = copy.deepcopy(audit_report)
    duplicate["rows"].append(copy.deepcopy(duplicate["rows"][0]))
    with pytest.raises(ValueError, match="Duplicate face"):
        summarize_reports({"duplicate": duplicate})
    different = copy.deepcopy(audit_report)
    different["inputs"][0]["sha256"] = "different"
    with pytest.raises(ValueError, match="complete input hashes"):
        summarize_reports({"a": audit_report, "b": different})


def test_legacy_selection_cli_is_explicitly_rejected(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["summary", "--selection", "old.json", "--output", "out.json"])
    with pytest.raises(SystemExit) as error:
        parse_args()
    assert error.value.code == 2
    assert "--selection is retired" in capsys.readouterr().err


def comparison_pipeline(result_mode="valid"):
    target_faces = [face([0, 0, 20, 20], (1, 0)), face([40, 0, 60, 20], (0, 1))]
    calls = []

    def process(image, mappings, identity_blend_strength=0.9, swapper_model="inswapper_128", **_parameters):
        calls.append((mappings, identity_blend_strength, swapper_model))
        assert np.array_equal(mappings[0]["target_embedding"], target_faces[1].vector)
        return SimpleNamespace(
            faces_detected=2,
            faces_swapped=0 if result_mode == "zero_swaps" else 1,
            output_image=image if result_mode == "unchanged" else image + 10,
            person_outcomes=[{"person_index": 2, "status": "SWAPPED"}],
            model_routing=[{"selected": swapper_model}],
            elapsed_time_sec=0.01,
        )

    pipeline = SimpleNamespace(
        detector=SimpleNamespace(detect_faces=lambda _image: target_faces),
        recognizer=SimpleNamespace(
            extract_embedding=lambda _image, item: item.vector, build_gallery_embedding=lambda vectors: vectors[0]
        ),
        load_source_references=lambda _paths: [{"embedding": np.array([1, 0])}],
        process_image_multi_mapping=process,
    )
    return pipeline, calls


def test_compare_binds_real_target_and_writes_reproducible_manifest(tmp_path):
    target, source = save_image(tmp_path / "target.png", 10), save_image(tmp_path / "source.png", 30)
    pipeline, calls = comparison_pipeline()
    report = compare_variants(pipeline, target, [source], 2, tmp_path / "out", "inswapper_128")
    assert len(calls) == 2
    assert report["target"]["sha256"] == file_sha256(target)
    assert report["sources"][0]["sha256"] == file_sha256(source)
    assert report["gallery_reference_weights"] == [1.0]
    assert calls[0][0][0]["gallery_reference_weights"] == [1.0]
    assert all(item["status"] == "valid" for item in report["variants"])
    assert all(item["parameters"]["identity_blend_strength"] == 0.9 for item in report["variants"])
    assert json.loads((tmp_path / "out/manifest.json").read_text()) == report
    for entry in report["variants"]:
        assert entry["output_sha256"] == file_sha256(tmp_path / "out" / entry["output"])


@pytest.mark.parametrize("mode", ["zero_swaps", "unchanged"])
def test_compare_never_publishes_noop_as_valid_variant(tmp_path, mode):
    target, source = save_image(tmp_path / "target.png", 10), save_image(tmp_path / "source.png", 30)
    pipeline, _ = comparison_pipeline(mode)
    report = compare_variants(pipeline, target, [source], 2, tmp_path / "out", "inswapper_128")
    assert all(item["status"] == "failed" and item["output"] is None and item["error"] for item in report["variants"])
    assert list((tmp_path / "out").iterdir()) == [tmp_path / "out/manifest.json"]


@pytest.mark.parametrize("index", [-1, 0, 3])
def test_compare_rejects_invalid_person_index(tmp_path, index):
    target = save_image(tmp_path / "target.png", 10)
    pipeline, calls = comparison_pipeline()
    with pytest.raises(ValueError, match="outside detected range"):
        compare_variants(pipeline, target, [], index, tmp_path / "out", "inswapper_128")
    assert not calls


def test_summary_preserves_same_face_index_on_different_images(audit_report):
    report = copy.deepcopy(audit_report)
    report["inputs"].append({"image": "other", "sha256": "other-hash", "expected": {"1": "alice"}})
    report["cohort"].append({"image": "other", "face_index": 1, "expected_reference": "alice"})
    report["rows"].append(
        {
            "image": "other",
            "face_index": 1,
            "expected_reference": "alice",
            "status": "matched",
            "expected_similarity": 0.5,
            "expected_is_best": True,
        }
    )
    result = summarize_reports({"both_images": report})["variants"][0]
    assert result["cohort_faces"] == 4
    assert result["expected_evaluable_faces"] == 2
    assert result["mean_expected_similarity_available_only"] == pytest.approx(0.75)


def test_unmatched_result_faces_are_visible_and_do_not_replace_originals():
    detector, recognizer = fake_models()
    rows, image = audit_image("photo", canvas(20), canvas(10), {}, {}, detector, recognizer)
    assert len(rows) == 1
    assert image["unmatched_result_faces"] == [1]
    assert rows[0]["result_face_index"] == 2


def test_compare_refuses_directory_with_stale_outputs(tmp_path):
    target, source = save_image(tmp_path / "target.png", 10), save_image(tmp_path / "source.png", 30)
    output = tmp_path / "out"
    output.mkdir()
    stale = save_image(output / "target_production.png", 40)
    checksum = file_sha256(stale)
    pipeline, calls = comparison_pipeline()
    with pytest.raises(ValueError, match="empty output directory"):
        compare_variants(pipeline, target, [source], 2, output, "inswapper_128")
    assert not calls
    assert file_sha256(stale) == checksum


def test_corrupt_result_file_is_an_explicit_record(tmp_path):
    dirs = [tmp_path / name for name in ("original", "result", "ref")]
    for directory in dirs:
        directory.mkdir()
    save_image(dirs[0] / "photo.png", 10)
    (dirs[1] / "photo_faceswapped.png").write_bytes(b"not an image")
    detector, recognizer = fake_models()
    report = evaluate_directories(*dirs, {}, detector, recognizer)
    assert [row["status"] for row in report["rows"]] == ["result_decode_failed"] * 2
    assert report["images"][0]["result_sha256"]
