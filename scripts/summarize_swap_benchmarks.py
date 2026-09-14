"""Summarize full v2 audit cohorts by stable keys, never hand-picked row offsets.

The old --selection/bare-row-list format is rejected: it cannot establish a common
coverage denominator. Regenerate those files with evaluate_swap_quality first.
"""

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path

from scripts.swap_benchmark_utils import write_json

KNOWN_STATUSES = {
    "matched",
    "result_missing",
    "result_decode_failed",
    "canvas_mismatch",
    "original_face_missing",
    "original_unavailable",
    "result_face_missing",
}


def parse_args():
    """Require named full reports rather than ambiguous positional selections."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="append", help="VARIANT=REPORT.json; repeat for each variant")
    parser.add_argument("--selection", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.selection is not None:
        parser.error(
            "--selection is retired: positional cherry-picking is ambiguous; regenerate v2 reports and use --audit VARIANT=REPORT"
        )
    if not args.audit:
        parser.error("At least one --audit VARIANT=REPORT is required")
    return args


def row_key(row):
    """Require explicit image identity and a positive original-face index."""
    image, index = row.get("image"), row.get("face_index")
    if not isinstance(image, str) or not image or type(index) is not int or index < 1:
        raise ValueError("Invalid stable (image, face_index) key")
    return image, index


def index_rows(rows):
    """Refuse duplicates rather than silently overwriting a cohort member."""
    indexed = {}
    for row in rows:
        key = row_key(row)
        if key in indexed:
            raise ValueError(f"Duplicate face key: {key}")
        indexed[key] = row
    return indexed


def validate_report(report):
    """Validate full-cohort provenance; no implicit migration of old lists."""
    if not isinstance(report, dict) or report.get("schema_version") != 2 or report.get("kind") != "swap-quality":
        raise ValueError("Only swap-quality schema_version=2 reports are accepted; regenerate legacy row-list audits")
    for name in ("inputs", "cohort", "rows", "images"):
        if not isinstance(report.get(name), list):
            raise ValueError(f"Audit requires {name} list")
    cohort = index_rows(report["cohort"])
    rows = index_rows(report["rows"])
    inputs = {item["image"]: item for item in report["inputs"]}
    if len(inputs) != len(report["inputs"]) or not inputs:
        raise ValueError("Audit must declare unique original image inputs")
    if any(image not in inputs for image, _index in cohort):
        raise ValueError("Cohort face has no declared input image")
    if rows.keys() - cohort.keys():
        raise ValueError("Audit has rows outside its declared cohort")
    images = {item["image"]: item for item in report["images"]}
    if len(images) != len(report["images"]) or images.keys() - inputs.keys():
        raise ValueError("Duplicate or undeclared per-image diagnostics")
    # Label declarations independently prevent omission of expected faces from
    # both rows and cohort. Unlabeled physical faces still depend on detection.
    for image, item in inputs.items():
        for index, label in item["expected"].items():
            if cohort.get((image, int(index)), {}).get("expected_reference") != label:
                raise ValueError("Declared expected face is missing or relabeled in cohort")
    for key, row in rows.items():
        if row.get("expected_reference") != cohort[key].get("expected_reference"):
            raise ValueError(f"Expected identity changed for {key}")
    provenance = {
        "inputs": sorted(inputs.values(), key=lambda row: row["image"]),
        "cohort": [cohort[key] for key in sorted(cohort)],
        "reference_hashes": report.get("reference_hashes"),
        "minimum_iou": report.get("minimum_iou"),
        "models": report.get("models"),
    }
    if (
        not isinstance(provenance["reference_hashes"], dict)
        or not isinstance(provenance["minimum_iou"], (int, float))
        or not 0 < provenance["minimum_iou"] <= 1
    ):
        raise ValueError("Missing reference provenance or invalid IoU threshold")
    return provenance, cohort, rows, images


def summarize_reports(reports: dict):
    """Compare only identical declared cohorts; count missing/unknown observations.

    Mean expected cosine is conditional on available labeled measurements, with its
    sample count shown. Relative expected top-1 uses all labeled cohort faces as its
    denominator, so omissions cannot improve it. No automatic winner is selected.
    """
    if not reports:
        raise ValueError("No audit reports supplied")
    baseline = None
    variants = []
    for variant, report in sorted(reports.items()):
        provenance, cohort, rows, images = validate_report(report)
        if baseline is None:
            baseline = provenance
        elif baseline != provenance:
            raise ValueError(
                "Reports must share complete input hashes, labels, reference hashes, model metadata, geometry threshold and cohort"
            )
        statuses, labels = Counter(), Counter()
        expected_scores = []
        top1 = 0
        for key, member in cohort.items():
            row = rows.get(key)
            status = row.get("status", "unknown") if row else "audit_row_missing"
            statuses[status if status in KNOWN_STATUSES or status == "audit_row_missing" else "unknown"] += 1
            expected = member.get("expected_reference")
            label_status = (
                "unlabeled"
                if expected is None
                else "reference_missing"
                if expected not in report["reference_hashes"]
                else "labeled"
            )
            labels[label_status] += 1
            if status == "matched" and label_status == "labeled":
                score = row.get("expected_similarity")
                if (
                    type(score) in (int, float)
                    and math.isfinite(score)
                    and -1 <= score <= 1
                    and type(row.get("expected_is_best")) is bool
                ):
                    expected_scores.append(score)
                    top1 += int(row["expected_is_best"])
        labeled_total = len(cohort) - labels["unlabeled"]
        image_statuses = Counter(
            images.get(item["image"], {}).get("status", "audit_image_missing") for item in provenance["inputs"]
        )
        variants.append(
            {
                "variant": variant,
                "scope": "complete_declared_cohort",
                "total_images": len(provenance["inputs"]),
                "cohort_faces": len(cohort),
                "labeled_faces": labeled_total,
                "unlabeled_faces": labels["unlabeled"],
                "reference_missing_faces": labels["reference_missing"],
                "matched_faces": statuses["matched"],
                "missing_or_unknown_faces": len(cohort) - statuses["matched"],
                "expected_evaluable_faces": len(expected_scores),
                "labeled_without_measurement": labeled_total - len(expected_scores),
                "expected_unique_top1_count": top1,
                "expected_unique_top1_fraction_all_labeled": top1 / labeled_total if labeled_total else None,
                "mean_expected_similarity_available_only": sum(expected_scores) / len(expected_scores)
                if expected_scores
                else None,
                "status_counts": dict(sorted(statuses.items())),
                "image_status_counts": dict(sorted(image_statuses.items())),
            }
        )
    return {"schema_version": 2, "kind": "swap-benchmark-summary", "provenance": baseline, "variants": variants}


def main():
    """Write a deterministic summary and flat CSV without an implicit leaderboard."""
    args = parse_args()
    reports = {}
    for item in args.audit:
        variant, separator, path = item.partition("=")
        if not separator or not variant or not path or variant in reports:
            raise ValueError("Each --audit must be a unique VARIANT=REPORT.json")
        reports[variant] = json.loads(Path(path).read_text(encoding="utf-8"))
    summary = summarize_reports(reports)
    write_json(args.output, summary)
    rows = [{key: value for key, value in row.items() if not isinstance(value, dict)} for row in summary["variants"]]
    with args.output.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
