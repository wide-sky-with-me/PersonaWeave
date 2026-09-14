"""Startup readiness checks without inference or paid provider calls."""

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from api.refinement.legacy import LegacyRefinementArchive
from api.refinement.service import RefinementSessionService
from api.refinement.store import RefinementSessionStore


@pytest.mark.parametrize(
    ("variable", "key"),
    [
        (variable, key)
        for variable in ("AI_VISION_API_KEY", "AI_IMAGE_API_KEY", "AI_VISION_API_BASE", "AI_IMAGE_MODEL")
        for key in (None, "", "   ")
    ]
    + [("generation", policy) for policy in (None, {}, {"size": "auto", "sizes": ["1024x1024"]})],
)
def test_missing_or_invalid_ai_config_keeps_local_api_up(monkeypatch, tmp_path, variable, key):
    module = importlib.import_module("api.app")
    lifecycle = importlib.import_module("api.lifecycle")
    system_routes = importlib.import_module("api.routes.system")
    monkeypatch.setattr(lifecycle, "PersonaWeavePipeline", lambda: object())
    monkeypatch.setattr(lifecycle, "TaskManager", lambda **kwargs: SimpleNamespace(tasks={}, _requests={}))
    store = RefinementSessionStore(tmp_path / "sessions.sqlite3", tmp_path / "sessions", tmp_path / "published")
    monkeypatch.setattr(lifecycle, "RefinementSessionService", lambda ai: RefinementSessionService(ai=ai, store=store))
    monkeypatch.setattr(
        lifecycle,
        "LegacyRefinementArchive",
        lambda: LegacyRefinementArchive(
            tmp_path / "legacy-staging", tmp_path / "legacy-accepted", db_path=tmp_path / "legacy.sqlite3"
        ),
    )
    monkeypatch.setattr(system_routes, "get_optimal_providers", lambda: ["CPUExecutionProvider"])
    if variable == "generation":
        config = yaml.safe_load((Path(__file__).resolve().parents[1] / "config/ai_gateway.yaml").read_text())
        if key is None:
            config["image_models"][0].pop("generation")
        else:
            config["image_models"][0]["generation"] = key
        config_path = tmp_path / "invalid-gateway.yaml"
        config_path.write_text(yaml.safe_dump(config))
        monkeypatch.setenv("PERSONAWEAVE_AI_CONFIG", str(config_path))
    elif key is None:
        monkeypatch.delenv(variable, raising=False)
    else:
        monkeypatch.setenv(variable, key)

    with TestClient(module.app) as client:
        health = client.get("/health").json()
        assert health["pipeline_ready"] is True
        assert health["ai_refinement_ready"] is False
        assert client.get("/api/v1/ai/models").status_code == 503
        assert client.get("/api/v1/refinement/tasks/missing").status_code == 404
        assert client.get("/api/v1/refinement/legacy-tasks").status_code == 200
        assert client.post("/api/v1/refinement/tasks", json={}).status_code == 410
        assert client.post("/api/v1/refinement/review", json={}).status_code == 410
