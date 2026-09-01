"""
Unit and Integration Tests for PersonaWeave FastAPI Service.
"""

import base64
import csv
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import app
from api.portrait_library_import import import_manifest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def fixture_data_uri(name: str) -> str:
    suffix = Path(name).suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode((FIXTURES_DIR / name).read_bytes()).decode("ascii")


@pytest.fixture(scope="module")
def client():
    """Initializes TestClient with lifespan context."""
    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoint(client):
    """Verifies the health check endpoints."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app_name"] == "PersonaWeave"
    assert data["version"] == "0.1.0"
    assert data["pipeline_ready"] is True

    # Test api/v1 prefix
    response_v1 = client.get("/api/v1/health")
    assert response_v1.status_code == 200
    assert response_v1.json()["status"] == "ok"


def test_ai_image_model_catalog_uses_gateway_configuration(client):
    response = client.get("/api/v1/ai/models")
    assert response.status_code == 200
    data = response.json()
    aliases = {item["alias"] for item in data["models"]}
    assert data["default_alias"] in aliases
    assert next(item for item in data["models"] if item["is_default"])["alias"] == data["default_alias"]
    assert all("api_key" not in item for item in data["models"])


def test_sync_swap_endpoint(client):
    """Verifies synchronous single-image face swapping."""
    payload = {
        "source_manual_images": [fixture_data_uri("source-01.png")],
        "target_image_url": fixture_data_uri("group-01.png"),
        "person_index": 1,
        "parameters": {
            "similarity_threshold": 0.38,
            "swapper_model": "auto",
        },
        "return_base64": True,
    }

    response = client.post("/api/v1/swap-sync", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["code"] == 200
    assert res_data["data"]["faces_detected"] >= 1
    assert res_data["data"]["faces_swapped"] >= 1
    assert res_data["data"]["processed_base64"] is not None
    assert res_data["data"]["processed_base64"].startswith("data:image/jpeg;base64,")
    assert res_data["data"]["model_routing"]
    assert res_data["data"]["model_routing"][0]["selected"] in {
        "inswapper_128",
        "ghost_1_256",
        "hyperswap_1c_256",
    }


def test_sync_swap_endpoint_resolves_managed_library_asset_id(client, tmp_path, monkeypatch):
    """Verifies a managed portrait-library asset ID resolves through to a swap."""
    manifest_source = tmp_path / "manifest_source"
    (manifest_source / "T01").mkdir(parents=True)
    shutil.copy2(FIXTURES_DIR / "source-01.png", manifest_source / "T01" / "01_front.png")
    with (manifest_source / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "gender", "age", "category", "label", "file"])
        writer.writeheader()
        writer.writerow(
            {
                "id": "T01",
                "gender": "female",
                "age": "25",
                "category": "angle",
                "label": "01_front",
                "file": "T01/01_front.png",
            }
        )

    library_root = tmp_path / "library"
    import_manifest(manifest_source, library_root)
    monkeypatch.setenv("PERSONAWEAVE_PORTRAIT_LIBRARY_ROOT", str(library_root))

    catalog_res = client.get("/api/v1/portrait-library")
    assert catalog_res.status_code == 200
    catalog = catalog_res.json()["data"]
    assert catalog["available"] is True
    asset_id = catalog["identities"][0]["cover_asset_id"]

    payload = {
        "source_asset_ids": [asset_id],
        "target_image_url": fixture_data_uri("group-01.png"),
        "person_index": 1,
        "parameters": {
            "similarity_threshold": 0.38,
            "swapper_model": "auto",
        },
    }
    response = client.post("/api/v1/swap-sync", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["data"]["faces_detected"] >= 1
    assert res_data["data"]["faces_swapped"] >= 1


def test_batch_task_lifecycle(client):
    """Verifies submitting, tracking, and completing a batch task."""
    payload = {
        "source_manual_images": [
            fixture_data_uri("source-01.png"),
            fixture_data_uri("source-02.png"),
        ],
        "person_index": 1,
        "batch_images": [
            {"client_target_id": "group-01", "image_url": fixture_data_uri("group-01.png")},
            {"client_target_id": "group-02", "image_url": fixture_data_uri("group-02.png")},
        ],
        "parameters": {
            "similarity_threshold": 0.38,
            "swapper_model": "inswapper_128",
        },
    }

    response = client.post("/api/v1/batch-tasks", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 200
    task_id = data["data"]["task_id"]
    assert task_id.startswith("pw_task_")
    assert data["data"]["total_images"] == 2

    # Poll for completion (max 15 seconds)
    completed = False
    for _ in range(30):
        time.sleep(0.5)
        status_res = client.get(f"/api/v1/batch-tasks/{task_id}")
        assert status_res.status_code == 200
        task_detail = status_res.json()["data"]
        if task_detail["status"] == "COMPLETED":
            completed = True
            assert task_detail["progress"] == 1.0
            assert len(task_detail["results"]) == 2
            assert task_detail["results"][0]["status"] == "SUCCESS"
            assert task_detail["results"][0]["client_target_id"] == "group-01"
            assert task_detail["results"][1]["client_target_id"] == "group-02"
            assert task_detail["results"][0]["model_routing"][0]["selected"] == "inswapper_128"
            assert task_detail["completed_at"]
            # Verify file download endpoint
            file_url = task_detail["results"][0]["processed_url"]
            file_res = client.get(file_url)
            assert file_res.status_code == 200
            assert file_res.headers["content-type"].startswith("image/")
            original_url = task_detail["results"][0]["original_url"]
            assert original_url.startswith(f"/api/v1/files/{task_id}/original_")
            assert client.get(original_url).status_code == 200

            history_res = client.get("/api/v1/task-history/batch-tasks", params={"limit": 10})
            assert history_res.status_code == 200
            summary = next(item for item in history_res.json()["data"] if item["task_id"] == task_id)
            assert summary["viewable_results"] == 2
            assert "results" not in summary
            assert "original_url" not in str(summary)

            legacy_history = client.get("/api/v1/batch-tasks", params={"limit": 10})
            legacy_task = next(item for item in legacy_history.json()["data"] if item["task_id"] == task_id)
            assert len(legacy_task["results"]) == 2
            break
        elif task_detail["status"] == "FAILED":
            pytest.fail(f"Batch task failed with error: {task_detail.get('error')}")

    assert completed, "Task did not complete within timeout."


def test_batch_task_invalid_not_found(client):
    """Verifies 404 response for non-existent task."""
    response = client.get("/api/v1/batch-tasks/pw_task_non_existent")
    assert response.status_code == 404


def test_batch_history_limit_is_bounded(client):
    assert client.get("/api/v1/task-history/batch-tasks", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/task-history/batch-tasks", params={"limit": 101}).status_code == 422


def test_static_file_routes_reject_path_traversal(client):
    output_response = client.get("/api/v1/files/%2E%2E/%2E%2E/.env")
    data_response = client.get("/api/v1/data/%2E%2E/.env")
    assert output_response.status_code == 404
    assert data_response.status_code == 404
