"""Render controlled swap variants for one explicitly mapped face."""

import argparse
from pathlib import Path

import cv2

from core.pipeline import PersonaWeavePipeline

VARIANTS = {
    "production": {},
    "production_no_boundary_color": {
        "enable_color_transfer": False,
        "color_transfer_mode": "off",
    },
}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--person-index", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--swapper-model", default="inswapper_128")
    return parser.parse_args()


def main():
    args = parse_args()
    pipeline = PersonaWeavePipeline(swapper_model=args.swapper_model)
    image = cv2.imread(str(args.target))
    if image is None:
        raise ValueError(f"Could not decode target: {args.target}")
    references = pipeline.load_source_references([str(path) for path in args.source])
    gallery = pipeline.recognizer.build_gallery_embedding([reference["embedding"] for reference in references])
    mapping = {
        "target_index": args.person_index,
        "source_references": references,
        "gallery_embedding": gallery,
        "source_mode": "best-match",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, options in VARIANTS.items():
        result = pipeline.process_image_multi_mapping(image=image, mappings=[mapping], **options)
        output = args.output_dir / f"{args.target.stem}_{name}.jpg"
        cv2.imwrite(str(output), result.output_image)
        print(f"{name}: {result.elapsed_time_sec:.3f}s -> {output}")


if __name__ == "__main__":
    main()
