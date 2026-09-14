"""Non-production same-person source-strategy comparison; no automatic winner.

Run --help without loading models. A fresh output directory holds private source
health metadata, exact manifests, PNG outputs and a three-slot comparison board.
"""

import argparse
import copy
import hashlib
import inspect
import json
from pathlib import Path

import cv2
import numpy as np

from core.face_metrics import calculate_reference_quality, calculate_yaw_ratio, has_visible_face_change
from scripts.source_quality import inspect_source_quality
from scripts.swap_benchmark_utils import file_sha256, write_json

STRATEGIES = ("best_only", "gallery", "production_blend")


def parse_args():
    """Parse experiment-only controls; production pipeline supplies swap defaults."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--person-index", type=int, required=True, help="1-based left-to-right target face")
    parser.add_argument("--output-dir", type=Path, required=True, help="Must be empty; private artifacts")
    parser.add_argument(
        "--parameters", type=Path, help="JSON object of existing pipeline parameters, plus similarity_threshold"
    )
    parser.add_argument("--cpu", action="store_true", help="Explicit CPUExecutionProvider; no GPU fallback guessing")
    parser.add_argument("--models-dir", type=Path)
    parser.add_argument("--minimum-quality", type=float, default=0.48, help="Experimental advisory threshold")
    parser.add_argument(
        "--minimum-sharpness", type=float, default=30.0, help="Experimental Laplacian variance threshold"
    )
    parser.add_argument(
        "--outlier-similarity", type=float, default=0.35, help="Experimental leave-one-out median cosine"
    )
    parser.add_argument("--peer-cohesion", type=float, default=0.55, help="Experimental peer-group median cosine")
    return parser.parse_args()


def load_single_face_references(pipeline, sources: list[Path]) -> list[dict]:
    """Read every declared source, rejecting ambiguous faces rather than picking leftmost.

    Uses production quality/pose calculations, but deliberately not the convenience
    loader which limits detection to one face and can silently hide ambiguity.
    """
    references = []
    for path in sources:
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"Could not decode source: {path}")
        faces = pipeline.detector.detect_faces(image, confidence_threshold=0.25)
        if len(faces) != 1:
            raise ValueError(f"Source {path} has {len(faces)} detected faces; provide exactly one face per source")
        face = faces[0]
        h, w = image.shape[:2]
        x1, y1 = np.maximum(0, np.floor(face.bbox[:2]).astype(int))
        x2, y2 = np.minimum([w, h], np.ceil(face.bbox[2:]).astype(int))
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            raise ValueError(f"Empty source face crop: {path}")
        references.append(
            {
                "path": str(path),
                "image": image,
                "face": face,
                "embedding": pipeline.recognizer.extract_embedding(image, face),
                "quality_score": calculate_reference_quality(image, face),
                "yaw": calculate_yaw_ratio(face.kps),
                "sharpness": float(cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()),
                "sha256": file_sha256(path),
                "pixel_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
            }
        )
    if not references:
        raise ValueError("At least one source reference is required")
    return references


def _parameter_overrides(processor, overrides: dict | None) -> tuple[dict, float | None]:
    """Validate the real callable's parameter contract before any model initialization."""
    defaults = {
        name: value.default
        for name, value in inspect.signature(processor).parameters.items()
        if value.default is not inspect.Parameter.empty
    }
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError("parameters must be a JSON object")
    overrides = {} if overrides is None else dict(overrides)
    if "source_mode" in overrides:
        raise ValueError(
            "source_mode is the variable controlled by the three experiment arms; remove it explicitly from the parameter file"
        )
    threshold = overrides.pop("similarity_threshold", None)
    if threshold is not None and (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not np.isfinite(threshold)
        or not -1 <= threshold <= 1
    ):
        raise ValueError("similarity_threshold must be a finite cosine threshold")
    unknown = set(overrides) - set(defaults)
    if unknown:
        raise ValueError(f"Unsupported production parameters: {sorted(unknown)}")
    if overrides.get("manual_mask") is not None:
        raise ValueError("This single-case experiment does not decode manual masks")
    parameters = {**defaults, **overrides}
    json.dumps(parameters, allow_nan=False)
    return parameters, threshold


