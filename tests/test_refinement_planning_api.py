"""Retired generation and scope endpoints return 410 before loading images or consulting AI."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("route", ["review", "plan", "tasks", "tasks/old-task/cancel", "tasks/old-task/people/1/retry"])
@pytest.mark.parametrize("payload", [{}, {"swapped_image_url": "http://127.0.0.1/private.png", "people": []}])
def test_retired_mutation_routes_are_gone_without_io(monkeypatch, route, payload):
    from api.app import app

    def forbidden(*args, **kwargs):
        raise AssertionError("Retired endpoints must not load media")

    monkeypatch.setattr("api.media.load_image_from_source", forbidden)
    monkeypatch.setattr(app.state, "refinement_service", None, raising=False)
    response = TestClient(app).post(f"/api/v1/refinement/{route}", json=payload)
    assert response.status_code == 410
    assert "session" in str(response.json()).lower()
