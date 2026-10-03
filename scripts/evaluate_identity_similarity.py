"""Non-production ArcFace identity-transfer audit: real target photos x portrait-library sources.

Run: uv run python -m scripts.evaluate_identity_similarity --target-list photos.txt --output-dir outputs/diagnostics/NEW-RUN

Every eligible face in every target photo is swapped with library identities through the production
multi-mapping path (library references -> production gallery/best-match -> process_image_multi_mapping).
The same ArcFace model that drives production matching then scores the result face against all active
identities. Scores are calibrated on the library itself (leave-one-out genuine vs cross-identity
impostor pairs), so a number is read against a false-accept rate instead of a guessed constant.
This measures identity transfer only: not naturalness, not different models, not different photos.
Skipped/failed swaps stay in the denominator. Output images contain real people: keep them private.
"""

import argparse
import random
from pathlib import Path

import cv2
import numpy as np

from scripts.compare_source_strategies import prepare_strategies, resolved_parameters
from scripts.swap_benchmark_utils import cosine, file_sha256, match_face_boxes, write_json

FAR_LEVELS = (0.01, 0.001)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-list", type=Path, required=True, help="Text file: one target photo path per line")
    parser.add_argument("--output-dir", type=Path, required=True, help="New private evidence directory")
    parser.add_argument("--identities-per-face", type=int, default=3)
    parser.add_argument("--min-face-px", type=int, default=200, help="Skip faces whose short side is smaller")
    parser.add_argument("--seed", type=int, default=20261003)
    parser.add_argument(
        "--strengths",
        type=float,
        nargs="+",
        default=[0.9],
        help="identity_blend_strength values; every value reuses the same faces and identity assignment",
    )
    return parser.parse_args(argv)


def unit(vector):
    vector = np.asarray(vector, dtype=np.float64)
    return vector / np.linalg.norm(vector)


def calibration(refs_by_identity, build_gallery, genders):
    """Leave-one-out genuine and cross-identity impostor cosines for centroid scoring.

    ``refs_by_identity`` maps identity -> list of (embedding, weight). The centroid is built with the
    production gallery function, so the calibration scores are the same quantity as a swap's score.
    """
    centroids = {
        name: unit(build_gallery([e for e, _ in refs], [w for _, w in refs])) for name, refs in refs_by_identity.items()
    }
    genuine, impostor, impostor_same_gender = [], [], []
    for name, refs in refs_by_identity.items():
        for index, (embedding, _weight) in enumerate(refs):
            rest = refs[:index] + refs[index + 1 :]
            held_out = unit(build_gallery([e for e, _ in rest], [w for _, w in rest]))
            genuine.append(float(unit(embedding) @ held_out))
            for other, centroid in centroids.items():
                if other != name:
                    score = float(unit(embedding) @ centroid)
                    impostor.append(score)
                    if genders[other] == genders[name]:
                        impostor_same_gender.append(score)
    return centroids, np.asarray(genuine), np.asarray(impostor), np.asarray(impostor_same_gender)


def far_threshold(impostor, far):
    """Smallest cosine accepted at no more than ``far`` impostor acceptance."""
    return float(np.quantile(impostor, 1 - far))


def rank_scores(scores, intended):
    """Closed-set rank of the intended identity among all library identities (ties count against)."""
    intended_score = scores[intended]
    rank = 1 + sum(score >= intended_score for name, score in scores.items() if name != intended)
    best_other = max(score for name, score in scores.items() if name != intended)
    return intended_score, rank, intended_score - best_other


def identify(result_embedding, centroids, intended):
    """Rank by cosine to each identity's production gallery centroid."""
    return rank_scores({name: float(unit(result_embedding) @ c) for name, c in centroids.items()}, intended)


def mean_ref_scores(embedding, units):
    """Mean cosine to each individual reference: no averaged 'mean face' that inflates similarity to everyone."""
    x = unit(embedding)
    return {name: float((reference_units @ x).mean()) for name, reference_units in units.items()}