def resolved_parameters(pipeline, overrides: dict | None) -> tuple[dict, float | None]:
    """Resolve actual model fallback once and share parameters unchanged across arms."""
    parameters, threshold = _parameter_overrides(pipeline.process_image_multi_mapping, overrides)
    parameters["swapper_model"] = parameters.get("swapper_model") or pipeline.swapper_model
    return parameters, threshold


def prepare_strategies(pipeline, target_face, references: list[dict]) -> tuple[list[dict], dict]:
    """Reuse production ranking, then express all three arms with existing source modes.

    best_only is a one-reference gallery, never a new production enum. Its pose guard
    sees only that selected reference, so an incompatible best reference can fail.
    """
    weights = [ref["quality_score"] for ref in references]
    gallery = pipeline.recognizer.build_gallery_embedding([ref["embedding"] for ref in references], weights)
    selection = {}
    pipeline.select_best_reference_embedding(
        target_face,
        references,
        gallery,
        "best-match",
        gallery_reference_weights=weights,
        diagnostics=selection,
    )
    selected = selection["selected_reference_index"] or (1 if len(references) == 1 else None)
    if selected is None or not 1 <= selected <= len(references):
        raise ValueError("Production selector did not identify an actual best reference")
    plans = []
    for name in STRATEGIES:
        indices = [selected] if name == "best_only" else list(range(1, len(references) + 1))
        refs = [references[index - 1] for index in indices]
        raw_weights = [1.0] if name == "best_only" else weights
        vector = pipeline.recognizer.build_gallery_embedding([ref["embedding"] for ref in refs], raw_weights)
        mode = "best-match" if name == "production_blend" else "gallery"
        recipe = {}
        pipeline.select_best_reference_embedding(
            target_face,
            refs,
            vector,
            mode,
            gallery_reference_weights=raw_weights,
            diagnostics=recipe,
        )
        plans.append(
            {
                "strategy": name,
                "reference_indices": indices,
                "planned_source_selection": recipe,
                "mapping": {
                    "source_references": refs,
                    "gallery_embedding": vector,
                    "gallery_reference_weights": raw_weights,
                    "source_mode": mode,
                },
            }
        )
    return plans, selection


