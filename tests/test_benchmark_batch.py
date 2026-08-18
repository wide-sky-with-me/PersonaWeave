from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "benchmark_batch.py"
SPEC = importlib.util.spec_from_file_location("benchmark_batch", MODULE_PATH)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


ALL_TAGS = [
    "frontal",
    "profile",
    "occlusion",
    "multi_person",
    "low_resolution",
    "expression",
    "lighting",
]


def manifest(case_count: int = 12) -> dict:
    cases = []
    for index in range(1, case_count + 1):
        tags = [ALL_TAGS[(index - 1) % len(ALL_TAGS)]]
        if index == 1:
            tags = ALL_TAGS
        cases.append({"source_asset_code": f"I{index:02d}", "scenario_tags": tags})
    return {"batch_id": "batch-1", "cases": cases}


def stamp(seconds: int) -> str:
    return (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)).isoformat()


def reviewer_verdicts(*, passed: bool = True) -> list[dict]:
    return [
        {
            "reviewer_id": "reviewer_a",
            "provider": "model-a",
            "passed": passed,
            "checks": {"identity_preserved": passed},
            "reasons": [] if passed else ["identity mismatch"],
        },
        {
            "reviewer_id": "reviewer_b",
            "provider": "model-b",
            "passed": passed,
            "checks": {"protected_faces_unchanged": passed},
            "reasons": [] if passed else ["protected face changed"],
        },
    ]


def job(
    stage: str,
    item_key: str,
    duration: int,
    *,
    status: str = "succeeded",
    output: dict | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    return {
        "id": f"job-{stage}-{item_key}",
        "stage": stage,
        "item_key": item_key,
        "status": status,
        "phase": "completed",
        "created_at": stamp(0),
        "updated_at": stamp(duration),
        "error_code": error_code,
        "error_message": error_message,
        "output": output or {},
    }


def batch_response() -> dict:
    assets = []
    jobs = []
    for index in range(1, 13):
        source_id = f"source-{index}"
        result_id = f"result-{index}"
        assets.extend(
            [
                {
                    "id": source_id,
                    "kind": "source",
                    "display_code": f"I{index:02d}",
                },
                {
                    "id": result_id,
                    "kind": "result",
                    "display_code": f"O{index:02d}",
                    "parent_asset_id": source_id,
                },
            ]
        )
        jobs.extend(
            [
                job("analyze", source_id, index),
                job(
                    "swap",
                    source_id,
                    index * 2,
                    output={"result_asset_id": result_id},
                ),
                job(
                    "validate",
                    result_id,
                    index * 3,
                    output={
                        "passed": True,
                        "checks": {"all_checks_passed": True},
                        "reviewer_verdicts": reviewer_verdicts(),
                        "review_reasons": [],
                    },
                ),
            ]
        )
    return {"id": "batch-1", "assets": assets, "latest_jobs": jobs}


def replace_job(response: dict, replacement: dict) -> None:
    response["latest_jobs"] = [
        replacement if existing["id"] == replacement["id"] else existing
        for existing in response["latest_jobs"]
    ]


def test_manifest_accepts_twelve_representative_cases() -> None:
    parsed = benchmark.validate_manifest(manifest())

    assert len(parsed["cases"]) == 12
    assert parsed["cases"][0]["source_asset_code"] == "I01"
    assert {
        tag for case in parsed["cases"] for tag in case["scenario_tags"]
    } == set(ALL_TAGS)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(cases=value["cases"][:9]), "10 to 20"),
        (
            lambda value: value["cases"][0].update(scenario_tags=["frontal", "unknown"]),
            "unsupported scenario tags",
        ),
        (
            lambda value: value["cases"][0].update(source_asset_code="image-01"),
            "must match Ixx",
        ),
    ],
)
def test_manifest_rejects_invalid_count_tag_and_code(mutation, message: str) -> None:
    value = manifest()
    mutation(value)

    with pytest.raises(benchmark.BenchmarkError, match=message):
        benchmark.validate_manifest(value)


