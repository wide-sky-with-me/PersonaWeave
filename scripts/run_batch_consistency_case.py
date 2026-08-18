#!/usr/bin/env python3
"""Run the reproducible NASA multi-image identity consistency case.

The script intentionally drives the public PersonaWeave HTTP API for creation,
uploads, analysis, and task execution. It reads raw job outputs only after the
API run so a needs-review swap can still be inspected without pretending that
the formal workflow reached validation.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import shutil
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageChops, ImageOps, ImageStat

from personaweave.assets import AssetResolver
from personaweave.config import Settings
from personaweave.domain import BoundingBox, ValidationPeer, ValidationRequest, ValidationSubject
from personaweave.providers.yunwu import YunwuMultimodalProvider
from personaweave.storage import Repository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASE_DIR = PROJECT_ROOT / "data/test_sets/nasa_christina_koch_20260816"


def checked(response: httpx.Response) -> httpx.Response:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:1000]
        raise RuntimeError(f"HTTP {response.status_code}: {detail}") from error
    return response


def load_state(case_dir: Path) -> dict[str, Any]:
    path = case_dir / "runtime-state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def get_detail(client: httpx.Client, base_url: str, batch_id: str) -> dict[str, Any]:
    return checked(client.get(f"{base_url}/api/v1/batches/{batch_id}")).json()


def create_and_analyze(client: httpx.Client, base_url: str, case_dir: Path) -> dict[str, Any]:
    state = load_state(case_dir)
    if state.get("batch_id"):
        detail = get_detail(client, base_url, state["batch_id"])
        save_json(case_dir / "analysis-detail.json", detail)
        return detail

    batch = checked(
        client.post(
            f"{base_url}/api/v1/batches",
            json={"name": "NASA 公开组图一致性测试 2026-08-16"},
        )
    ).json()
    state = {"batch_id": batch["id"]}
    save_json(case_dir / "runtime-state.json", state)

    sources = sorted((case_dir / "sources").glob("*.jpg"))
    if len(sources) != 3:
        raise RuntimeError(f"expected exactly three source images, found {len(sources)}")
    for source in sources:
        with source.open("rb") as handle:
            checked(
                client.post(
                    f"{base_url}/api/v1/batches/{batch['id']}/images",
                    files={"image": (source.name, handle, "image/jpeg")},
                )
            )

    analysis = checked(
        client.post(f"{base_url}/api/v1/batches/{batch['id']}/analysis/run")
    ).json()
    detail = get_detail(client, base_url, batch["id"])
    save_json(case_dir / "analysis-response.json", analysis)
    save_json(case_dir / "analysis-detail.json", detail)
    return detail


def spanning_people(detail: dict[str, Any]) -> list[dict[str, Any]]:
    source_ids = {asset["id"] for asset in detail["assets"] if asset["kind"] == "source"}
    candidates = []
    for person in detail["persons"]:
        seen = {face["asset_id"] for face in person["faces"]}
        candidates.append(
            {
                "display_code": person["display_code"],
                "person_id": person["id"],
                "source_count": len(seen & source_ids),
                "face_count": len(person["faces"]),
                "asset_ids": sorted(seen),
            }
        )
    return sorted(candidates, key=lambda item: (-item["source_count"], item["display_code"]))


def ensure_target_and_spec(
    client: httpx.Client,
    base_url: str,
    case_dir: Path,
    detail: dict[str, Any],
    person_code: str | None,
    max_iterations: int,
) -> tuple[dict[str, Any], str, str]:
    candidates = spanning_people(detail)
    save_json(case_dir / "person-candidates.json", candidates)
    if person_code is None:
        exact = [item for item in candidates if item["source_count"] == 3]
        if len(exact) != 1:
            raise RuntimeError(
                "automatic selection requires exactly one Pxx spanning all three sources; "
                f"candidates={candidates}"
            )
        person_code = exact[0]["display_code"]
    if person_code not in {item["display_code"] for item in candidates}:
        raise RuntimeError(f"unknown person code: {person_code}")

    identities = detail.get("identities", [])
    if identities:
        identity = identities[0]
    else:
        identity = checked(
            client.post(
                f"{base_url}/api/v1/batches/{detail['id']}/identities",
                json={"label": "虚构成年女性身份锚点"},
            )
        ).json()
        reference = case_dir / "reference/T01-fictional-adult.png"
        with reference.open("rb") as handle:
            checked(
                client.post(
                    f"{base_url}/api/v1/batches/{detail['id']}/identities/{identity['id']}/references",
                    files={"image": (reference.name, handle, "image/png")},
                )
            )

    target_code = identity["display_code"]
    instruction = (
        f"将 @{person_code} 在全部原图中的面部身份统一替换为 @{target_code}。"
        "只修改该人物面部内部的身份特征，保留原人物的发型、发际线、眼镜、安全帽、服装、"
        "身体、姿态、表情、视线和遮挡关系；严格保留其他所有人物、背景、物体、文字、Logo、"
        "构图、尺寸、光照、色彩、清晰度、噪点和景深。目标身份必须跨图一致，同时自然适应"
        "每张原图的头部角度、表情、尺度、光线与肤色。若无法可靠定位，只标记人工复核，"
        "不得修改其他人物。"
    )
    checked(
        client.put(
            f"{base_url}/api/v1/batches/{detail['id']}/spec",
            json={"task_instruction": instruction, "max_iterations": max_iterations},
        )
    )
    return get_detail(client, base_url, detail["id"]), person_code, target_code


def image_data_uri(path: Path, max_edge: int = 1600) -> str:
    with Image.open(path) as image:
        preview = ImageOps.exif_transpose(image).convert("RGB")
        preview.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        output = BytesIO()
        preview.save(output, format="JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def pixel_metrics(source: Path, result: Path) -> dict[str, Any]:
    with Image.open(source) as source_image, Image.open(result) as result_image:
        source_rgb = ImageOps.exif_transpose(source_image).convert("RGB")
        result_rgb = ImageOps.exif_transpose(result_image).convert("RGB")
    if source_rgb.size != result_rgb.size:
        return {
            "same_dimensions": False,
            "source_size": list(source_rgb.size),
            "result_size": list(result_rgb.size),
        }
    difference = ImageChops.difference(source_rgb, result_rgb)
    statistics = ImageStat.Stat(difference)
    rmse = math.sqrt(sum(value * value for value in statistics.rms) / 3) / 255
    mean_absolute_error = sum(statistics.mean) / (3 * 255)
    pixels = (
        difference.get_flattened_data()
        if hasattr(difference, "get_flattened_data")
        else difference.getdata()
    )
    changed_pixels = sum(1 for red, green, blue in pixels if max(red, green, blue) > 10)
    return {
        "same_dimensions": True,
        "rmse": round(rmse, 6),
        "mean_absolute_error": round(mean_absolute_error, 6),
        "changed_pixel_ratio_gt_10": round(changed_pixels / (source_rgb.width * source_rgb.height), 6),
    }


async def final_multimodal_qa(
    *,
    settings: Settings,
    batch_id: str,
    instruction: str,
    target_code: str,
    cases: list[dict[str, Any]],
    reference_path: Path,
) -> list[dict[str, Any]]:
    provider = YunwuMultimodalProvider(
        settings.yunwu_base_url,
        settings.yunwu_api_key,
        settings.yunwu_vision_model,
        settings.multimodal_timeout_seconds,
    )
    reference_uri = image_data_uri(reference_path)
    reports = []
    for item in cases:
        request_data = item["swap_input"]
        subjects = [
            ValidationSubject(
                face_instance_id=operation["face_instance_id"],
                source_person_code=operation["source_person_code"],
                target_identity_code=operation.get("target_identity_code"),
                bbox=BoundingBox.model_validate(operation["bbox"]),
                reference_uris=[reference_uri] if operation.get("target_identity_code") else [],
            )
            for operation in request_data["replacements"]
        ]
        peers = [
            ValidationPeer(
                result_asset_id=peer["result_id"],
                result_uri=image_data_uri(peer["result_path"]),
                source_image_code=peer["source_code"],
                target_identity_codes=[target_code],
            )
            for peer in cases
            if peer["source_asset_id"] != item["source_asset_id"]
        ]
        validation = await provider.validate(
            ValidationRequest(
                batch_id=batch_id,
                result_asset_id=item["result_id"],
                source_uri=image_data_uri(item["source_path"]),
                result_uri=image_data_uri(item["result_path"]),
                expected_face_instance_ids=[subject.face_instance_id for subject in subjects],
                protected_face_instance_ids=request_data["protected_face_instance_ids"],
                source_image_code=item["source_code"],
                task_instruction=instruction,
                compiled_instruction=request_data["compiled_instruction"],
                expected_subjects=subjects,
                peer_results=peers,
            )
        )
        reports.append({"source_code": item["source_code"], **validation.model_dump(mode="json")})
    return reports


def collect_and_validate(case_dir: Path, batch_id: str, target_code: str) -> dict[str, Any]:
    settings = Settings()
    repository = Repository(settings.database_path)
    repository.initialize()
    resolver = AssetResolver(settings.asset_root)
    batch = repository.get_batch(batch_id)
    assets = {asset["id"]: asset for asset in repository.list_assets(batch_id)}
    latest_swap_jobs = {
        job["item_key"]: job
        for job in repository.latest_stage_jobs(batch_id, "swap")
    }
    cases = []
    results_dir = case_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    for source_id, job in latest_swap_jobs.items():
        if not job.get("output"):
            continue
        source = assets[source_id]
        source_path = resolver.local_path(source["storage_uri"])
        result_path = resolver.local_path(job["output"]["output_uri"])
        if source_path is None or result_path is None:
            raise RuntimeError("test requires materialized local source and result files")
        copied_result = results_dir / f"{source['display_code']}-result{result_path.suffix}"
        shutil.copy2(result_path, copied_result)
        cases.append(
            {
                "source_asset_id": source_id,
                "source_code": source["display_code"],
                "source_path": source_path,
                "result_id": f"runtime-{job['id']}",
                "result_path": copied_result,
                "job_status": job["status"],
                "completed_iterations": job["output"].get("completed_iterations"),
                "provider": job["output"].get("provider"),
                "model": job["output"].get("model"),
                "generation_plan": job["output"].get("generation_plan", {}),
                "generation_review": job["output"].get("generation_review", {}),
                "swap_input": job["input"],
                "pixel_metrics": pixel_metrics(source_path, copied_result),
            }
        )
    if not cases:
        raise RuntimeError("no materialized swap outputs were available")
    qa = asyncio.run(
        final_multimodal_qa(
            settings=settings,
            batch_id=batch_id,
            instruction=batch["task_instruction"],
            target_code=target_code,
            cases=cases,
            reference_path=case_dir / "reference/T01-fictional-adult.png",
        )
    )
    report_cases = []
    for item in cases:
        report_cases.append(
            {
                key: value
                for key, value in item.items()
                if key not in {"source_path", "result_path", "swap_input"}
            }
            | {
                "source_path": str(item["source_path"].relative_to(PROJECT_ROOT)),
                "result_path": str(item["result_path"].relative_to(PROJECT_ROOT)),
            }
        )
    return {
        "batch_id": batch_id,
        "batch_status": batch["status"],
        "task_instruction": batch["task_instruction"],
        "cases": report_cases,
        "final_multimodal_qa": qa,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--case-dir", type=Path, default=DEFAULT_CASE_DIR)
    parser.add_argument("--phase", choices=("prepare", "generate", "all"), default="all")
    parser.add_argument("--person-code")
    parser.add_argument("--max-iterations", type=int, choices=range(1, 11), default=2)
    args = parser.parse_args()
    case_dir = args.case_dir.resolve()
    timeout = httpx.Timeout(connect=30.0, write=180.0, read=1800.0, pool=30.0)
    with httpx.Client(timeout=timeout) as client:
        detail = create_and_analyze(client, args.base_url.rstrip("/"), case_dir)
        candidates = spanning_people(detail)
        print(json.dumps({"batch_id": detail["id"], "status": detail["status"], "people": candidates}, ensure_ascii=False, indent=2))
        if args.phase == "prepare":
            return
        detail, person_code, target_code = ensure_target_and_spec(
            client,
            args.base_url.rstrip("/"),
            case_dir,
            detail,
            args.person_code,
            args.max_iterations,
        )
        task_result = checked(
            client.post(f"{args.base_url.rstrip('/')}/api/v1/batches/{detail['id']}/task/run")
        ).json()
        save_json(case_dir / "task-run-response.json", task_result)

    report = collect_and_validate(case_dir, detail["id"], target_code)
    report["selected_person_code"] = person_code
    report["target_identity_code"] = target_code
    report["task_run_response"] = task_result
    save_json(case_dir / "runtime-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
