"""Measure cross-image identity consistency for gallery and best-match source modes."""

import argparse
import os
import sys
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(PROJECT_ROOT))

from core.pipeline import PersonaWeavePipeline  # noqa: E402

IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".webp"}


def parse_args() -> argparse.Namespace:
    """Parse optional path and matching-threshold overrides."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=PROJECT_ROOT / "data/test_target")
    parser.add_argument(
        "--target-reference",
        type=Path,
        default=PROJECT_ROOT / "data/test_person_ref/person_to_replace.png",
    )
    parser.add_argument("--input-dir", type=Path, default=PROJECT_ROOT / "data/test_images")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "outputs/consistency_check")
    parser.add_argument("--models-dir", type=Path, default=PROJECT_ROOT / "models")
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Optional fixed target similarity threshold; defaults to an adaptive per-image cutoff.",
    )
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.35,
        help="Absolute confidence floor for target-reference matching (default: 0.35).",
    )
    return parser.parse_args()


def collect_images(directory: Path) -> list[Path]:
    """Return supported images in deterministic filename order."""
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)


def bbox_center(bbox: np.ndarray) -> np.ndarray:
    """Return the center point of an xyxy face bounding box."""
    return np.array([(bbox[0] + bbox[2]) * 0.5, (bbox[1] + bbox[3]) * 0.5], dtype=np.float32)


def find_original_target_face(
    pipeline: PersonaWeavePipeline,
    image: np.ndarray,
    target_embedding: np.ndarray,
    threshold: float | None,
    min_similarity: float = 0.35,
):
    """Find the target identity used by the selective batch swap."""
    scored_faces = []
    for face in pipeline.detector.detect_faces(image):
        embedding = pipeline.recognizer.extract_embedding(image, face)
        similarity = pipeline.recognizer.compute_similarity(embedding, target_embedding)
        scored_faces.append((similarity, face))
    if not scored_faces:
        raise RuntimeError("No face was detected in the input image.")

    scored_faces.sort(key=lambda item: item[0], reverse=True)
    if threshold is None:
        best_similarity = scored_faces[0][0]
        second_similarity = scored_faces[1][0] if len(scored_faces) > 1 else best_similarity - 1e-3
        threshold = (best_similarity + second_similarity) * 0.5

    relative_matches = [item for item in scored_faces if item[0] >= threshold]
    if not relative_matches:
        raise RuntimeError("No face in the input image matches the target reference.")
    confident_matches = [item for item in relative_matches if item[0] >= min_similarity]
    best_similarity, best_face = relative_matches[0]
    return best_face, best_similarity, len(confident_matches), threshold, best_similarity >= min_similarity


def extract_output_embedding(
    pipeline: PersonaWeavePipeline, image: np.ndarray, original_bbox: np.ndarray
) -> np.ndarray:
    """Extract the output face nearest to the original target location."""
    faces = pipeline.detector.detect_faces(image)
    if not faces:
        raise RuntimeError("No face was detected in the swapped output.")
    target_center = bbox_center(original_bbox)
    output_face = min(faces, key=lambda face: float(np.linalg.norm(bbox_center(face.bbox) - target_center)))
    return pipeline.recognizer.extract_embedding(image, output_face)


def print_statistics(mode: str, embeddings: list[np.ndarray]) -> None:
    """Print the cosine matrix and off-diagonal pairwise statistics."""
    stacked = np.stack(embeddings).astype(np.float32)
    matrix = stacked @ stacked.T
    pairwise = matrix[np.triu_indices(len(embeddings), k=1)]

    print(f"\n=== {mode} cosine similarity matrix ===")
    for row in matrix:
        print("  " + "  ".join(f"{value:.6f}" for value in row))
    print(
        f"{mode} off-diagonal statistics: "
        f"mean={pairwise.mean():.6f}, min={pairwise.min():.6f}, std={pairwise.std():.6f}"
    )


def run_mode(
    pipeline: PersonaWeavePipeline,
    mode: str,
    input_paths: list[Path],
    output_dir: Path,
    source_references: list[dict],
    gallery_embedding: np.ndarray,
    target_embedding: np.ndarray,
    threshold: float | None,
    min_similarity: float,
) -> tuple[list[np.ndarray], int, int]:
    """Run one complete batch mode and return re-extracted output embeddings."""
    mode_dir = output_dir / mode.replace("-", "_")
    mode_dir.mkdir(parents=True, exist_ok=True)
    output_embeddings = []
    confident_count = 0
    low_confidence_count = 0

    for input_path in input_paths:
        image = cv2.imread(os.fspath(input_path))
        if image is None:
            raise RuntimeError(f"Failed to read input image: {input_path}")
        original_face, target_similarity, matched_count, image_threshold, is_confident = find_original_target_face(
            pipeline, image, target_embedding, threshold, min_similarity
        )
        confidence_label = "[confident match]" if is_confident else "[low-confidence / likely not the reference person]"
        confident_count += int(is_confident)
        low_confidence_count += int(not is_confident)
        result = pipeline.process_image(
            image=image,
            source_references=source_references,
            gallery_embedding=gallery_embedding,
            target_embedding=target_embedding,
            similarity_threshold=image_threshold,
            source_mode=mode,
        )
        output_path = mode_dir / f"swapped_{input_path.name}"
        if not cv2.imwrite(os.fspath(output_path), result.output_image):
            raise RuntimeError(f"Failed to write output image: {output_path}")
        output_embeddings.append(extract_output_embedding(pipeline, result.output_image, original_face.bbox))
        print(
            f"[{mode}] {input_path.name}: detected={result.faces_detected}, "
            f"target_similarity={target_similarity:.4f}, threshold={image_threshold:.4f}, "
            f"min_similarity={min_similarity:.4f}, matched={matched_count}, swapped={result.faces_swapped}, "
            f"elapsed={result.elapsed_time_sec:.2f}s {confidence_label}"
        )

    return output_embeddings, confident_count, low_confidence_count


def print_confidence_summary(confident_count: int, low_confidence_count: int) -> None:
    """Explain what the consistency statistics can and cannot establish."""
    total = confident_count + low_confidence_count
    print("\n=== Target-reference confidence summary ===")
    print(f"confident match: {confident_count}/{total}")
    print(f"low-confidence / likely not the reference person: {low_confidence_count}/{total}")
    if low_confidence_count > confident_count:
        print(
            "Most inputs are low-confidence. The cross-image consistency metrics below therefore measure how "
            "consistently the same injected identity appears on different faces, not consistency from correctly "
            "reusing the same target person. These are different conclusions."
        )


def main() -> None:
    """Run both source modes against the repository's real test dataset."""
    args = parse_args()
    source_paths = collect_images(args.source_dir)
    input_paths = collect_images(args.input_dir)
    if not source_paths:
        raise RuntimeError(f"No source reference images found in: {args.source_dir}")
    if len(input_paths) < 2:
        raise RuntimeError(f"At least two batch input images are required in: {args.input_dir}")

    pipeline = PersonaWeavePipeline(models_dir=os.fspath(args.models_dir))
    source_references = pipeline.load_source_references([os.fspath(path) for path in source_paths])
    gallery_embedding = pipeline.recognizer.build_gallery_embedding([ref["embedding"] for ref in source_references])
    target_embedding = pipeline.extract_target_reference_embedding(os.fspath(args.target_reference))

    mode_embeddings = {}
    confidence_counts = None
    for mode in ("gallery", "best-match"):
        embeddings, confident_count, low_confidence_count = run_mode(
            pipeline=pipeline,
            mode=mode,
            input_paths=input_paths,
            output_dir=args.output_dir,
            source_references=source_references,
            gallery_embedding=gallery_embedding,
            target_embedding=target_embedding,
            threshold=args.threshold,
            min_similarity=args.min_similarity,
        )
        mode_embeddings[mode] = embeddings
        if confidence_counts is None:
            confidence_counts = (confident_count, low_confidence_count)

    print_confidence_summary(*confidence_counts)
    for mode, embeddings in mode_embeddings.items():
        print_statistics(mode, embeddings)


if __name__ == "__main__":
    main()
