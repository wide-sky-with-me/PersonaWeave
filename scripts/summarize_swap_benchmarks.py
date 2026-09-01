"""Generate one aggregate leaderboard from selected face records."""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True, help="JSON list of {audit_dir, face_index}")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    selections = json.loads(args.selection.read_text())
    scores = defaultdict(list)
    for selection in selections:
        directory = Path(selection["audit_dir"])
        face_index = int(selection["face_index"])
        for path in directory.glob("audit-*.json"):
            rows = json.loads(path.read_text())
            if len(rows) < face_index:
                continue
            row = rows[face_index - 1]
            scores[path.stem.removeprefix("audit-")].append(row)

    leaderboard = []
    for variant, rows in scores.items():
        count = len(rows)
        leaderboard.append(
            {
                "variant": variant,
                "samples": count,
                "mean_best_identity_similarity": round(sum(row["best_similarity"] for row in rows) / count, 4),
                "mean_original_similarity": round(sum(row["original_result_similarity"] for row in rows) / count, 4),
                "identity_margin": round(
                    sum(row["best_similarity"] - row["original_result_similarity"] for row in rows) / count, 4
                ),
            }
        )
    leaderboard.sort(key=lambda row: row["identity_margin"], reverse=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(leaderboard, indent=2) + "\n")
    with args.output.with_suffix(".csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=leaderboard[0].keys() if leaderboard else ["variant"])
        writer.writeheader()
        writer.writerows(leaderboard)
    for row in leaderboard:
        print(row)


if __name__ == "__main__":
    main()
