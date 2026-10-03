"""Non-production: cut per-face target regions and pick source portraits for external diffusion swappers.

Run: uv run python -m scripts.diffusion_swap_prepare --trials trials.json --photo-dir DIR --work-dir NEW
``trials.json`` lists {image, face_index, identity} (the identity assignment shared with the other arms). Each
target is a square region around ONE face (so the swapper's own face finder cannot pick a neighbour) and each
source is that identity's first library reference, a single frontal portrait: these swappers take one source image,
not a gallery. Output names are zero-padded trial numbers so independent tools pair files by sort order.
"""

import argparse
import json
from pathlib import Path

import cv2

from scripts.evaluate_identity_similarity import load_library
from utils.image_io import read_image

REGION_SCALE = 1.9


def region(image, bbox):
    """Square crop centred on the face with replicated borders, so the face stays centred near edges."""
    x1, y1, x2, y2 = bbox
    side = int(max(x2 - x1, y2 - y1) * REGION_SCALE)
    cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
    padded = cv2.copyMakeBorder(image, side, side, side, side, cv2.BORDER_REPLICATE)
    left, top = cx - side // 2 + side, cy - side // 2 + side
    return padded[top : top + side, left : left + side]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=Path, required=True)
    parser.add_argument("--photo-dir", type=Path, required=True, help="Directory tree holding the target photos")
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.work_dir.exists() and any(args.work_dir.iterdir()):
        raise ValueError("Use an empty work directory")
    from core.pipeline import PersonaWeavePipeline

    pipeline = PersonaWeavePipeline()
    library = load_library(pipeline)
    photos = {path.name: path for path in args.photo_dir.rglob("*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"}}
    (args.work_dir / "targets").mkdir(parents=True)
    (args.work_dir / "sources").mkdir()
    manifest, cache = [], {}
    for number, trial in enumerate(json.loads(args.trials.read_text(encoding="utf-8"))):
        path = photos[trial["image"]]
        if path not in cache:
            image = read_image(str(path))
            cache = {path: (image, sorted(pipeline.detector.detect_faces(image), key=lambda f: f.bbox[0]))}
        image, faces = cache[path]
        face = faces[trial["face_index"] - 1]
        name = f"{number:03d}.png"
        cv2.imwrite(str(args.work_dir / "targets" / name), region(image, face.bbox))
        reference = library[trial["identity"]]["refs"][0]
        cv2.imwrite(str(args.work_dir / "sources" / name), read_image(str(reference["path"])))
        manifest.append({**trial, "number": number, "file": name, "source_reference": Path(reference["path"]).name})
    (args.work_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"Prepared {len(manifest)} trials in {args.work_dir}")


if __name__ == "__main__":
    main()
