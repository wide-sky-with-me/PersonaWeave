"""Offline asset preparation/verification for generated 4x4 portrait sheets; not a production pipeline.

Never calls an AI service. Crops explicitly approved sheets, measures SCRFD/ArcFace evidence,
and writes the existing importer's manifest only for complete, verified identities.
"""

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from core.detector import FaceDetector
from core.face_metrics import calculate_yaw_ratio
from core.recognizer import FaceRecognizer
from utils.image_io import read_image

LABELS = (
    "01_front",
    "02_left_30deg",
    "03_left_60deg",
    "04_left_80deg",
    "05_right_30deg",
    "06_right_60deg",
    "07_right_80deg",
    "08_chin_down_front",
    "09_chin_up_front",
    "10_left_75deg_chin_down",
    "11_right_75deg_chin_down",
    "12_gentle_smile_front",
    "13_broad_smile_front",
    "14_laughing_front",
    "15_surprised_front",
    "16_serious_front",
)


def prepare(root: Path, codes: list[str] | None = None) -> dict:
    """Write per-image measurements and idempotent import rows; fail closed on incomplete sheets."""
    roster = json.loads((root / "prompts.json").read_text())
    detector = FaceDetector("models/buffalo_l/det_10g.onnx", providers=["CPUExecutionProvider"])
    recognizer = FaceRecognizer("models/buffalo_l/w600k_r50.onnx", providers=["CPUExecutionProvider"])
    reports = root / "validation"
    reports.mkdir(exist_ok=True)
    for person in roster:
        code = person["code"]
        if codes and code not in codes:
            continue
        sheet = root / "sheets" / f"{code}.png"
        if not sheet.is_file():
            continue
        sheet_hash = hashlib.sha256(sheet.read_bytes()).hexdigest()
        overrides = root / "overrides" / code
        override_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(overrides.glob("*.png"))
        }
        report_path = reports / f"{code}.json"
        if report_path.is_file():
            prior = json.loads(report_path.read_text())
            if prior.get("sheet_sha256") == sheet_hash and prior.get("override_hashes", {}) == override_hashes:
                continue
        rows, vectors, problems = [], [], []
        directory = root / person["gender"] / f"{code}_age{person['age']}"
        directory.mkdir(parents=True, exist_ok=True)
        with Image.open(sheet) as image:
            image = image.convert("RGB")
            width, height = image.size
            for index, label in enumerate(LABELS):
                row, column = divmod(index, 4)
                box = (
                    round(column * width / 4),
                    round(row * height / 4),
                    round((column + 1) * width / 4),
                    round((row + 1) * height / 4),
                )
                path = directory / f"{code}_{label}.png"
                override = overrides / f"{label}.png"
                if override.is_file():
                    shutil.copyfile(override, path)
                else:
                    image.crop(box).save(path)
                pixels = read_image(path)
                faces = detector.detect_faces(pixels, confidence_threshold=0.5)
                record = {
                    "file": path.relative_to(root).as_posix(),
                    "label": label,
                    "category": "angle" if index < 11 else "expression",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "face_count": len(faces),
                }
                if len(faces) != 1:
                    problems.append(f"{label}: expected exactly one face, found {len(faces)}")
                    vectors.append(None)
                else:
                    face = faces[0]
                    embedding = recognizer.extract_embedding(pixels, face)
                    vectors.append(embedding)
                    x1, y1, x2, y2 = face.bbox
                    yaw = calculate_yaw_ratio(face.kps)
                    record.update(
                        yaw=round(yaw, 4),
                        detection_score=round(float(face.det_score), 4),
                        face_width=round(float(x2 - x1), 1),
                        face_height=round(float(y2 - y1), 1),
                    )
                    if min(x2 - x1, y2 - y1) < 80:
                        problems.append(f"{label}: face is too small")
                    if (index == 3 and yaw > -0.65) or (index == 6 and yaw < 0.65):
                        problems.append(f"{label}: requested profile is not supported by measured yaw {yaw:.3f}")
                rows.append(record)
        anchor = vectors[0]
        original_anchor = None
        original_path = root / "anchors" / f"{code}.png"
        if original_path.is_file():
            original_image = read_image(original_path)
            original_faces = detector.detect_faces(original_image, confidence_threshold=0.5)
            if len(original_faces) != 1:
                problems.append("Original identity anchor is not usable")
            else:
                original_anchor = recognizer.extract_embedding(original_image, original_faces[0])
        for row, vector in zip(rows, vectors, strict=True):
            if anchor is None or vector is None:
                continue
            similarity = float(np.dot(anchor, vector))
            row["similarity_to_front"] = round(similarity, 4)
            # This is an import-review threshold, not a changed production matching threshold.
            if similarity < 0.35:
                problems.append(f"{row['label']}: low identity consistency {similarity:.3f}")
            if original_anchor is not None:
                original_similarity = float(np.dot(original_anchor, vector))
                row["similarity_to_original_identity"] = round(original_similarity, 4)
                if original_similarity < 0.35:
                    problems.append(f"{row['label']}: identity drift from original {original_similarity:.3f}")
        if anchor is not None:
            np.save(reports / f"{code}-front.npy", anchor)
        report = {
            "code": code,
            "age": person["age"],
            "gender": person["gender"],
            "sheet_sha256": sheet_hash,
            "override_hashes": override_hashes,
            "valid": not problems,
            "problems": problems,
            "assets": rows,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(code, "OK" if not problems else "NEEDS_REVIEW", "; ".join(problems), flush=True)
    valid = []
    for person in roster:
        path = reports / f"{person['code']}.json"
        if path.is_file():
            report = json.loads(path.read_text())
            if report["valid"]:
                valid.append(report)
    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["id", "gender", "age", "category", "label", "file", "source_contact_sheet"]
        )
        writer.writeheader()
        for person in valid:
            for asset in person["assets"]:
                writer.writerow(
                    {
                        "id": person["code"],
                        "gender": person["gender"],
                        "age": person["age"],
                        "category": asset["category"],
                        "label": asset["label"],
                        "file": asset["file"],
                        "source_contact_sheet": f"sheets/{person['code']}.png",
                    }
                )
    summary = {"valid_identities": len(valid), "valid_assets": sum(len(p["assets"]) for p in valid)}
    (reports / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/portrait_refresh_20260923"))
    parser.add_argument("--codes", nargs="*")
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.codes)))
