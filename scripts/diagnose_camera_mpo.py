"""Non-production camera-image regression using installed routes/models and an isolated project store."""

import argparse
import base64
import gc
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def diagnose(root, image_path, report_path):
    """Exercise the supplied image locally; never contact VLM/image generation or modify real projects."""
    os.chdir(root)
    sys.path.insert(0, str(root))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from PIL import Image

    from api.inference_limiter import InferenceLimiter
    from api.projects.store import ProjectStore
    from api.routes.faces import router as faces_router
    from api.routes.projects import router as projects_router
    from core.detector import FaceDetector
    from core.image_security import decode_image_bytes
    from core.recognizer import FaceRecognizer
    from utils.gpu_helper import get_optimal_providers

    data = image_path.read_bytes()
    source = "data:image/jpeg;base64," + base64.b64encode(data).decode()
    with Image.open(image_path) as header:
        report = {"format": header.format, "size": list(header.size), "bytes": len(data)}
    report.update(sha256=hashlib.sha256(data).hexdigest(), ai_requests=0)
    try:
        pixels = decode_image_bytes(data)
    except ValueError as exc:
        report["decode_error"] = str(exc)
    else:
        report["decoded_shape"] = list(pixels.shape)
        del pixels
    providers = get_optimal_providers()
    app = FastAPI()
    app.include_router(faces_router)
    app.include_router(projects_router)
    app.state.pipeline = SimpleNamespace(
        detector=FaceDetector(str(root / "models/buffalo_l/det_10g.onnx"), providers=providers),
        recognizer=FaceRecognizer(str(root / "models/buffalo_l/w600k_r50.onnx"), providers=providers),
    )
    report["providers"] = app.state.pipeline.detector.model.session.get_providers()
    app.state.inference_limiter = InferenceLimiter(1)
    app.state.refinement_sessions = None
    app.state.task_manager = None

    class LocalOnlyArbiter:
        """Explicitly disable optional paid arbitration while exercising real SCRFD/ArcFace."""

        async def refine_clusters(self, *, clusters, **kwargs):
            return clusters

    with tempfile.TemporaryDirectory(prefix="personaweave-mpo-") as directory:
        base = Path(directory)
        app.state.projects = ProjectStore(base / "projects.sqlite3", base / "图片")
        with TestClient(app) as client, patch("core.ai.arbiter.VLMIdentityArbiter", LocalOnlyArbiter):
            detected = client.post("/api/v1/detect-faces", json={"image_url": source})
            report["detection"] = {"status": detected.status_code, "response": detected.json()}
            clustered = client.post("/api/v1/cluster-faces", json={"image_urls": [source, source]})
            report["clustering"] = {"status": clustered.status_code, "response": clustered.json()}
            project = client.post("/api/v1/projects").json()["data"]
            endpoint = "/api/v1/projects/" + project["id"]
            imported = client.post(endpoint + "/assets", json={"image": source})
            report["import"] = {"status": imported.status_code}
            if imported.status_code == 200:
                asset = imported.json()["data"][0]
                doc = {
                    "schema": 1,
                    "studio": {"targetImages": [{"id": "camera", "url": asset}]},
                    "workbench": {},
                    "warnings": [],
                }
                saved = client.put(endpoint, json={"expected_revision": 0, "title": "MPO", "document": doc})
                restored = client.post(endpoint + "/restore")
                media = client.get(endpoint + "/assets/" + asset["$asset"])
                report["project"] = {
                    "save_status": saved.status_code,
                    "restore_status": restored.status_code,
                    "media_status": media.status_code,
                    "media_type": media.headers.get("content-type"),
                    "same_bytes": media.content == data,
                    "same_document": restored.json().get("data", {}).get("document") == doc,
                }
        gc.collect()  # Release legacy SQLite connection cycles before Windows temp cleanup.
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    diagnose(args.root.resolve(), args.image.resolve(), args.report.resolve())
