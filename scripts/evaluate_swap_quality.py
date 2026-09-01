"""Audit swap identity against original faces and a directory of references.

The report is intentionally model-agnostic: it detects faces left-to-right, compares
original/result embeddings, and records the two closest reference identities. Use it
together with visual review; recognition scores are acceptance signals, not aesthetic
quality scores.
"""

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np

from core.detector import FaceDetector
from core.recognizer import FaceRecognizer
from utils.gpu_helper import get_optimal_providers


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--original-dir", type=Path, required=True)
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--result-suffix", default="_faceswapped")
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument(
        "--expected-map", type=Path, help="Optional JSON: image stem -> 1-based face index -> reference stem"
    )
    return parser.parse_args()


def detect_embeddings(path, detector, recognizer):
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"Could not decode image: {path}")
    faces = detector.detect_faces(image, confidence_threshold=0.25)
    faces.sort(key=lambda face: float(face.bbox[0]))
    return faces, [recognizer.extract_embedding(image, face) for face in faces]


def find_result(original, result_dir, suffix):
    candidates = sorted(result_dir.glob(f"{original.stem}{suffix}.*"))
    return candidates[0] if candidates else None


def main():
    args = parse_args()
    providers = get_optimal_providers()
    detector = FaceDetector(
        str(args.models_dir / "buffalo_l" / "det_10g.onnx"), providers=providers, confidence_threshold=0.25
    )
    recognizer = FaceRecognizer(str(args.models_dir / "buffalo_l" / "w600k_r50.onnx"), providers=providers)
    expected = json.loads(args.expected_map.read_text()) if args.expected_map else {}

    references = {}
    for path in sorted(args.reference_dir.iterdir()):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        _, embeddings = detect_embeddings(path, detector, recognizer)
        if embeddings:
            references[path.stem] = embeddings[0]

    rows = []
    for original in sorted(args.original_dir.iterdir()):
        if original.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        result = find_result(original, args.result_dir, args.result_suffix)
        if result is None:
            continue
        _, original_embeddings = detect_embeddings(original, detector, recognizer)
        _, result_embeddings = detect_embeddings(result, detector, recognizer)
        for index, (original_emb, result_emb) in enumerate(
            zip(original_embeddings, result_embeddings, strict=False), 1
        ):
            ranking = sorted(
                ((float(np.dot(result_emb, reference_emb)), name) for name, reference_emb in references.items()),
                reverse=True,
            )
            expected_name = expected.get(original.stem, {}).get(str(index))
            expected_similarity = (
                float(np.dot(result_emb, references[expected_name])) if expected_name in references else None
            )
            rows.append(
                {
                    "image": original.stem,
                    "face_index": index,
                    "original_result_similarity": round(float(np.dot(original_emb, result_emb)), 4),
                    "best_reference": ranking[0][1] if ranking else None,
                    "best_similarity": round(ranking[0][0], 4) if ranking else None,
                    "second_reference": ranking[1][1] if len(ranking) > 1 else None,
                    "second_similarity": round(ranking[1][0], 4) if len(ranking) > 1 else None,
                    "expected_reference": expected_name,
                    "expected_similarity": round(expected_similarity, 4) if expected_similarity is not None else None,
                    "expected_is_best": expected_name is None or (ranking and ranking[0][1] == expected_name),
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    csv_path = args.output.with_suffix(".csv")
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys() if rows else ["image"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} face records to {args.output} and {csv_path}")


if __name__ == "__main__":
    main()
