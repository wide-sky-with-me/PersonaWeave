"""Run the configured high-quality refinement models against one controlled edit."""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from dotenv import load_dotenv

from core.ai_gateway import AIGateway
from core.refinement_service import RefinementService


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


async def run(input_path: Path, output_dir: Path, aliases: tuple[str, ...] | None = None) -> None:
    baseline = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
    if baseline is None:
        raise ValueError(f"Could not read evaluation input: {input_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    gateway = AIGateway()
    service = RefinementService(gateway)
    selected_aliases = aliases or tuple(gateway.image_edits)
    person = SimpleNamespace(
        person_id=1,
        face_index=1,
        person_locator="上排·从左到右第 1 位",
        review_prompt="仅优化发际线与皮肤交界处的融合，保持自然发丝边缘",
        user_instructions=(
            "仅优化发际线与皮肤交界处的融合。严格保持原图全局白平衡、曝光、饱和度、对比度、人物、"
            "背景、电视、白板文字、Logo、水印和构图不变。"
        ),
        issues=[],
    )
    results = []
    for alias in selected_aliases:
        started = time.perf_counter()
        try:
            generated = await service.refine_whole_image(
                baseline, [person], "严格保持原图整体色彩与所有文字不变", alias
            )
            candidate = generated["candidate"]
            output_path = output_dir / f"{alias}.png"
            if not cv2.imwrite(str(output_path), candidate):
                raise ValueError(f"Could not save candidate: {output_path}")
            results.append(
                {
                    "alias": alias,
                    "status": "ok",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "output": str(output_path),
                    "audit": generated["audit"],
                    "metrics": color_metrics(baseline, candidate),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "alias": alias,
                    "status": "error",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "error": str(exc),
                }
            )
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "input": str(input_path),
        "prompt_scope": "one controlled hairline edit with strict global preservation",
        "results": results,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--models", nargs="+", help="Configured image aliases; defaults to the complete gateway catalog"
    )
    args = parser.parse_args()
    load_dotenv()
    output_dir = args.output_dir or Path("outputs/model_evaluation") / datetime.now().strftime("%Y%m%d_%H%M%S")
    asyncio.run(run(args.input, output_dir, tuple(args.models) if args.models else None))


if __name__ == "__main__":
    main()
