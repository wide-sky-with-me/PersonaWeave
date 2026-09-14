#!/usr/bin/env python3
"""Preflight and launch the PersonaWeave API and Web Studio."""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
API_PORT = 8000
WEB_PORT = 3000
REQUIRED_MODELS = (
    "models/buffalo_l/det_10g.onnx",
    "models/buffalo_l/w600k_r50.onnx",
    "models/buffalo_l/2d106det.onnx",
    "models/inswapper_128.onnx",
)
ENV_REFERENCE = re.compile(r"os\.environ/([A-Za-z_][A-Za-z0-9_]*)")


def fail(message: str) -> None:
    print(f"[dev] ERROR: {message}", file=sys.stderr)


def load_dotenv_values(path: Path) -> dict[str, str]:
    """Read dotenv values for preflight only; never mutate the environment file."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[:1] == value[-1:] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def check_providers(uv: str, env: dict[str, str]) -> bool:
    command = [
        uv,
        "run",
        "python",
        "-c",
        "from utils.gpu_helper import get_optimal_providers; "
        "print(','.join(p if isinstance(p, str) else p[0] for p in get_optimal_providers()))",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        fail(f"could not inspect ONNX Runtime providers: {exc}")
        return False
    providers = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if not providers:
        fail("ONNX Runtime reported no execution providers")
        return False
    print(f"[dev] ONNX providers: {providers}")
    if "CUDAExecutionProvider" in providers:
        print("[dev] GPU acceleration: CUDA available")
    else:
        print("[dev] WARNING: CUDA unavailable; inference will use CPU", file=sys.stderr)
    return "CPUExecutionProvider" in providers or "CUDAExecutionProvider" in providers


def preflight() -> tuple[str, str, dict[str, str]] | None:
    print("[dev] Running preflight checks...")
    uv = shutil.which("uv")
    pnpm = shutil.which("pnpm")
    ok = True
    if not uv:
        fail("uv is not on PATH (install it manually; this script never installs tools)")
        ok = False
    if not pnpm:
        fail("pnpm is not on PATH (install it manually; this script never installs tools)")
        ok = False

    missing_models = [name for name in REQUIRED_MODELS if not (ROOT / name).is_file()]
    if missing_models:
        fail("required model files are missing:\n  - " + "\n  - ".join(missing_models))
        ok = False

    env = os.environ.copy()
    dotenv_values = load_dotenv_values(ROOT / ".env")
    for key, value in dotenv_values.items():
        env.setdefault(key, value)

    api_port = int(env.get("PERSONAWEAVE_API_PORT", API_PORT))
    web_port = int(env.get("PERSONAWEAVE_WEB_PORT", WEB_PORT))

    for port in (api_port, web_port):
        if not port_available(port):
            fail(f"port {port} is already in use")
            ok = False

    config_path = Path(env.get("PERSONAWEAVE_AI_CONFIG", "config/ai_gateway.yaml"))
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    if not config_path.is_file():
        fail(f"AI config does not exist: {config_path}")
        ok = False
    else:
        referenced = sorted(set(ENV_REFERENCE.findall(config_path.read_text(encoding="utf-8"))))
        missing_env = [name for name in referenced if not env.get(name)]
        if missing_env:
            print(
                "[dev] WARNING: AI features are disabled until these variables are set: " + ", ".join(missing_env),
                file=sys.stderr,
            )
        else:
            print(f"[dev] AI config environment: {', '.join(referenced) or 'no references'}")

    if uv and not check_providers(uv, env):
        ok = False
    if not ok or not uv or not pnpm:
        return None
    print("[dev] Preflight passed.")
    return uv, pnpm, env


def process_options() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def stop_process(process: subprocess.Popen[bytes], name: str) -> None:
    if process.poll() is not None:
        return
    print(f"[dev] Stopping {name}...")
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main() -> int:
    checked = preflight()
    if checked is None:
        return 1
    uv, pnpm, env = checked
    api_port = int(env.get("PERSONAWEAVE_API_PORT", API_PORT))
    web_port = int(env.get("PERSONAWEAVE_WEB_PORT", WEB_PORT))
    env["PERSONAWEAVE_API_PORT"] = str(api_port)
    env["PERSONAWEAVE_WEB_PORT"] = str(web_port)
    processes: list[tuple[str, subprocess.Popen[bytes]]] = []
    try:
        print(f"[dev] API: http://localhost:{api_port}")
        api = subprocess.Popen(
            [uv, "run", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", str(api_port)],
            cwd=ROOT,
            env=env,
            **process_options(),
        )
        processes.append(("API", api))
        print(f"[dev] Web: http://localhost:{web_port}")
        web = subprocess.Popen(
            [pnpm, "dev", "--host", "0.0.0.0", "--port", str(web_port), "--strictPort"],
            cwd=WEB_DIR,
            env=env,
            **process_options(),
        )
        processes.append(("Web", web))
        while True:
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    print(f"[dev] {name} exited with status {code}", file=sys.stderr)
                    return code or 1
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("\n[dev] Ctrl+C received. Shutting down...")
        return 0
    finally:
        for name, process in reversed(processes):
            stop_process(process, name)


if __name__ == "__main__":
    raise SystemExit(main())
