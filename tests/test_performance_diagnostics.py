"""Performance collection remains read-only, bounded and free of pictures or configuration secrets."""

import json

import pytest

from scripts import collect_windows_performance as diagnostic


def test_collection_filters_active_metadata_and_never_collects_results(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/portable-runtime.json").write_text(json.dumps({"url": "http://127.0.0.1:8765"}))
    (tmp_path / "manifest.json").write_text(json.dumps({"edition": "windows-x64-gpu", "files": {"private": "secret"}}))
    monkeypatch.setattr(diagnostic.shutil, "which", lambda _: None)
    monkeypatch.setattr(diagnostic, "memory_report", lambda: {"physical_available_mib": 123})
    calls = []

    def read(_client, url):
        calls.append(url)
        if url.endswith("health"):
            return {"providers": ["CPUExecutionProvider"], "private": "secret"}
        if "task-history" in url:
            return {
                "data": [
                    {"task_id": "pw_task_123", "status": "PROCESSING"},
                    {"task_id": "pw_task_old", "status": "COMPLETED"},
                ]
            }
        return {"data": {"waiting_reason": "等待可用内存", "results": [{"original_url": "data:image/png;private"}]}}

    monkeypatch.setattr(diagnostic, "read_json", read)
    report = diagnostic.collect(tmp_path)
    assert report["active_batches"][0]["waiting_reason"] == "等待可用内存"
    assert len(calls) == 3
    assert "private" not in json.dumps(report)
    assert "secret" not in json.dumps(report)
    assert report["ai_requests"] == report["model_inference_calls"] == 0


@pytest.mark.parametrize(
    "url", ["https://example.com", "http://127.0.0.1:8765@evil.test", "http://127.0.0.1:8765/private"]
)
def test_collector_refuses_nonlocal_or_malformed_runtime_address(tmp_path, monkeypatch, url):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/portable-runtime.json").write_text(json.dumps({"url": url}))
    monkeypatch.setattr(diagnostic.shutil, "which", lambda _: None)
    monkeypatch.setattr(diagnostic, "read_json", lambda *_: pytest.fail("must not contact this address"))
    assert diagnostic.collect(tmp_path)["running_server_error"] == "ValueError"
