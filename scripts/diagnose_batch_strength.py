"""Non-production CPU replay of a persisted task; never changes task state.

Run with uv run python -m scripts.diagnose_batch_strength TASK_ID --output DIR.
Output contains private diagnostic portraits and is not published to the API.
"""

import argparse
import copy
import hashlib
import inspect
import json
import sqlite3
import sys
from pathlib import Path

import cv2
import numpy as np

from api.media import load_image_from_source
from api.schemas import BatchTaskCreateRequest
from api.tasks.sources import build_source_references, resolve_source_locations
from core.face_metrics import calculate_yaw_ratio
from core.pipeline import PersonaWeavePipeline


def tile(image, label, size=256):
    """Fit a crop without distortion and add its label."""
    h, w = image.shape[:2]
    scale = min(size / w, size / h)
    resized = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))))
    canvas = np.full((size + 32, size, 3), 30, np.uint8)
    y, x = (size - resized.shape[0]) // 2, (size - resized.shape[1]) // 2
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    cv2.putText(canvas, label, (5, size + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    return canvas


def write_image(path: Path, image: np.ndarray) -> None:
    """Fail explicitly instead of claiming a missing diagnostic artifact exists."""
    if not cv2.imwrite(str(path), image):
        raise OSError(f"Could not write diagnostic image: {path}")


def source_reference_board(references: list[dict], selection: dict) -> np.ndarray:
    """Show the actual gallery and selection coefficients, not a synthetic identity photo.

    Gallery weights describe averaging before gallery L2 normalization; the best
    reference coefficient is a separate blend with that normalized gallery.
    """
    if not references:
        raise ValueError("Cannot render a source board without actual source references")
    selected_index = selection.get("selected_reference_index")
    weights = selection.get("gallery_reference_weights")
    if weights is not None and len(weights) != len(references):
        raise ValueError("Source gallery diagnostic weights do not match the reference count")
    panels = []
    for index, ref in enumerate(references, 1):
        image, face = ref["image"], ref["face"]
        h, w = image.shape[:2]
        x1, y1 = np.floor(face.bbox[:2]).astype(int)
        x2, y2 = np.ceil(face.bbox[2:]).astype(int)
        x1, x2 = np.clip([x1, x2], 0, w)
        y1, y2 = np.clip([y1, y2], 0, h)
        if x2 <= x1 or y2 <= y1:
            raise ValueError("Source reference face crop is empty")
        weight = "unknown" if weights is None else f"{weights[index - 1]:.3f}"
        label = f"Ref #{index}: g={weight}" + (" BEST" if index == selected_index else "")
        panels.append(tile(image[y1:y2, x1:x2], label, size=192))
    columns = min(4, len(panels))
    while len(panels) % columns:
        panels.append(np.zeros_like(panels[0]))
    rows = [np.concatenate(panels[start : start + columns], axis=1) for start in range(0, len(panels), columns)]
    board = np.concatenate(rows, axis=0)
    header = np.full((70, board.shape[1], 3), 30, np.uint8)
    gallery = selection.get("gallery_weight")
    best = selection.get("best_reference_weight")
    gallery_text = "unknown" if gallery is None else f"{gallery:.2f}"
    best_text = "unknown" if best is None else f"{best:.2f}"
    description = f"{selection.get('mode', 'unknown')}: gallery={gallery_text}, best={best_text}"
    cv2.putText(header, description, (5, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    cv2.putText(
        header,
        "Actual input = embedding mixture; not one photo",
        (5, 52),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.35,
        (255, 255, 255),
        1,
    )
    return np.concatenate([header, board], axis=0)


def main():
    """Replay unchanged mappings and measure intermediate versus final changes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Use a new empty output directory; diagnostic evidence is never overwritten")
    args.output.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect("file:data/task/tasks.sqlite3?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT request_json FROM api_tasks WHERE kind = ? AND task_id = ?", ("batch", args.task_id)
        ).fetchone()
    if row is None or row[0] is None:
        raise ValueError("Task or replay request is unavailable")
    request = BatchTaskCreateRequest.model_validate_json(row[0])
    if not request.person_mappings:
        raise ValueError("This diagnostic requires explicit multi-person mappings")
    if any(item.manual_mask for item in request.normalized_batch_images):
        raise ValueError("This diagnostic currently requires a task without manual masks")
    pipeline = PersonaWeavePipeline(providers=["CPUExecutionProvider"])
    mappings = []
    for item in request.person_mappings:
        image = load_image_from_source(item.target_reference_url)
        faces = sorted(pipeline.detector.detect_faces(image, confidence_threshold=0.25), key=lambda f: f.bbox[0])
        target = pipeline.recognizer.extract_embedding(image, faces[item.target_reference_person_index - 1])
        refs = build_source_references(
            pipeline,
            resolve_source_locations(item.source_identity_id, item.source_asset_ids, item.source_manual_images),
        )
        mappings.append(
            {
                "target_index": item.person_index,
                "target_embedding": target,
                "source_references": refs,
                "gallery_reference_weights": [r["quality_score"] for r in refs],
                "gallery_embedding": pipeline.recognizer.build_gallery_embedding(
                    [r["embedding"] for r in refs], [r["quality_score"] for r in refs]
                ),
                "source_mode": item.source_mode,
                "similarity_threshold": request.parameters.similarity_threshold,
            }
        )
    report = {
        "schema_version": 2,
        "metric_note": "Cosine scores compare to the actual mixed input embedding, not a pictured reference or naturalness.",
        "task_id": args.task_id,
        "request_sha256": hashlib.sha256(row[0].encode()).hexdigest(),
        "parameters": request.parameters.model_dump(),
        "provider": "CPUExecutionProvider",
        "images": [],
    }
    original_swap = pipeline.swap_single_face_facefusion
    original_select = pipeline.select_best_reference_embedding
    selection_context = {}

    def traced_selection(*args, **kwargs):
        # Capture the actual production choice and its own reference list, never a
        # nearest-photo guess across unrelated people. No embeddings leave the report.
        kwargs.setdefault("diagnostics", {})
        bound = inspect.signature(original_select).bind(*args, **kwargs)
        result = original_select(*args, **kwargs)
        selection_context.clear()
        selection_context.update(
            references=bound.arguments["source_references"],
            metadata=copy.deepcopy(kwargs["diagnostics"]),
        )
        return result

    pipeline.select_best_reference_embedding = traced_selection
    for image_index, item in enumerate(request.normalized_batch_images, 1):
        image = load_image_from_source(item.image_url)
        faces = sorted(pipeline.detector.detect_faces(image), key=lambda f: f.bbox[0])
        embeddings = [pipeline.recognizer.extract_embedding(image, f) for f in faces]
        record = {
            "image": image_index,
            "input_pixel_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
            "faces": [{"bbox": f.bbox.tolist(), "yaw": calculate_yaw_ratio(f.kps)} for f in faces],
            "mapping_similarities": [
                [round(pipeline.recognizer.compute_similarity(m["target_embedding"], e), 5) for e in embeddings]
                for m in mappings
            ],
            "trials": [],
        }
        sheets = {}
        for strength in (0.72, 0.9, 0.95):
            measurements = []

            def measured_swap(
                measurements=measurements, sheets=sheets, strength=strength, image_index=image_index, **kwargs
            ):
                captured = {}

                def profile(frame, event, _arg):
                    if event == "return" and frame.f_code is original_swap.__func__.__code__:
                        captured.update(frame.f_locals)

                previous_profile = sys.getprofile()
                sys.setprofile(profile)
                try:
                    candidate = original_swap(**kwargs)
                finally:
                    sys.setprofile(previous_profile)
                face = kwargs["target_face"]
                before = kwargs["canvas"]
                h, w = before.shape[:2]
                x1, y1 = np.floor(face.bbox[:2]).astype(int)
                x2, y2 = np.ceil(face.bbox[2:]).astype(int)
                x1, x2 = np.clip([x1, x2], 0, w)
                y1, y2 = np.clip([y1, y2], 0, h)
                crop_before, crop_after = before[y1:y2, x1:x2], candidate[y1:y2, x1:x2]
                source = kwargs["source_embedding"]
                before_emb = pipeline.recognizer.extract_embedding(before, copy.copy(face))
                after_emb = pipeline.recognizer.extract_embedding(candidate, copy.copy(face))
                measurement = {
                    "face_index": face.index,
                    "bbox_mae": float(cv2.absdiff(crop_before, crop_after).mean()),
                    "aligned_output_mae": float(cv2.absdiff(captured["crop"], captured["swapped_crop"]).mean()),
                    "mask_means": [float(m.mean()) for m in captured["masks"]],
                    "intersection_mean": float(captured["crop_mask"].mean()),
                    "bbox_alpha_mean": float(captured["warped_mask"][y1:y2, x1:x2].mean()),
                    "similarity_to_mixed_source_before": pipeline.recognizer.compute_similarity(before_emb, source),
                    "similarity_to_mixed_source_after": pipeline.recognizer.compute_similarity(after_emb, source),
                    "source_selection": copy.deepcopy(selection_context["metadata"]),
                }
                measurements.append(measurement)
                if face.index not in sheets:
                    refs = selection_context["references"]
                    selection = selection_context["metadata"]
                    board = source_reference_board(refs, selection)
                    write_image(args.output / f"image_{image_index}_face_{face.index}_sources.jpg", board)
                    mask_image = (captured["crop_mask"] * 255).astype(np.uint8)[:, :, None].repeat(3, axis=2)
                    sheets[face.index] = [
                        tile(board, "Actual gallery (see source board)"),
                        tile(crop_before, "Original"),
                        tile(captured["swapped_crop"], "Aligned output"),
                        tile(mask_image, "Keep mask: white = swap"),
                    ]
                label = "request {:.2f}: MAE {:.2f}".format(strength, measurement["bbox_mae"])
                sheets[face.index].append(tile(crop_after, label))
                return candidate

            pipeline.swap_single_face_facefusion = measured_swap
            params = request.parameters.model_dump()
            allowed = {
                "enable_color_transfer",
                "color_transfer_mode",
                "color_transfer_ring_strength",
                "color_transfer_ring_width_px",
                "enable_dense_mask",
                "swapper_model",
                "allow_research_models",
                "enable_face_restoration",
                "restoration_fidelity_weight",
                "restoration_strength",
            }
            result = pipeline.process_image_multi_mapping(
                image, mappings, identity_blend_strength=strength, **{k: v for k, v in params.items() if k in allowed}
            )
            record["trials"].append(
                {
                    "strength": strength,
                    "swapped": result.faces_swapped,
                    "outcomes": result.person_outcomes,
                    "measurements": measurements,
                }
            )
            write_image(args.output / f"image_{image_index}_strength_{strength}.jpg", result.output_image)
            print(
                json.dumps(
                    {
                        "image": image_index,
                        "strength": strength,
                        "swapped": result.faces_swapped,
                        "measurements": measurements,
                    }
                ),
                flush=True,
            )
        for face_index, panels in sheets.items():
            write_image(args.output / f"image_{image_index}_face_{face_index}.jpg", np.concatenate(panels, axis=1))
        report["images"].append(record)
        (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    pipeline.swap_single_face_facefusion = original_swap
    pipeline.select_best_reference_embedding = original_select


if __name__ == "__main__":
    main()
