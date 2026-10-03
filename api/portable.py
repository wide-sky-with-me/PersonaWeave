"""Single-process portable entry point: private runtime, local browser and isolated application data."""

import argparse
import contextlib
import getpass
import json
import os
import socket
import sys
import tempfile
import threading
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_writable_directories(root):
    """Check actual write access before opening persistent state; do not rely on permission bits."""
    for directory in (root / "data", root / "outputs"):
        try:
            directory.mkdir(exist_ok=True)
            with tempfile.TemporaryFile(dir=directory):
                pass
        except OSError as exc:
            raise OSError("程序目录无法写入，请把整个文件夹移到本地可写目录（例如 D:\\PW），再启动。") from exc


def configure(root):
    """Save explicitly entered AI settings locally; never display or bundle a user's keys."""
    from dotenv import dotenv_values

    path = root / ".env"
    values = dict(dotenv_values(path)) if path.exists() else {}
    print("配置视觉分析和图像编辑服务。留空保留原值；配置后重启工作台。")
    for prefix, title in (("AI_VISION", "视觉分析"), ("AI_IMAGE", "图像编辑")):
        print(f"\n{title}：")
        for suffix, label in (
            ("MODEL", "模型（含协议前缀，OpenAI 兼容服务填写 openai/实际模型名）"),
            ("API_BASE", "API 地址"),
            ("API_KEY", "API Key"),
        ):
            key = f"{prefix}_{suffix}"
            value = getpass.getpass(label + "：") if suffix == "API_KEY" else input(label + "：")
            if any(char in value for char in "\r\n\x00"):
                raise ValueError("配置不能包含换行或空字符")
            if value.strip():
                values[key] = value.strip()
    text = "\n".join(f"{key}={json.dumps(value or '', ensure_ascii=False)}" for key, value in values.items()) + "\n"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    print("配置已保存在本机 .env。关闭旧窗口后双击 Start.cmd 生效。")


@contextlib.contextmanager
def instance_lock(root):
    """Prevent a second backend from opening/recovering the same SQLite task state."""
    directory = root / "data"
    directory.mkdir(exist_ok=True)
    with (directory / "portable.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def open_when_ready(url, stopped):
    """Wait for model loading to finish before opening the default browser."""
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while not stopped.wait(0.5):
        try:
            with client.open(url + "/api/v1/health", timeout=2) as response:
                if json.load(response).get("pipeline_ready"):
                    webbrowser.open(url)
                    return
        except (OSError, ValueError):
            continue


def main():
    """Start the shipped app on loopback; no installation, model downloads or automatic AI calls."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configure", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    os.chdir(ROOT)
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["PERSONAWEAVE_AI_CONFIG"] = str(ROOT / "config/ai_gateway.yaml")
    if args.configure:
        configure(ROOT)
        return 0
    check_writable_directories(ROOT)
    with instance_lock(ROOT) as acquired:
        if not acquired:
            state = ROOT / "data/portable-runtime.json"
            if state.exists() and not args.no_browser:
                url = json.loads(state.read_text(encoding="utf-8")).get("url", "")
                if url.startswith("http://127.0.0.1:"):
                    webbrowser.open(url)
            print("工作台已在运行，请使用现有窗口。")
            return 0
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("127.0.0.1", args.port))
            except OSError:
                listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            url = f"http://127.0.0.1:{port}"
            (ROOT / "data/portable-runtime.json").write_text(
                json.dumps({"url": url, "pid": os.getpid()}), encoding="utf-8"
            )
            manifest_path = ROOT / "manifest.json"
            edition = (
                json.loads(manifest_path.read_text(encoding="utf-8")).get("edition", "")
                if manifest_path.exists()
                else ""
            )
            label = "NVIDIA GPU" if edition == "windows-x64-gpu" else "CPU"
            print(f"PersonaWeave Windows {label} 便携版\n正在加载模型，工作台地址：{url}", flush=True)
            print("关闭程序请在此窗口按 Ctrl+C。AI 服务首次使用请运行 Configure-AI.cmd。", flush=True)
            print("首次加载可能较慢，请保留窗口；模型就绪后自动打开浏览器。故障可运行 Check-Install.cmd。", flush=True)
            stopped = threading.Event()
            if not args.no_browser:
                threading.Thread(target=open_when_ready, args=(url, stopped), daemon=True).start()
            try:
                import uvicorn

                # Browser SSE subscriptions can otherwise keep shutdown waiting forever.
                # After the HTTP grace period, lifespan still drains native/AI workers.
                server = uvicorn.Server(
                    uvicorn.Config(
                        "api.app:app", host="127.0.0.1", port=port, use_colors=False, timeout_graceful_shutdown=10
                    )
                )
                server.run(sockets=[listener])
            finally:
                stopped.set()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"启动失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
