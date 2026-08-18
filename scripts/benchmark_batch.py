from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx


REQUIRED_SCENARIO_TAGS = frozenset(
    {
        "frontal",
        "profile",
        "occlusion",
        "multi_person",
        "low_resolution",
        "expression",
        "lighting",
    }
)
_SOURCE_CODE = re.compile(r"I\d{2}\Z")
_SECRET = re.compile(
    r"(?i)(bearer\s+|(?:api[_ -]?key|authorization|password|token)\s*[:=]\s*)\S+|sk-[A-Za-z0-9_-]+"
)
_MAX_REASON_LENGTH = 240
_MAX_REASONS = 20
_STAGES = ("analyze", "swap", "validate")
_FINAL_STATUSES = ("succeeded", "needs_review", "failed", "not_run")


class BenchmarkError(ValueError):
    """A manifest or API response cannot be benchmarked deterministically."""


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise BenchmarkError(f"{label} must be a JSON object")
    return value


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"{label} must be a non-empty string")
    return value.strip()


def validate_manifest(value: object) -> dict[str, Any]:
    manifest = _object(value, "manifest")
    unknown = set(manifest) - {"batch_id", "cases"}
    if unknown:
        raise BenchmarkError(f"manifest has unsupported fields: {sorted(unknown)}")
    batch_id = _nonempty_string(manifest.get("batch_id"), "manifest.batch_id")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not 10 <= len(cases) <= 20:
        raise BenchmarkError("manifest.cases must contain 10 to 20 cases")

    normalized: list[dict[str, Any]] = []
    codes: set[str] = set()
    coverage: set[str] = set()
    for index, raw_case in enumerate(cases):
        case = _object(raw_case, f"manifest.cases[{index}]")
        unknown = set(case) - {"source_asset_code", "scenario_tags"}
        if unknown:
            raise BenchmarkError(
                f"manifest.cases[{index}] has unsupported fields: {sorted(unknown)}"
            )
        code = _nonempty_string(
            case.get("source_asset_code"),
            f"manifest.cases[{index}].source_asset_code",
        )
        if not _SOURCE_CODE.fullmatch(code):
            raise BenchmarkError(
                f"manifest.cases[{index}].source_asset_code must match Ixx"
            )
        if code in codes:
            raise BenchmarkError(f"duplicate source_asset_code: {code}")
        tags = case.get("scenario_tags")
        if not isinstance(tags, list) or not tags:
            raise BenchmarkError(
                f"manifest.cases[{index}].scenario_tags must be a non-empty list"
            )
        if any(not isinstance(tag, str) for tag in tags):
            raise BenchmarkError(
                f"manifest.cases[{index}].scenario_tags must contain only strings"
            )
        tag_set = set(tags)
        if len(tag_set) != len(tags):
            raise BenchmarkError(
                f"manifest.cases[{index}].scenario_tags must not contain duplicates"
            )
        invalid = tag_set - REQUIRED_SCENARIO_TAGS
        if invalid:
            raise BenchmarkError(
                f"manifest.cases[{index}] has unsupported scenario tags: {sorted(invalid)}"
            )
        codes.add(code)
        coverage.update(tag_set)
        normalized.append(
            {"source_asset_code": code, "scenario_tags": sorted(tag_set)}
        )

    missing = REQUIRED_SCENARIO_TAGS - coverage
    if missing:
        raise BenchmarkError(f"manifest does not cover scenario tags: {sorted(missing)}")
    return {"batch_id": batch_id, "cases": sorted(normalized, key=lambda case: case["source_asset_code"])}