def mean_ref_calibration(units, genders):
    """Genuine (other references of the same person) and impostor pools for the mean-reference score."""
    genuine, impostor, impostor_same_gender = [], [], []
    for name, own in units.items():
        pairs = own @ own.T
        genuine.extend(((pairs.sum(axis=1) - 1) / (len(own) - 1)).tolist())
        for other, theirs in units.items():
            if other != name:
                scores = (own @ theirs.T).mean(axis=1).tolist()
                impostor.extend(scores)
                if genders[other] == genders[name]:
                    impostor_same_gender.extend(scores)
    return np.asarray(genuine), np.asarray(impostor), np.asarray(impostor_same_gender)


def summarize(values):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return None
    return {
        "n": len(values),
        "mean": float(values.mean()),
        "median": float(np.median(values)),
        "p10": float(np.quantile(values, 0.1)),
        "p90": float(np.quantile(values, 0.9)),
    }


def aggregate(rows, thresholds):
    """Rates use every attempted row as denominator; unmatched/skipped rows count as misses."""
    attempted = len(rows)
    scored = [row for row in rows if row["status"] == "scored"]

    def rate(predicate):
        return sum(predicate(row) for row in scored) / attempted if attempted else None

    result = {
        "attempted": attempted,
        "scored": len(scored),
        "not_scored": {
            reason: sum(row["status"] == reason for row in rows)
            for reason in sorted({row["status"] for row in rows} - {"scored"})
        },
        "result_vs_intended": summarize([row["sim_result_intended"] for row in scored]),
        "original_vs_intended_before_swap": summarize([row["sim_original_intended"] for row in scored]),
        "result_vs_original_face": summarize([row["sim_result_original"] for row in scored]),
        "rank1_rate": rate(lambda row: row["rank"] == 1),
        "top5_rate": rate(lambda row: row["rank"] <= 5),
        "result_closer_to_original_than_intended": rate(
            lambda row: row["sim_result_original"] > row["sim_result_intended"]
        ),
        "margin_vs_best_other": summarize([row["margin"] for row in scored]),
        "face_mae": summarize([row["face_mae"] for row in scored if "face_mae" in row]),
        "result_det_score": summarize([row["result_det_score"] for row in scored if "result_det_score" in row]),
        "mean_ref_result_vs_intended": summarize([row["sim_mean_ref"] for row in scored if "sim_mean_ref" in row]),
        "mean_ref_rank1_rate": rate(lambda row: row.get("rank_mean_ref") == 1),
        "mean_ref_top5_rate": rate(lambda row: row.get("rank_mean_ref", 99) <= 5),
    }
    for name, threshold in thresholds.items():
        field = "sim_mean_ref" if name.startswith("mean_ref") else "sim_result_intended"
        result[f"accepted_at_{name}"] = rate(lambda row, f=field, t=threshold: row.get(f, -1) >= t)
    return result


def stratify(rows, thresholds):
    def band(value, edges, labels):
        return labels[sum(value >= edge for edge in edges)]

    groups = {
        "face_px": lambda r: band(r["face_px"], (300, 500), ("<300", "300-500", ">=500")),
        "abs_yaw": lambda r: band(abs(r["yaw"]), (0.15, 0.35), ("<0.15", "0.15-0.35", ">=0.35")),
        "gender": lambda r: r["gender"],
    }
    return {
        name: {
            label: aggregate([r for r in rows if key(r) == label], thresholds)
            for label in sorted({key(r) for r in rows})
        }
        for name, key in groups.items()
    }


def load_library(pipeline):
    """Active identities with production reference extraction (the same path a task uses)."""
    from api.portraits.library import connect
    from api.tasks.sources import build_source_references, resolve_source_locations

    with connect() as conn:
        identities = conn.execute(
            "SELECT id, gender FROM identities WHERE id NOT IN (SELECT identity_id FROM archived_identities) "
            "ORDER BY source_key"
        ).fetchall()
    library = {}
    for identity in identities:
        refs = build_source_references(pipeline, resolve_source_locations(identity["id"], [], []))
        if refs:
            library[identity["id"]] = {"gender": identity["gender"], "refs": refs}
    return library


def target_gender(attribute_model, image, face):
    """InsightFace genderage output: 1 male, 0 female; used only to pair sources, never as a score."""
    gender, _age = attribute_model.get(image, face)
    return "male" if int(gender) == 1 else "female"


