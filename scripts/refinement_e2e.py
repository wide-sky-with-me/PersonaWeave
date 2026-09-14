"""Non-production three-evidence diagnosis, one-image edit and advisory review CLI.

Run: uv run python -m scripts.refinement_e2e CURRENT.png --people people.json
     --original ORIGINAL.png --output-dir outputs/diagnostics/NEW-RUN --allow-paid
The people JSON is a list of {"person_id": 7, "person_locator": "left person",
"reference_paths": ["portrait.png"]}; relative references resolve beside that JSON.
Original and reference images are optional evidence, never image-edit attachments.
No calls occur without --allow-paid; automatic mode skips no-defect/uncertain mapping.
The isolated bundle is private diagnostic evidence, not a session or published result.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

import cv2
from dotenv import load_dotenv

from core.refinement.conversation import ConversationRefinementService
from core.refinement.evidence import prepare_people
from core.refinement.image_ops import decode_image


def add_evidence_arguments(parser):
    parser.add_argument("image", type=Path, help="Explicit current complete canvas")
    parser.add_argument("--original", type=Path, help="Optional original scene evidence")
    parser.add_argument("--people", type=Path, required=True, help="Stable-person/reference JSON manifest")
    parser.add_argument("--instructions", default="检查人物自然度、参考身份一致性与遮挡关系，按可见证据修复明确问题")
    parser.add_argument("--output-dir", type=Path, required=True, help="New private diagnostic directory")
    parser.add_argument("--allow-paid", action="store_true", help="Explicitly authorize this diagnostic model workload")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_evidence_arguments(parser)
    parser.add_argument("--model-alias", help="Configured alias; defaults to gateway.default_image_alias")
    parser.add_argument("--mode", choices=["discuss", "auto"], default="auto")
    args = parser.parse_args(argv)
    if not args.allow_paid:
        parser.error("--allow-paid is required: analysis and generation may both incur charges")
    return args


def load_inputs(args):
    """Load only explicitly selected local evidence; never guess target IDs from image order."""
    current = decode_image(args.image.read_bytes())
    original = decode_image(args.original.read_bytes()) if args.original else None
    manifest = json.loads(args.people.read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        raise ValueError("People manifest must be a list of stable-person records")
    people, references = [], {}
    for item in manifest:
        if not isinstance(item, dict) or set(item) - {"person_id", "person_locator", "user_focus", "reference_paths"}:
            raise ValueError("Unsupported person manifest fields")
        people.append({key: value for key, value in item.items() if key != "reference_paths"})
        paths = item.get("reference_paths", [])
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            raise ValueError("reference_paths must contain local path strings")
        references[item["person_id"]] = [decode_image((args.people.parent / path).read_bytes()) for path in paths]
    prepare_people(people, references)
    return {
        "original": original,
        "current": current,
        "people": people,
        "references": references,
        "history": [],
        "user_message": args.instructions,
    }


def create_bundle_dir(path):
    """Diagnostics must not be accidentally written into a production/public artifact root."""
    root = Path(__file__).resolve().parents[1]
    output = path.resolve()
    for protected in ("outputs/api_results", "outputs/refinement_staging", "outputs/refinement_evidence", "data"):
        if output.is_relative_to((root / protected).resolve()):
            raise ValueError("Diagnostic output must be outside production artifact directories")
    output.mkdir(parents=True, exist_ok=False)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def generate_and_review(service, inputs, diagnosis, alias, candidate_path):
    """Save normalized bytes before advisory review; never regenerate after a review outage."""
    candidate, audit = await service.generate(
        inputs["current"], inputs["people"], diagnosis, inputs["user_message"], alias
    )
    ok, encoded = cv2.imencode(".png", candidate)
    if not ok:
        raise ValueError("Could not encode diagnostic candidate")
    content = encoded.tobytes()
    candidate_path.write_bytes(content)
    result = {
        "candidate_path": str(candidate_path),
        "candidate_digest": hashlib.sha256(content).hexdigest(),
        "candidate_shape": list(candidate.shape),
        "generation_audit": audit,
        "final_review": None,
        "review_error": None,
    }
    # A valid candidate remains discoverable even if review is interrupted by process termination.
    write_json(candidate_path.with_suffix(".json"), result)
    try:
        report = await service.review(
            inputs["original"],
            inputs["current"],
            candidate,
            inputs["people"],
            inputs["references"],
            diagnosis,
            inputs["user_message"],
        )
        result["final_review"] = report.model_dump(mode="json")
    except Exception as exc:  # Review is advisory; do not leak upstream messages or discard successful generation.
        result["review_error"] = f"Advisory review unavailable ({type(exc).__name__})"
    write_json(candidate_path.with_suffix(".json"), result)
    return candidate, result


async def run(args):
    if not args.allow_paid:
        raise ValueError("Explicit --allow-paid authorization is required")
    inputs = load_inputs(args)
    create_bundle_dir(args.output_dir)
    service = ConversationRefinementService()
    diagnosis = await service.analyze(**inputs)
    evidence = {
        "source": str(args.image),
        "source_shape": list(inputs["current"].shape),
        "diagnosis": diagnosis.model_dump(mode="json"),
        "status": "discussed",
    }
    write_json(args.output_dir / "evidence.json", evidence)
    if args.mode != "discuss":
        if diagnosis.mapping_uncertain:
            evidence["status"] = "clarification_required"
        elif not diagnosis.needs_edit:
            evidence["status"] = "no_edit"
        else:
            _, generated = await generate_and_review(
                service, inputs, diagnosis, args.model_alias, args.output_dir / "candidate.png"
            )
            evidence.update(generated, status="review_required")
    write_json(args.output_dir / "evidence.json", evidence)
    print(json.dumps({"status": evidence["status"], "bundle": str(args.output_dir)}, ensure_ascii=False))
    return evidence


def main():
    args = parse_args()
    load_dotenv()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
