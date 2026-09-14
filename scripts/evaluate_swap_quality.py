"""Non-production identity audit with geometric association and explicit missing rows.

Report v2 retains the complete declared original-face cohort. Relative reference
ranking is identity evidence, never a naturalness score or an acceptance guarantee.
"""

import argparse
import csv
import json
from pathlib import Path

import cv2

from scripts.swap_benchmark_utils import cosine, file_sha256, image_paths, match_face_boxes, write_json

CSV_FIELDS = [
    "image",
    "face_index",
    "expected_reference",
    "label_status",
    "status",
    "result_face_index",
    "match_iou",
    "original_result_similarity",
    "expected_similarity",
    "expected_is_best",
    "best_reference",
    "best_similarity",
]


def parse_args():
    """Parse the existing directory interface plus a geometric IoU threshold."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-dir", type=Path, required=True)
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--result-suffix", default="_faceswapped")
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument(
        "--expected-map", type=Path, help="JSON: image stem -> 1-based original face index -> reference stem"
    )
    parser.add_argument("--minimum-iou", type=float, default=0.3)
    return parser.parse_args()


def validate_expected_map(value) -> dict:
    """Reject ambiguous labels instead of guessing image or face selection."""
    if not isinstance(value, dict):
        raise ValueError("expected-map must be an image-stem object")
    for image, mapping in value.items():
        if not isinstance(image, str) or not image or not isinstance(mapping, dict):
            raise ValueError("Expected image keys and face mapping objects")
        for index, reference in mapping.items():
            if not isinstance(index, str) or not index.isdecimal() or int(index) < 1 or str(int(index)) != index:
                raise ValueError("Expected face indices must be canonical positive integer strings")
            if not isinstance(reference, str) or not reference:
                raise ValueError("Expected references must be nonempty reference stems")
    return value


def detect_faces(image, detector):
    """Use stable left-to-right numbering only on each original canvas."""
    return sorted(detector.detect_faces(image, confidence_threshold=0.25), key=lambda face: tuple(face.bbox))


def load_references(paths, detector, recognizer):
    """Refuse zero/multi-face references; users must provide a single-face image."""
    references = {}
    for name, path in paths.items():
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"Invalid reference image: {path}")
        faces = detect_faces(image, detector)
        if len(faces) != 1:
            raise ValueError(f"Reference {name!r} has {len(faces)} faces; provide a single-face reference")
        references[name] = recognizer.extract_embedding(image, faces[0])
    return references


def audit_image(
    image_name,
    original,
    result,
    expected,
    references,
    detector,
    recognizer,
    minimum_iou=0.3,
    missing_status="result_missing",
):
    """Associate same-sized canvases by IoU, retaining unmatched/expected rows.

    Missing original detections declared by expected-map stay in the denominator.
    Without original geometry no positional or identity-based fallback is attempted.
    """
    if not 0 < minimum_iou <= 1:
        raise ValueError("minimum-iou must be in (0, 1]")
    original_faces = detect_faces(original, detector) if original is not None else []
    result_faces = []
    if original is None:
        image_status = "original_unavailable"
    elif result is None:
        image_status = missing_status
    elif original.shape != result.shape:
        image_status = "canvas_mismatch"
    else:
        image_status = "audited"
        result_faces = detect_faces(result, detector)
    matches = match_face_boxes(
        [face.bbox for face in original_faces], [face.bbox for face in result_faces], minimum_iou
    )
    indices = sorted(set(range(1, len(original_faces) + 1)) | {int(index) for index in expected})
    rows = []
    for face_index in indices:
        label = expected.get(str(face_index))
        row = dict.fromkeys(CSV_FIELDS)
        row.update(
            {
                "image": image_name,
                "face_index": face_index,
                "expected_reference": label,
                "label_status": "unlabeled"
                if label is None
                else "labeled"
                if label in references
                else "reference_missing",
                "status": "original_face_missing",
            }
        )
        if face_index > len(original_faces):
            rows.append(row)
            continue
        original_face = original_faces[face_index - 1]
        row["original_bbox"] = original_face.bbox.tolist()
        if image_status != "audited":
            row["status"] = image_status
        elif face_index - 1 not in matches:
            row["status"] = "result_face_missing"
        else:
            result_index, iou = matches[face_index - 1]
            original_embedding = recognizer.extract_embedding(original, original_face)
            result_embedding = recognizer.extract_embedding(result, result_faces[result_index])
            ranking = sorted(
                ((cosine(result_embedding, embedding), name) for name, embedding in references.items()),
                key=lambda pair: (-pair[0], pair[1]),
            )
            row.update(
                {
                    "status": "matched",
                    "result_face_index": result_index + 1,
                    "match_iou": iou,
                    "original_result_similarity": cosine(original_embedding, result_embedding),
                }
            )
            if ranking:
                row["best_similarity"], row["best_reference"] = ranking[0]
            if label in references:
                row["expected_similarity"] = cosine(result_embedding, references[label])
                # A tie is not evidence that the intended identity won uniquely.
                row["expected_is_best"] = all(
                    row["expected_similarity"] > score for score, name in ranking if name != label
                )
        rows.append(row)
    used = {index for index, _iou in matches.values()}
    return rows, {
        "image": image_name,
        "status": image_status,
        "original_faces_detected": len(original_faces),
        "result_faces_detected": len(result_faces),
        "unmatched_result_faces": [index + 1 for index in range(len(result_faces)) if index not in used],
    }


def evaluate_directories(
    original_dir,
    result_dir,
    reference_dir,
    expected,
    detector,
    recognizer,
    result_suffix="_faceswapped",
    minimum_iou=0.3,
):
    """Audit every original file and declared expected image, including absent outputs."""
    expected = validate_expected_map(expected)
    originals, results, reference_paths = image_paths(original_dir), image_paths(result_dir), image_paths(reference_dir)
    if not originals and not expected:
        raise ValueError("No original images or declared expected faces to audit")
    references = load_references(reference_paths, detector, recognizer)
    report = {
        "schema_version": 2,
        "kind": "swap-quality",
        "minimum_iou": minimum_iou,
        "reference_hashes": {name: file_sha256(path) for name, path in reference_paths.items()},
        "inputs": [],
        "cohort": [],
        "rows": [],
        "images": [],
    }
    for name in sorted(originals.keys() | expected.keys()):
        original_path, result_path = originals.get(name), results.get(name + result_suffix)
        original = cv2.imread(str(original_path)) if original_path else None
        result = cv2.imread(str(result_path)) if result_path else None
        rows, image_record = audit_image(
            name,
            original,
            result,
            expected.get(name, {}),
            references,
            detector,
            recognizer,
            minimum_iou,
            "result_decode_failed" if result_path else "result_missing",
        )
        report["inputs"].append(
            {
                "image": name,
                "sha256": file_sha256(original_path) if original_path else None,
                "expected": expected.get(name, {}),
            }
        )
        image_record.update(
            {
                "result_path": str(result_path) if result_path else None,
                "result_sha256": file_sha256(result_path) if result_path else None,
            }
        )
        report["images"].append(image_record)
        report["rows"].extend(rows)
        report["cohort"].extend(
            {"image": row["image"], "face_index": row["face_index"], "expected_reference": row["expected_reference"]}
            for row in rows
        )
    expected_result_stems = {name + result_suffix for name in originals.keys() | expected.keys()}
    report["unexpected_result_images"] = sorted(results.keys() - expected_result_stems)
    return report


def main():
    """Initialize existing models only for an explicitly requested offline audit."""
    args = parse_args()
    from core.detector import FaceDetector
    from core.recognizer import FaceRecognizer
    from utils.gpu_helper import get_optimal_providers

    expected = json.loads(args.expected_map.read_text(encoding="utf-8")) if args.expected_map else {}
    providers = get_optimal_providers()
    detector = FaceDetector(
        str(args.models_dir / "buffalo_l/det_10g.onnx"), providers=providers, confidence_threshold=0.25
    )
    recognizer = FaceRecognizer(str(args.models_dir / "buffalo_l/w600k_r50.onnx"), providers=providers)
    report = evaluate_directories(
        args.original_dir,
        args.result_dir,
        args.reference_dir,
        expected,
        detector,
        recognizer,
        args.result_suffix,
        args.minimum_iou,
    )
    report["models"] = {
        "detector_sha256": file_sha256(args.models_dir / "buffalo_l/det_10g.onnx"),
        "recognizer_sha256": file_sha256(args.models_dir / "buffalo_l/w600k_r50.onnx"),
    }
    write_json(args.output, report)
    with args.output.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["rows"])
    print(f"Wrote v2 audit: {len(report['inputs'])} images, {len(report['cohort'])} cohort faces -> {args.output}")


if __name__ == "__main__":
    main()