def _panel(image, title, size=384):
    canvas = np.full((size + 40, size, 3), 25, np.uint8)
    if image is not None:
        h, w = image.shape[:2]
        scale = min(size / w, size / h)
        preview = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))))
        top, left = (size - preview.shape[0]) // 2, (size - preview.shape[1]) // 2
        canvas[top : top + preview.shape[0], left : left + preview.shape[1]] = preview
    else:
        cv2.putText(
            canvas, "FAILED - no valid output", (10, size // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (90, 90, 255), 1
        )
    cv2.putText(canvas, title, (8, size + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (240, 240, 240), 1)
    return canvas


def compare_source_strategies(
    pipeline,
    target: Path,
    sources: list[Path],
    person_index: int,
    output_dir: Path,
    *,
    parameters: dict | None = None,
    quality_thresholds: dict | None = None,
) -> dict:
    """Run one declared target and three fixed arms; retain failures in the denominator.

    PNG boards contain a FAILED placeholder, never an unchanged original pretending
    to be a successful treatment. Manifests contain no image/embedding arrays.
    """
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Use an empty output directory; existing evidence cannot be overwritten")
    actual_parameters, threshold = resolved_parameters(pipeline, parameters)
    image = cv2.imread(str(target))
    if image is None:
        raise ValueError(f"Could not decode target: {target}")
    # Match production's stable sort, including its tie semantics.
    faces = sorted(pipeline.detector.detect_faces(image), key=lambda face: face.bbox[0])
    if not 1 <= person_index <= len(faces):
        raise ValueError("person-index outside detected target range")
    target_face = faces[person_index - 1]
    target_embedding = pipeline.recognizer.extract_embedding(image, target_face)
    references = load_single_face_references(pipeline, sources)
    quality = inspect_source_quality(references, **(quality_thresholds or {}))
    plans, best_recipe = prepare_strategies(pipeline, target_face, references)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "kind": "source-strategy-comparison",
        "advisory_only": True,
        "declared_strategy_count": len(STRATEGIES),
        "successful_strategy_count": 0,
        "target": {
            "path": str(target),
            "sha256": file_sha256(target),
            "pixel_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
            "shape": list(image.shape),
            "person_index": person_index,
            "bbox": target_face.bbox.tolist(),
        },
        "sources": [
            {
                "reference_index": index,
                **{k: ref[k] for k in ("path", "sha256", "pixel_sha256", "quality_score", "sharpness", "yaw")},
            }
            for index, ref in enumerate(references, 1)
        ],
        "common_parameters": actual_parameters,
        "mapping_similarity_threshold_override": threshold,
        "requested_providers": list(pipeline.providers),
        "production_best_selection": best_recipe,
        "source_quality": quality,
        "strategies": [],
        "note": "Fixed three-arm denominator, no winner. Planned recipe is not proof of inference. Success means a visible local swap, not identity or naturalness verification.",
    }
    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    boards, face_boards = [], []
    h, w = image.shape[:2]
    x1, y1 = np.clip(np.floor(target_face.bbox[:2]).astype(int), [0, 0], [w, h])
    x2, y2 = np.clip(np.ceil(target_face.bbox[2:]).astype(int), [0, 0], [w, h])
    for plan in plans:
        name = plan["strategy"]
        mapping = copy.deepcopy(plan["mapping"])
        mapping.update(target_index=person_index, target_embedding=target_embedding.copy())
        if threshold is not None:
            mapping["similarity_threshold"] = threshold
        entry = {k: copy.deepcopy(v) for k, v in plan.items() if k != "mapping"}
        entry.update(status="failed", output=None, parameters=copy.deepcopy(actual_parameters), error=None)
        output = None
        try:
            result = pipeline.process_image_multi_mapping(
                image=image.copy(), mappings=[mapping], **copy.deepcopy(actual_parameters)
            )
            entry.update(
                faces_detected=result.faces_detected,
                faces_swapped=result.faces_swapped,
                person_outcomes=result.person_outcomes,
                model_routing=result.model_routing,
                elapsed_time_sec=result.elapsed_time_sec,
            )
            if result.faces_swapped != 1 or not has_visible_face_change(image, result.output_image, target_face.bbox):
                raise ValueError(
                    "No valid visible swap on the declared target face; unchanged/guard-skipped output is a failure"
                )
            filename = f"{target.stem}_{name}.png"
            path = output_dir / filename
            if not cv2.imwrite(str(path), result.output_image):
                path.unlink(missing_ok=True)
                raise OSError("Could not save experiment output")
            output = result.output_image
            entry.update(status="valid", output=filename, output_sha256=file_sha256(path))
            manifest["successful_strategy_count"] += 1
        except Exception as exc:
            # Offline experiment boundary: even provider-specific exception classes
            # retain this arm in the denominator; never convert them into a success.
            entry["error"] = str(exc)
            entry["error_type"] = type(exc).__name__
        manifest["strategies"].append(entry)
        write_json(manifest_path, manifest)
        boards.append(_panel(output, f"{name}: {entry['status']}"))
        face_boards.append(_panel(None if output is None else output[y1:y2, x1:x2], f"{name}: {entry['status']}"))
    for filename, panels in (("comparison.png", boards), ("face_comparison.png", face_boards)):
        path = output_dir / filename
        if not cv2.imwrite(str(path), np.concatenate(panels, axis=1)):
            raise OSError("Could not save comparison board")
        manifest[filename] = {"path": filename, "sha256": file_sha256(path)}
    write_json(manifest_path, manifest)
    return manifest


def main():
    """Initialize models only after argument parsing (not on import or --help)."""
    args = parse_args()
    from core.pipeline import PersonaWeavePipeline

    parameters = json.loads(args.parameters.read_text(encoding="utf-8")) if args.parameters else {}
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be a JSON object")
    _parameter_overrides(PersonaWeavePipeline.process_image_multi_mapping, parameters)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError("Use an empty output directory; existing evidence cannot be overwritten")
    pipeline = PersonaWeavePipeline(
        models_dir=str(args.models_dir) if args.models_dir else None,
        providers=["CPUExecutionProvider"] if args.cpu else None,
    )
    result = compare_source_strategies(
        pipeline,
        args.target,
        args.source,
        args.person_index,
        args.output_dir,
        parameters=parameters,
        quality_thresholds={
            name: getattr(args, name)
            for name in ("minimum_quality", "minimum_sharpness", "outlier_similarity", "peer_cohesion")
        },
    )
    print(f"Valid arms: {result['successful_strategy_count']}/{result['declared_strategy_count']}; no winner selected")
    if result["successful_strategy_count"] != result["declared_strategy_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
