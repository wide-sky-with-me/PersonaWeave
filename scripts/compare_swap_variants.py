"""Render non-production, verified swap variants and an input/result manifest."""

import argparse
import hashlib
import inspect
from pathlib import Path

import cv2
import numpy as np

from scripts.swap_benchmark_utils import file_sha256, write_json

VARIANTS = {
    "production": {},
    "production_no_boundary_color": {"enable_color_transfer": False, "color_transfer_mode": "off"},
}


def parse_args():
    """Keep the original comparison CLI, adding no implicit target selection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--person-index", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--swapper-model", default="inswapper_128")
    return parser.parse_args()


def compare_variants(
    pipeline, target: Path, sources: list[Path], person_index: int, output_dir: Path, swapper_model: str
):
    """Bind the requested target face to its real embedding; reject no-op variants.

    A fresh directory prevents an older successful image being mistaken for a failed
    rerun. Failure entries survive in manifest.json, but never get an output path.
    """
    image = cv2.imread(str(target))
    if image is None:
        raise ValueError(f"Could not decode target: {target}")
    faces = sorted(pipeline.detector.detect_faces(image), key=lambda face: tuple(face.bbox))
    if not 1 <= person_index <= len(faces):
        raise ValueError(f"person-index {person_index} outside detected range 1..{len(faces)}")
    target_face = faces[person_index - 1]
    target_embedding = pipeline.recognizer.extract_embedding(image, target_face)
    references = pipeline.load_source_references([str(path) for path in sources])
    if not references:
        raise ValueError("No source references could be extracted")
    gallery = pipeline.recognizer.build_gallery_embedding([reference["embedding"] for reference in references])
    mapping = {
        "target_index": person_index,
        "target_embedding": target_embedding,
        "source_references": references,
        "gallery_embedding": gallery,
        "gallery_reference_weights": [1.0] * len(references),
        "source_mode": "best-match",
    }
    defaults = {
        name: parameter.default
        for name, parameter in inspect.signature(pipeline.process_image_multi_mapping).parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError("Use an empty output directory; old outputs must not masquerade as this run")
    manifest = {
        "schema_version": 1,
        "kind": "swap-variants",
        "target": {
            "path": str(target),
            "sha256": file_sha256(target),
            "pixel_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
            "shape": list(image.shape),
        },
        "sources": [{"path": str(path), "sha256": file_sha256(path)} for path in sources],
        "person_index": person_index,
        "target_bbox": target_face.bbox.tolist(),
        "source_mode": mapping["source_mode"],
        "gallery_reference_weights": mapping["gallery_reference_weights"],
        "loaded_source_paths": [reference.get("path") for reference in references],
        "variants": [],
    }
    manifest_path = output_dir / "manifest.json"
    write_json(manifest_path, manifest)
    for name, overrides in VARIANTS.items():
        parameters = {**defaults, **overrides, "swapper_model": swapper_model}
        entry = {"variant": name, "parameters": parameters, "status": "failed", "output": None, "error": None}
        try:
            result = pipeline.process_image_multi_mapping(image=image.copy(), mappings=[mapping], **parameters)
            entry.update(
                {
                    "faces_detected": result.faces_detected,
                    "faces_swapped": result.faces_swapped,
                    "person_outcomes": result.person_outcomes,
                    "model_routing": result.model_routing,
                    "elapsed_time_sec": result.elapsed_time_sec,
                }
            )
            if (
                result.faces_swapped != 1
                or result.output_image.shape != image.shape
                or np.array_equal(image, result.output_image)
            ):
                raise ValueError(
                    "Invalid variant: expected exactly one visible swap on the same canvas, not an unchanged input"
                )
            filename = f"{target.stem}_{name}.png"
            output = output_dir / filename
            if not cv2.imwrite(str(output), result.output_image):
                output.unlink(missing_ok=True)
                raise OSError(f"Failed to write variant: {output}")
            entry.update({"status": "valid", "output": filename, "output_sha256": file_sha256(output)})
        except (ValueError, RuntimeError, OSError, cv2.error) as exc:
            entry["error"] = str(exc)
        manifest["variants"].append(entry)
        write_json(manifest_path, manifest)
    return manifest


def main():
    """Run explicit local model inference only when this CLI is invoked."""
    from core.pipeline import PersonaWeavePipeline

    args = parse_args()
    pipeline = PersonaWeavePipeline(swapper_model=args.swapper_model)
    manifest = compare_variants(
        pipeline, args.target, args.source, args.person_index, args.output_dir, args.swapper_model
    )
    for entry in manifest["variants"]:
        print(f"{entry['variant']}: {entry['status']} -> {entry['output'] or entry['error']}")
    if any(entry["status"] != "valid" for entry in manifest["variants"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
