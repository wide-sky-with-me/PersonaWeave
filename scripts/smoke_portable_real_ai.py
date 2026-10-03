"""Non-production real-AI end-to-end run on a disposable portable copy; sends paid requests.

Run with the copy's own interpreter: runtime\\python.exe scripts/smoke_portable_real_ai.py ROOT REPORT.json --allow-paid
Flow: local swap -> refinement session -> discuss turn (vision) -> generate turn (one image
edit + review) -> accept. Each paid request is sent once and never retried. Never run it on a
directory holding user data: it creates a session and publishes an accepted result there.
"""

import base64
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if "--allow-paid" not in sys.argv:
    sys.exit("--allow-paid is required: this run calls the configured vision and image-edit services")
root, report_path = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root))
from dotenv import dotenv_values  # noqa: E402  (the copy's own site-packages)

secrets = [v for v in dotenv_values(root / ".env").values() if v and len(v) > 6]


def clean(text):
    text = str(text)
    for secret in secrets:
        text = text.replace(secret, "<redacted>")
    return text


op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
report = {"steps": []}


def step(name, **info):
    report["steps"].append({"step": name, **{k: clean(v) if isinstance(v, str) else v for k, v in info.items()}})
    print(name, json.dumps(report["steps"][-1], ensure_ascii=False)[:700], flush=True)


def call(path, body=None, method=None, raw=False):
    req = urllib.request.Request(
        url + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with op.open(req, timeout=600) as r:
            data = r.read()
            return data if raw else json.loads(data)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {path}: {clean(e.read()[:500].decode('utf-8', 'replace'))}") from None


def uri(rel):
    return "data:image/png;base64," + base64.b64encode((root / rel).read_bytes()).decode()


state = root / "data/portable-runtime.json"
state.unlink(missing_ok=True)
# Must outlive the context of this statement: the child writes to it until terminated below.
log = open(report_path.with_suffix(".server.log"), "w", encoding="utf-8")  # noqa: SIM115
proc = subprocess.Popen(
    [str(root / "runtime/python.exe"), "-u", "-X", "utf8", "-m", "api.portable", "--no-browser", "--port", "0"],
    cwd=root,
    stdout=log,
    stderr=subprocess.STDOUT,
)
ok = False
try:
    t0 = time.time()
    while True:
        try:
            url = json.loads(state.read_text())["url"]
            health = json.loads(op.open(url + "/api/v1/health", timeout=5).read())
            if health["pipeline_ready"]:
                break
        except Exception:
            time.sleep(1)
        assert time.time() - t0 < 600, "startup timeout"
    step("health", providers=health["providers"], ai_refinement_ready=health["ai_refinement_ready"])
    assert health["ai_refinement_ready"], "AI not configured"

    swapped = call(
        "/api/v1/swap-sync",
        {
            "source_manual_images": [uri("data/test_target/01.png")],
            "target_image_url": uri("data/test_images/group-01.png"),
            "person_index": 1,
            "parameters": {"similarity_threshold": 0.38, "swapper_model": "auto"},
            "return_base64": True,
        },
    )["data"]
    step("swap", detected=swapped["faces_detected"], swapped=swapped["faces_swapped"])
    baseline = swapped["processed_base64"]
    (report_path.with_suffix(".baseline.jpg")).write_bytes(base64.b64decode(baseline.split(",", 1)[1]))

    session = call(
        "/api/v1/refinement/sessions",
        {
            "request_id": "e2e-create-1",
            "target_id": "e2e-real-ai",
            "title": "真实AI端到端验证",
            "original_image_url": uri("data/test_images/group-01.png"),
            "baseline_image_url": baseline,
            "people": [
                {
                    "person_id": 1,
                    "face_index": 1,
                    "person_locator": "合影中从左数第一位",
                    "source_manual_images": [uri("data/test_target/01.png")],
                }
            ],
        },
    )["data"]
    sid = session["id"]
    step("session", id=sid, revision=session["revision"], versions=len(session["versions"]))

    def run_turn(request_id, mode, message, **extra):
        nonlocal_session = call(f"/api/v1/refinement/sessions/{sid}")["data"]
        body = {
            "request_id": request_id,
            "expected_revision": nonlocal_session["revision"],
            "base_version_id": nonlocal_session["baseline_version_id"] if not extra.get("base") else extra["base"],
            "mode": mode,
            "message": message,
        }
        data = call(f"/api/v1/refinement/sessions/{sid}/turns", body)["data"]
        turn_id = data["turns"][-1]["id"]
        deadline = time.time() + 900
        while time.time() < deadline:
            data = call(f"/api/v1/refinement/sessions/{sid}")["data"]
            turn = next(t for t in data["turns"] if t["id"] == turn_id)
            if turn["status"] in {"review_required", "completed", "failed", "cancelled"}:
                return data, turn
            time.sleep(3)
        raise TimeoutError("turn did not finish")

    data, turn = run_turn("e2e-discuss-1", "discuss", "请检查人物1的换脸是否自然，参考身份是否一致，只讨论不要生成。")
    step(
        "discuss",
        status=turn["status"],
        error=turn.get("error"),
        versions=len(data["versions"]),
        issues=len((turn.get("diagnosis") or {}).get("issues", [])),
        summary=str(turn.get("assistant_message") or "")[:300],
    )
    assert turn["status"] == "completed" and len(data["versions"]) == 1, "discuss must complete without generating"

    data, turn = run_turn(
        "e2e-generate-1",
        "generate",
        "让人物1的肤色、光线和清晰度与整张合影自然协调；保持人数、姿态、构图、背景和其他人物不变。",
    )
    step(
        "generate",
        status=turn["status"],
        error=turn.get("error"),
        review_error=turn.get("review_error"),
        versions=len(data["versions"]),
        model=(turn.get("generation_metadata") or {}).get("model"),
        review_keys=sorted((turn.get("review") or {}).keys()),
    )
    assert turn["status"] == "review_required" and turn.get("candidate_version_id"), "candidate expected"
    candidate = next(v for v in data["versions"] if v["id"] == turn["candidate_version_id"])
    content = call(f"/api/v1/refinement/sessions/{sid}/artifacts/{candidate['digest']}", raw=True)
    assert hashlib.sha256(content).hexdigest() == candidate["digest"]
    report_path.with_suffix(".candidate.png").write_bytes(content)
    step("candidate", bytes=len(content), digest=candidate["digest"])

    accepted = call(
        f"/api/v1/refinement/sessions/{sid}/accept",
        {"expected_revision": data["revision"], "version_id": candidate["id"], "candidate_digest": candidate["digest"]},
    )["data"]
    step(
        "accept",
        accepted_version_id=accepted["accepted_version_id"],
        published=bool(accepted.get("accepted_image_url")),
    )
    assert accepted["accepted_version_id"] == candidate["id"]
    ok = True
except Exception as exc:
    step("FAILED", error=f"{type(exc).__name__}: {exc}")
finally:
    proc.terminate()
    try:
        proc.wait(30)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()
    report["passed"] = ok
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("E2E_REAL_AI_PASSED" if ok else "E2E_REAL_AI_FAILED", flush=True)