def crop(image, bbox, size=160, margin=0.35):
    h, w = image.shape[:2]
    x1, y1, x2, y2 = bbox
    pad = (x2 - x1) * margin
    box = [int(max(0, x1 - pad)), int(max(0, y1 - pad)), int(min(w, x2 + pad)), int(min(h, y2 + pad))]
    return cv2.resize(image[box[1] : box[3], box[0] : box[2]], (size, size), interpolation=cv2.INTER_AREA)


def run(pipeline, attribute_model, targets, library, centroids, units, args, strength):
    parameters, _threshold = resolved_parameters(pipeline, {"identity_blend_strength": strength})
    rng = random.Random(args.seed)
    by_gender = {g: sorted(n for n, v in library.items() if v["gender"] == g) for g in ("male", "female")}
    rows, tiles = [], {}
    from utils.image_io import read_image

    for path in targets:
        image = read_image(str(path))
        faces = sorted(pipeline.detector.detect_faces(image), key=lambda face: face.bbox[0])
        for face in faces:
            pipeline.recognizer.extract_embedding(image, face)
        eligible = [
            (index, face)
            for index, face in enumerate(faces, 1)
            if min(face.bbox[2] - face.bbox[0], face.bbox[3] - face.bbox[1]) >= args.min_face_px
        ]
        genders = {index: target_gender(attribute_model, image, face) for index, face in eligible}
        plan = {index: rng.sample(by_gender[genders[index]], args.identities_per_face) for index, _face in eligible}
        for round_index in range(args.identities_per_face):
            mappings, meta = [], {}
            for index, face in eligible:
                identity = plan[index][round_index]
                refs = library[identity]["refs"]
                plans, _selection = prepare_strategies(pipeline, face, refs)
                mapping = dict(next(p for p in plans if p["strategy"] == "production_blend")["mapping"])
                mapping.update(target_index=index, target_embedding=face.embedding.copy())
                mappings.append(mapping)
                meta[index] = identity
            if not mappings:
                continue
            result = pipeline.process_image_multi_mapping(image=image.copy(), mappings=mappings, **parameters)
            outcomes = {item["person_index"]: item for item in result.person_outcomes}
            result_faces = pipeline.detector.detect_faces(result.output_image)
            matches = match_face_boxes([f.bbox for f in faces], [f.bbox for f in result_faces], 0.3)
            for index, face in eligible:
                identity = meta[index]
                row = {
                    "image": path.name,
                    "image_sha256": file_sha256(path),
                    "face_index": index,
                    "gender": genders[index],
                    "face_px": float(min(face.bbox[2] - face.bbox[0], face.bbox[3] - face.bbox[1])),
                    "yaw": float(pipeline_yaw(face)),
                    "identity": identity,
                    "outcome": outcomes.get(index, {}).get("status"),
                    "reason": outcomes.get(index, {}).get("reason"),
                }
                if row["outcome"] != "SWAPPED":
                    row["status"] = "swap_" + str(row["outcome"]).lower()
                elif index - 1 not in matches:
                    row["status"] = "result_face_missing"
                else:
                    result_face = result_faces[matches[index - 1][0]]
                    embedding = pipeline.recognizer.extract_embedding(result.output_image, result_face)
                    intended_score, rank, margin = identify(embedding, centroids, identity)
                    mean_scores = mean_ref_scores(embedding, units)
                    _mean, rank_mean_ref, _margin = rank_scores(mean_scores, identity)
                    row.update(
                        status="scored",
                        sim_result_intended=intended_score,
                        rank=rank,
                        margin=margin,
                        sim_mean_ref=mean_scores[identity],
                        rank_mean_ref=rank_mean_ref,
                        sim_original_intended=float(unit(face.embedding) @ centroids[identity]),
                        sim_result_original=cosine(embedding, face.embedding),
                        # Naturalness proxies only: how many pixels moved inside the face box and how
                        # confidently the detector still sees a face. Neither is a quality judgement.
                        face_mae=face_mean_abs_diff(image, result.output_image, face.bbox),
                        result_det_score=float(result_face.det_score),
                    )
                    reference = library[identity]["refs"][0]
                    tiles[(path.name, index, identity)] = {
                        "original": crop(image, face.bbox),
                        "result": crop(result.output_image, result_face.bbox),
                        "reference": crop(read_image(str(reference["path"])), reference["face"].bbox),
                    }
                rows.append(row)
    return rows, tiles