def load_manifest(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BenchmarkError(f"cannot load manifest: {error}") from error
    return validate_manifest(value)


def _safe_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    redacted = _SECRET.sub(lambda match: f"{match.group(1) or ''}[REDACTED]", value.strip())
    if len(redacted) > _MAX_REASON_LENGTH:
        redacted = redacted[: _MAX_REASON_LENGTH - 1] + "…"
    return redacted


def _safe_strings(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    safe = {_safe_text(value) for value in values}
    return sorted(value for value in safe if value is not None)[:_MAX_REASONS]


def _timestamp(value: object, label: str) -> datetime:
    text = _nonempty_string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise BenchmarkError(f"{label} is not an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BenchmarkError(f"{label} must include a UTC offset")
    return parsed


def job_duration(job: Mapping[str, Any]) -> float:
    created = _timestamp(job.get("created_at"), "job.created_at")
    updated = _timestamp(job.get("updated_at"), "job.updated_at")
    duration = (updated - created).total_seconds()
    if duration < 0:
        raise BenchmarkError("job.updated_at precedes job.created_at")
    return duration


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """Return a deterministic nearest-rank percentile."""
    if not values:
        return None
    if not 0 < quantile <= 1:
        raise ValueError("quantile must be in (0, 1]")
    ordered = sorted(values)
    return ordered[math.ceil(quantile * len(ordered)) - 1]


def _stage_metrics(jobs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    durations = [job_duration(job) for job in jobs]
    return {
        "count": len(durations),
        "p50_seconds": percentile(durations, 0.50),
        "p95_seconds": percentile(durations, 0.95),
        "max_seconds": max(durations) if durations else None,
    }
def _safe_check_value(value: object) -> bool | int | float | str | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _safe_text(value)
    return None




def _quality_evidence(job: Mapping[str, Any] | None) -> dict[str, Any]:
    output = job.get("output") if job else None
    verdicts = output.get("reviewer_verdicts") if isinstance(output, dict) else None
    public_verdicts: list[dict[str, Any]] = []
    if isinstance(verdicts, list):
        for raw in verdicts[:2]:
            if not isinstance(raw, dict):
                continue
            checks = raw.get("checks")
            safe_checks: dict[str, bool | int | float | str] = {}
            if isinstance(checks, dict):
                string_checks = sorted(
                    (key, value) for key, value in checks.items() if isinstance(key, str)
                )
                for key, value in string_checks[:40]:
                    safe_value = _safe_check_value(value)
                    if safe_value is not None:
                        safe_key = _safe_text(key)
                        if safe_key:
                            safe_checks[safe_key[:80]] = safe_value
            public_verdicts.append(
                {
                    "reviewer_id": _safe_text(raw.get("reviewer_id")),
                    "provider": _safe_text(raw.get("provider")),
                    "passed": raw.get("passed") if isinstance(raw.get("passed"), bool) else None,
                    "checks": safe_checks,
                    "reasons": _safe_strings(raw.get("reasons")),
                }
            )
    ids = {verdict["reviewer_id"] for verdict in public_verdicts}
    providers = {verdict["provider"] for verdict in public_verdicts if verdict["provider"]}
    complete = (
        len(public_verdicts) == 2
        and ids == {"reviewer_a", "reviewer_b"}
        and len(providers) == 2
        and all(verdict["passed"] is not None and verdict["checks"] for verdict in public_verdicts)
    )
    return {
        "available": complete,
        "basis": "persisted_reviewer_verdicts_and_checks" if complete else "missing",
        "missing_reason": None if complete else "distinct reviewer A/B verdicts with checks are not persisted",
        "all_reviewers_passed": complete and all(
            verdict["passed"] is True for verdict in public_verdicts
        ),
        "reviewer_verdicts": public_verdicts,
    }


def _job_index(raw_jobs: object) -> dict[tuple[str, str], Mapping[str, Any]]:
    if not isinstance(raw_jobs, list):
        raise BenchmarkError("batch.latest_jobs must be a list")
    indexed: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, raw in enumerate(raw_jobs):
        job = _object(raw, f"batch.latest_jobs[{index}]")
        stage = _nonempty_string(job.get("stage"), f"batch.latest_jobs[{index}].stage")
        item_key = _nonempty_string(job.get("item_key"), f"batch.latest_jobs[{index}].item_key")
        if stage not in _STAGES:
            continue
        key = (stage, item_key)
        if key in indexed:
            raise BenchmarkError(f"duplicate latest job mapping for {stage}/{item_key}")
        indexed[key] = job
    return indexed


def _reason_record(code: str, stage: str, job: Mapping[str, Any]) -> dict[str, Any]:
    raw_output = job.get("output")
    output = raw_output if isinstance(raw_output, dict) else {}
    reasons = _safe_strings(output.get("review_reasons"))
    validation_reasons = _safe_strings(output.get("reasons"))
    reviewer_reasons: list[str] = []
    verdicts = output.get("reviewer_verdicts")
    if isinstance(verdicts, list):
        reviewer_reasons = sorted(
            {reason for verdict in verdicts if isinstance(verdict, dict) for reason in _safe_strings(verdict.get("reasons"))}
        )[:_MAX_REASONS]
    error_code = _safe_text(job.get("error_code"))
    error_message = _safe_text(job.get("error_message"))
    return {
        "source_asset_code": code,
        "stage": stage,
        "status": job.get("status"),
        "review_reasons": reasons,
        "validation_reasons": validation_reasons,
        "reviewer_reasons": reviewer_reasons,
        "failure_code": error_code,
        "error_message": error_message,
    }


def summarize_batch(manifest: Mapping[str, Any], raw_batch: object) -> dict[str, Any]:
    manifest = validate_manifest(manifest)
    batch = _object(raw_batch, "batch")
    response_batch_id = _nonempty_string(batch.get("id"), "batch.id")
    if response_batch_id != manifest["batch_id"]:
        raise BenchmarkError("API batch id does not match manifest.batch_id")
    assets = batch.get("assets")
    if not isinstance(assets, list):
        raise BenchmarkError("batch.assets must be a list")

    source_by_code: dict[str, Mapping[str, Any]] = {}
    results_by_parent: dict[str, dict[str, Mapping[str, Any]]] = {}
    asset_ids: set[str] = set()
    for index, raw in enumerate(assets):
        asset = _object(raw, f"batch.assets[{index}]")
        kind = asset.get("kind")
        asset_id = _nonempty_string(asset.get("id"), f"batch.assets[{index}].id")
        if asset_id in asset_ids:
            raise BenchmarkError(f"duplicate asset id in API response: {asset_id}")
        asset_ids.add(asset_id)
        if kind == "source":
            code = _nonempty_string(asset.get("display_code"), f"batch.assets[{index}].display_code")
            if code in source_by_code:
                raise BenchmarkError(f"duplicate source asset code in API response: {code}")
            source_by_code[code] = asset
        elif kind == "result":
            parent = _nonempty_string(asset.get("parent_asset_id"), f"batch.assets[{index}].parent_asset_id")
            bucket = results_by_parent.setdefault(parent, {})
            if asset_id in bucket:
                raise BenchmarkError(f"duplicate result asset id in API response: {asset_id}")
            bucket[asset_id] = asset

    jobs = _job_index(batch.get("latest_jobs"))
    cases: list[dict[str, Any]] = []
    relevant_jobs: list[Mapping[str, Any]] = []
    manual_interventions: list[dict[str, Any]] = []
    reviewer_reasons: set[str] = set()
    failure_codes: set[str] = set()

    for manifest_case in manifest["cases"]:
        code = manifest_case["source_asset_code"]
        source = source_by_code.get(code)
        if source is None:
            raise BenchmarkError(f"source asset code is absent from API response: {code}")
        source_id = source["id"]
        stage_jobs: dict[str, Mapping[str, Any]] = {}
        for stage in ("analyze", "swap"):
            job = jobs.get((stage, source_id))
            if job is not None:
                stage_jobs[stage] = job

        candidates = results_by_parent.get(source_id, {})
        swap_output = stage_jobs.get("swap", {}).get("output")
        selected_result_id = swap_output.get("result_asset_id") if isinstance(swap_output, dict) else None
        if selected_result_id is not None:
            if not isinstance(selected_result_id, str) or selected_result_id not in candidates:
                raise BenchmarkError(f"swap result mapping is invalid for source asset code: {code}")
        elif len(candidates) == 1:
            selected_result_id = next(iter(candidates))
        elif len(candidates) > 1:
            raise BenchmarkError(f"multiple uncorrelated results for source asset code: {code}")

        validation_job = jobs.get(("validate", selected_result_id)) if selected_result_id else None
        if validation_job is not None:
            stage_jobs["validate"] = validation_job
        status = validation_job.get("status") if validation_job else "not_run"
        if status not in _FINAL_STATUSES:
            status = "not_run"

        case_records: list[dict[str, Any]] = []
        for stage in _STAGES:
            job = stage_jobs.get(stage)
            if job is None:
                continue
            relevant_jobs.append(job)
            if job.get("status") in {"needs_review", "failed"}:
                record = _reason_record(code, stage, job)
                case_records.append(record)
                manual_interventions.append(record)
                reviewer_reasons.update(record["reviewer_reasons"])
                if record["failure_code"]:
                    failure_codes.add(record["failure_code"])

        evidence = _quality_evidence(validation_job)
        cases.append(
            {
                "source_asset_code": code,
                "scenario_tags": manifest_case["scenario_tags"],
                "source_asset_id": source_id,
                "result_asset_id": selected_result_id,
                "final_status": status,
                "stage_durations_seconds": {
                    stage: job_duration(stage_jobs[stage]) if stage in stage_jobs else None
                    for stage in _STAGES
                },
                "manual_intervention": case_records,
                "quality_evidence": evidence,
            }
        )

    status_counts = {status: 0 for status in _FINAL_STATUSES}
    for case in cases:
        status_counts[case["final_status"]] += 1
    total = len(cases)
    stage_metrics = {
        stage: _stage_metrics([job for job in relevant_jobs if job.get("stage") == stage])
        for stage in _STAGES
    }
    if relevant_jobs:
        wall_start = min(_timestamp(job.get("created_at"), "job.created_at") for job in relevant_jobs)
        wall_end = max(_timestamp(job.get("updated_at"), "job.updated_at") for job in relevant_jobs)
        wall_seconds: float | None = (wall_end - wall_start).total_seconds()
        if wall_seconds < 0:
            raise BenchmarkError("task-loop wall interval is negative")
    else:
        wall_seconds = None

    return {
        "batch_id": manifest["batch_id"],
        "case_count": total,
        "scenario_coverage": {
            tag: sum(tag in case["scenario_tags"] for case in cases)
            for tag in sorted(REQUIRED_SCENARIO_TAGS)
        },
        "cases": cases,
        "stage_latency_seconds": stage_metrics,
        "total_wall_interval_seconds": wall_seconds,
        "outcomes": {
            "counts": status_counts,
            "rates": {status: status_counts[status] / total for status in _FINAL_STATUSES},
        },
        "manual_interventions": manual_interventions,
        "reviewer_reasons": sorted(reviewer_reasons)[:_MAX_REASONS],
        "failure_codes": sorted(failure_codes)[:_MAX_REASONS],
        "quality_claim": "No visual-fidelity claim is inferred from job status; only persisted reviewer evidence is reported.",
    }


def collect_batch(
    manifest: Mapping[str, Any],
    *,
    base_url: str,
    execute: bool = False,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    manifest = validate_manifest(manifest)
    owned_client = client is None
    api = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=600.0)
    path = f"/api/v1/batches/{manifest['batch_id']}"
    try:
        if execute:
            response = api.post(f"{path}/task/run")
            response.raise_for_status()
        response = api.get(path)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise BenchmarkError("API returned invalid JSON") from error
        return summarize_batch(manifest, payload)
    except httpx.HTTPError as error:
        raise BenchmarkError(f"API request failed: {_safe_text(str(error))}") from error
    finally:
        if owned_client:
            api.close()


def exit_code(report: Mapping[str, Any]) -> int:
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise BenchmarkError("report.cases must be a list")
    for case in cases:
        if not isinstance(case, dict):
            raise BenchmarkError("report.cases must contain objects")
        if case.get("final_status") in {"failed", "not_run"}:
            return 1
        evidence = case.get("quality_evidence")
        if not isinstance(evidence, dict) or evidence.get("available") is not True:
            return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark a configured PersonaWeave batch")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        report = collect_batch(manifest, base_url=args.base_url, execute=args.execute)
    except BenchmarkError as error:
        print(f"benchmark error: {_safe_text(str(error))}", file=sys.stderr)
        return 2
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        try:
            args.output.write_text(serialized, encoding="utf-8")
        except OSError as error:
            print(f"benchmark error: cannot write output: {_safe_text(str(error))}", file=sys.stderr)
            return 2
    else:
        print(serialized, end="")
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
