"""Non-production comparison of explicitly selected configured image aliases.

Run: uv run python -m scripts.evaluate_refinement_models CURRENT.png --people people.json
     --original ORIGINAL.png --output-dir outputs/diagnostics/NEW-RUN
     --models CONFIGURED_ALIAS --allow-paid
Uses the same manifest as refinement_e2e: stable person_id/person_locator/reference_paths.
One shared three-evidence diagnosis feeds one edit and advisory review per chosen alias.
Every model receives the identical baseline; drift metrics are not identity or quality scores.
Bundles never publish accepted outputs and require a new private directory.
"""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime

import cv2
import numpy as np
from dotenv import load_dotenv

from core.ai.gateway import AIGateway
from core.refinement.conversation import ConversationRefinementService
from scripts.refinement_e2e import (
    add_evidence_arguments,
    create_bundle_dir,
    generate_and_review,
    load_inputs,
    write_json,
)


def color_metrics(baseline: np.ndarray, candidate: np.ndarray) -> dict:
    """Return compact whole-canvas drift metrics after provider normalization."""
    baseline_lab = cv2.cvtColor(baseline, cv2.COLOR_BGR2LAB).astype(np.float32)
    candidate_lab = cv2.cvtColor(candidate, cv2.COLOR_BGR2LAB).astype(np.float32)
    baseline_hsv = cv2.cvtColor(baseline, cv2.COLOR_BGR2HSV).astype(np.float32)
    candidate_hsv = cv2.cvtColor(candidate, cv2.COLOR_BGR2HSV).astype(np.float32)
    difference = np.abs(baseline.astype(np.int16) - candidate.astype(np.int16))
    return {
        "mean_absolute_channel_difference": round(float(difference.mean()), 3),
        "pixels_over_10_percent": round(float((difference.max(axis=2) > 10).mean() * 100), 3),
        "lab_mean_delta": np.round(
            candidate_lab.reshape(-1, 3).mean(axis=0) - baseline_lab.reshape(-1, 3).mean(axis=0), 3
        ).tolist(),
        "saturation_mean_delta": round(float(candidate_hsv[..., 1].mean() - baseline_hsv[..., 1].mean()), 3),
    }


async def run(args, aliases: tuple[str, ...] | None = None):
    if not args.allow_paid:
        raise ValueError("Explicit --allow-paid authorization is required")
    inputs = load_inputs(args)
    gateway = AIGateway()
    selected_aliases = aliases or tuple(args.models)
    if not selected_aliases or len(set(selected_aliases)) != len(selected_aliases):
        raise ValueError("Select distinct configured image aliases explicitly")
    if any(alias not in gateway.image_edits for alias in selected_aliases):
        raise ValueError("Unknown image alias; consult the configured gateway catalog")
    create_bundle_dir(args.output_dir)
    service = ConversationRefinementService(gateway)
    diagnosis = await service.analyze(**inputs)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "input": str(args.image),
        "diagnosis": diagnosis.model_dump(mode="json"),
        "results": [],
    }
    write_json(args.output_dir / "manifest.json", manifest)
    # Each alias receives the same original baseline and diagnosis, never the preceding model output.
    for index, alias in enumerate(selected_aliases, 1):
        started = time.perf_counter()
        if diagnosis.mapping_uncertain or not diagnosis.needs_edit:
            result = {
                "alias": alias,
                "status": "skipped",
                "reason": "clarification_required" if diagnosis.mapping_uncertain else "no_edit",
            }
        else:
            try:
                candidate, generated = await generate_and_review(
                    service, inputs, diagnosis, alias, args.output_dir / f"model-{index:02d}.png"
                )
                result = {
                    "alias": alias,
                    "status": "ok",
                    **generated,
                    "metrics": color_metrics(inputs["current"], candidate),
                }
            except Exception as exc:  # Compare remaining explicitly requested aliases; no implicit retry.
                result = {
                    "alias": alias,
                    "status": "error",
                    "error": f"Diagnostic generation failed ({type(exc).__name__})",
                }
        result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        manifest["results"].append(result)
        write_json(args.output_dir / "manifest.json", manifest)
        print(json.dumps({"alias": alias, "status": result["status"]}, ensure_ascii=False), flush=True)
    return manifest


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_evidence_arguments(parser)
    parser.add_argument(
        "--models",
        nargs="+",
        required=True,
        help="Explicit configured aliases to compare; never auto-select the whole catalog",
    )
    args = parser.parse_args(argv)
    if not args.allow_paid:
        parser.error("--allow-paid is required: analysis plus one edit/review per selected alias may incur charges")
    return args


def main():
    args = parse_args()
    load_dotenv()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