def test_summary_correlates_assets_and_jobs_with_stable_metrics() -> None:
    response = batch_response()
    response["assets"].reverse()
    response["latest_jobs"].reverse()

    report = benchmark.summarize_batch(manifest(), response)

    assert [case["source_asset_code"] for case in report["cases"]] == [
        f"I{index:02d}" for index in range(1, 13)
    ]
    assert report["cases"][0]["result_asset_id"] == "result-1"
    assert report["stage_latency_seconds"] == {
        "analyze": {
            "count": 12,
            "p50_seconds": 6.0,
            "p95_seconds": 12.0,
            "max_seconds": 12.0,
        },
        "swap": {
            "count": 12,
            "p50_seconds": 12.0,
            "p95_seconds": 24.0,
            "max_seconds": 24.0,
        },
        "validate": {
            "count": 12,
            "p50_seconds": 18.0,
            "p95_seconds": 36.0,
            "max_seconds": 36.0,
        },
    }
    assert report["total_wall_interval_seconds"] == 36.0
    assert report["outcomes"]["counts"] == {
        "succeeded": 12,
        "needs_review": 0,
        "failed": 0,
        "not_run": 0,
    }
    assert all(case["quality_evidence"]["available"] for case in report["cases"])


def test_summary_attributes_review_and_failure_reasons_without_secrets() -> None:
    response = batch_response()
    review = job(
        "validate",
        "result-1",
        3,
        status="needs_review",
        output={
            "passed": False,
            "review_reasons": ["occlusion requires inspection"],
            "reviewer_verdicts": [
                {
                    **reviewer_verdicts(passed=False)[0],
                    "reasons": ["face edge uncertain"],
                },
                reviewer_verdicts(passed=False)[1],
            ],
        },
    )
    failure = job(
        "validate",
        "result-2",
        6,
        status="failed",
        error_code="provider_unavailable",
        error_message="Authorization: Bearer sk-private-token upstream failed",
    )
    replace_job(response, review)
    replace_job(response, failure)

    report = benchmark.summarize_batch(manifest(), response)

    assert report["outcomes"]["counts"]["needs_review"] == 1
    assert report["outcomes"]["counts"]["failed"] == 1
    assert report["failure_codes"] == ["provider_unavailable"]
    assert "face edge uncertain" in report["reviewer_reasons"]
    first, second = report["manual_interventions"][:2]
    assert first["source_asset_code"] == "I01"
    assert first["review_reasons"] == ["occlusion requires inspection"]
    assert second["source_asset_code"] == "I02"
    assert second["failure_code"] == "provider_unavailable"
    assert "sk-private-token" not in second["error_message"]
    assert "[REDACTED]" in second["error_message"]


def test_quality_check_strings_are_redacted_and_bounded() -> None:
    response = batch_response()
    verdicts = reviewer_verdicts()
    verdicts[0]["checks"]["diagnostic"] = "token=private-value " + ("x" * 1000)
    replacement = job(
        "validate",
        "result-1",
        3,
        output={"passed": True, "reviewer_verdicts": verdicts},
    )
    replace_job(response, replacement)

    report = benchmark.summarize_batch(manifest(), response)
    diagnostic = report["cases"][0]["quality_evidence"]["reviewer_verdicts"][0][
        "checks"
    ]["diagnostic"]

    assert "private-value" not in diagnostic
    assert "[REDACTED]" in diagnostic
    assert len(diagnostic) <= 240


def test_missing_dual_verdict_evidence_is_explicit_and_fails_exit_decision() -> None:
    response = batch_response()
    incomplete = job(
        "validate",
        "result-1",
        3,
        output={
            "passed": True,
            "reviewer_verdicts": [reviewer_verdicts()[0]],
        },
    )
    replace_job(response, incomplete)

    report = benchmark.summarize_batch(manifest(), response)
    evidence = report["cases"][0]["quality_evidence"]

    assert evidence["available"] is False
    assert evidence["basis"] == "missing"
    assert "not persisted" in evidence["missing_reason"]
    assert benchmark.exit_code(report) == 1


def test_exit_decision_is_pure_behavior() -> None:
    report = benchmark.summarize_batch(manifest(), batch_response())
    assert benchmark.exit_code(report) == 0

    report["cases"][0]["final_status"] = "not_run"
    assert benchmark.exit_code(report) == 1

    report["cases"][0]["final_status"] = "failed"
    assert benchmark.exit_code(report) == 1


def test_collect_optionally_executes_before_fetch_without_live_network() -> None:
    requests: list[tuple[str, str]] = []
    response_payload = batch_response()

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"completed": True}, request=request)
        return httpx.Response(200, json=response_payload, request=request)

    with httpx.Client(
        base_url="https://personaweave.example",
        transport=httpx.MockTransport(handle),
        timeout=600.0,
    ) as client:
        report = benchmark.collect_batch(
            manifest(),
            base_url="https://ignored.example",
            execute=True,
            client=client,
        )

    assert requests == [
        ("POST", "/api/v1/batches/batch-1/task/run"),
        ("GET", "/api/v1/batches/batch-1"),
    ]
    assert report["case_count"] == 12
