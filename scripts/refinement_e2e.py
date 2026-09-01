"""Run the real whole-image refinement chain against one local image.

The script exercises the same service contracts as the API: one whole-image
VLM diagnosis, one-image generation, and one advisory whole-image review. It
writes only an isolated evidence bundle and never publishes an accepted result.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.refinement_service import RefinementService


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--person-id", type=int, default=1)
    parser.add_argument("--face-index", type=int, default=1)
    parser.add_argument("--focus", default="全面检查换脸造成的面部结构、边缘、皮肤纹理与光影瑕疵")
    parser.add_argument("--instructions", default="修复所有可见换脸瑕疵，保持人物身份观感与整张照片自然真实")
    parser.add_argument("--model-alias", help="Configured image alias; defaults to gateway.default_image_alias")
    return parser.parse_args()


async def run(args):
    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not decode image: {args.image}")

    service = RefinementService()
    diagnostic_people = [SimpleNamespace(person_id=args.person_id, face_index=args.face_index, user_focus=args.focus)]
    diagnosis = await service.review(image, diagnostic_people)
    report = diagnosis[args.person_id]
    request_people = [
        SimpleNamespace(
            person_id=args.person_id,
            face_index=args.face_index,
            person_locator=report["person_locator"],
            review_prompt=report["recommended_prompt"],
            user_instructions=args.instructions,
            issues=[SimpleNamespace(**issue) for issue in report["issues"]],
        )
    ]
    generation = await service.refine_whole_image(
        image,
        request_people,
        "保持背景、构图、服装、文字和未要求修改的区域自然一致",
        args.model_alias,
    )
    candidate = generation["candidate"]
    review_error = None
    try:
        final_review = await service.review_whole_candidate(
            image,
            candidate,
            request_people,
            "保持背景、构图、服装、文字和未要求修改的区域自然一致",
        )
    except Exception as exc:  # Real-provider diagnostics must retain successful generation evidence.
        review_error = str(exc)
        final_review = None

    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = args.output_dir / "candidate.png"
    if not cv2.imwrite(str(candidate_path), candidate):
        raise ValueError(f"Could not write candidate: {candidate_path}")
    evidence = {
        "source": str(args.image),
        "source_shape": list(image.shape),
        "candidate_shape": list(candidate.shape),
        "diagnosis": diagnosis,
        "generation_audit": generation["audit"],
        "final_review": final_review,
        "review_error": review_error,
        "candidate_path": str(candidate_path),
    }
    (args.output_dir / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


def main():
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
