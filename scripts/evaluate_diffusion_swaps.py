"""Non-production: score external diffusion swap results with the same ArcFace identity metrics.

Run (after scripts/diffusion_pair_patch.py + the pair runner produced results):
    uv run python -m scripts.evaluate_diffusion_swaps --label reface --manifest WORK/manifest.json \
        --base-dir BASE --results-dir OUT/results --output-dir NEW
Targets are the swapper's own aligned crops (``BASE/target_cropped/N.png``), so the original-face similarity and
the pixel difference are measured on the same canvas the model edited. ``manifest.json`` rows carry ``ok_number``
(pair index) and the library identity; unscored pairs stay in the denominator. Output uses the schema of
evaluate_identity_similarity (``runs[label]``) so reports can be tabulated together.
"""

import argparse
from pathlib import Path

import cv2

from scripts.evaluate_identity_similarity import (
    aggregate,
    build_scoring,
    face_mean_abs_diff,
    identify,
    load_library,
    mean_ref_scores,
    pipeline_yaw,
    rank_scores,
    stratify,
    unit,
)
from scripts.swap_benchmark_utils import cosine, write_json
from utils.image_io import read_image


def pad_for_detection(image):
    """Replicate-pad by half the side: the swappers' FFHQ-style crops are too tight for the face detector."""
    margin = image.shape[0] // 2
    return cv2.copyMakeBorder(image, margin, margin, margin, margin, cv2.BORDER_REPLICATE)


def largest_face(pipeline, image):
    faces = pipeline.detector.detect_faces(image)
    return max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])) if faces else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=float, help="Total sampling seconds, recorded as given")
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError("Use an empty output directory")
    import json

    from core.pipeline import PersonaWeavePipeline

    pipeline = PersonaWeavePipeline()
    library = load_library(pipeline)
    scoring = build_scoring(pipeline, library)
    rows = []
    for entry in json.loads(args.manifest.read_text(encoding="utf-8")):
        number = entry["ok_number"]
        row = {
            "image": entry["image"],
            "face_index": entry["face_index"],
            "gender": entry["gender"],
            "identity": entry["identity"],
        }
        target = pad_for_detection(read_image(str(args.base_dir / "target_cropped" / f"{number}.png")))
        found = sorted((args.results_dir / str(number)).glob("*.png"))
        face = largest_face(pipeline, target)
        if not found or face is None:
            rows.append(
                {**row, "status": "no_result" if not found else "target_face_missing", "yaw": 0.0, "face_px": 0.0}
            )
            continue
        raw_result = read_image(str(found[0]))
        side = target.shape[0] // 2  # padded target = crop + 2 * crop / 2; compare at the crop's resolution
        result = pad_for_detection(cv2.resize(raw_result, (side, side), interpolation=cv2.INTER_AREA))
        result_face = largest_face(pipeline, result)
        row.update(
            yaw=float(pipeline_yaw(face)), face_px=float(min(face.bbox[2] - face.bbox[0], face.bbox[3] - face.bbox[1]))
        )
        if result_face is None:
            rows.append({**row, "status": "result_face_missing"})
            continue
        original = pipeline.recognizer.extract_embedding(target, face)
        embedding = pipeline.recognizer.extract_embedding(result, result_face)
        score, rank, margin = identify(embedding, scoring.centroids, entry["identity"])
        mean_scores = mean_ref_scores(embedding, scoring.units)
        _m, rank_mean_ref, _g = rank_scores(mean_scores, entry["identity"])
        rows.append(
            {
                **row,
                "status": "scored",
                "sim_result_intended": score,
                "rank": rank,
                "margin": margin,
                "sim_mean_ref": mean_scores[entry["identity"]],
                "rank_mean_ref": rank_mean_ref,
                "sim_original_intended": float(unit(original) @ scoring.centroids[entry["identity"]]),
                "sim_result_original": cosine(embedding, original),
                "face_mae": face_mean_abs_diff(target, result, face.bbox),
                "result_det_score": float(result_face.det_score),
            }
        )
    args.output_dir.mkdir(parents=True)
    write_json(
        args.output_dir / "report.json",
        {
            "schema_version": 2,
            "kind": "diffusion-identity-audit",
            "arms": [args.label],
            "runs": {
                args.label: {
                    "overall": aggregate(rows, scoring.thresholds),
                    "strata": stratify(rows, scoring.thresholds),
                    "swap_seconds_total": args.seconds,
                    "rows": rows,
                }
            },
            "thresholds": scoring.thresholds,
            "note": "Scored on the swapper's aligned crops, single frontal source portrait, dlib-alignable faces only.",
        },
    )
    print(f"Scored {sum(r['status'] == 'scored' for r in rows)}/{len(rows)} pairs")


if __name__ == "__main__":
    main()