def face_mean_abs_diff(before, after, bbox):
    x1, y1, x2, y2 = (int(v) for v in bbox)
    x1, y1 = max(0, x1), max(0, y1)
    return float(np.abs(before[y1:y2, x1:x2].astype(np.int16) - after[y1:y2, x1:x2].astype(np.int16)).mean())


def pipeline_yaw(face):
    from core.face_metrics import calculate_yaw_ratio

    return calculate_yaw_ratio(face.kps)


def main():
    args = parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError("Use an empty output directory; existing evidence cannot be overwritten")
    targets = [Path(line.strip()) for line in args.target_list.read_text(encoding="utf-8").splitlines() if line.strip()]
    from insightface.model_zoo import get_model

    from core.pipeline import PersonaWeavePipeline

    pipeline = PersonaWeavePipeline()
    attribute_model = get_model("models/buffalo_l/genderage.onnx", providers=list(pipeline.providers))
    attribute_model.prepare(ctx_id=0)
    library = load_library(pipeline)
    refs = {name: [(r["embedding"], r["quality_score"]) for r in value["refs"]] for name, value in library.items()}
    genders = {name: value["gender"] for name, value in library.items()}
    centroids, genuine, impostor, impostor_same = calibration(
        refs, pipeline.recognizer.build_gallery_embedding, genders
    )
    # Sources are gender-matched to targets, so same-gender impostors are the relevant false-accept pool.
    units = {name: np.stack([unit(embedding) for embedding, _ in pairs]) for name, pairs in refs.items()}
    mean_genuine, mean_impostor, mean_impostor_same = mean_ref_calibration(units, genders)
    thresholds = {
        f"{pool}@{far}": far_threshold(scores, far)
        for pool, scores in (
            ("any", impostor),
            ("same_gender", impostor_same),
            ("mean_ref_same_gender", mean_impostor_same),
        )
        for far in FAR_LEVELS
    }
    runs, all_tiles = {}, {}
    for strength in args.strengths:
        rows, tiles = run(pipeline, attribute_model, targets, library, centroids, units, args, strength)
        runs[str(strength)] = {
            "overall": aggregate(rows, thresholds),
            "strata": stratify(rows, thresholds),
            "rows": rows,
        }
        all_tiles[strength] = tiles
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Faces scored at every strength, evenly spread: the first rows would otherwise all come from the first photos.
    common = sorted(set.intersection(*(set(tiles) for tiles in all_tiles.values())))
    if common:
        chosen = common[:: max(1, len(common) // 16)][:16]
        first = all_tiles[args.strengths[0]]
        sheet = [
            np.concatenate(
                [
                    first[key]["original"],
                    *(all_tiles[s][key]["result"] for s in args.strengths),
                    first[key]["reference"],
                ],
                axis=1,
            )
            for key in chosen
        ]
        cv2.imwrite(str(args.output_dir / "contact_sheet.png"), np.concatenate(sheet, axis=0))
    report = {
        "schema_version": 2,
        "kind": "identity-transfer-audit",
        "seed": args.seed,
        "identities": len(library),
        "references_per_identity": sorted({len(v["refs"]) for v in library.values()}),
        "calibration": {
            "genuine": summarize(genuine),
            "impostor_any_gender": summarize(impostor),
            "impostor_same_gender": summarize(impostor_same),
            "mean_ref_genuine": summarize(mean_genuine),
            "mean_ref_impostor_any_gender": summarize(mean_impostor),
            "mean_ref_impostor_same_gender": summarize(mean_impostor_same),
            "thresholds": thresholds,
            "genuine_accepted": {
                name: float(((mean_genuine if name.startswith("mean_ref") else genuine) >= value).mean())
                for name, value in thresholds.items()
            },
        },
        "strengths": args.strengths,
        "parameters": resolved_parameters(pipeline, None)[0],
        "providers": [str(p) for p in pipeline.providers],
        "runs": runs,
        "note": "Identity similarity only; calibrated on library references, not on independent real-person pairs.",
    }
    write_json(args.output_dir / "report.json", report)
    print(f"Wrote {args.output_dir / 'report.json'}: {sum(len(run['rows']) for run in runs.values())} attempted swaps")


if __name__ == "__main__":
    main()
