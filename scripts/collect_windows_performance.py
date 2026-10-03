"""Read-only portable performance evidence; no model loading, AI calls or image collection."""

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def memory_report():
    """Keep physical RAM and Windows commit headroom separate in the report."""
    if sys.platform != "win32":
        return {"status": "windows_only"}

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
            (name, ctypes.c_uint64)
            for name in (
                "physical_total",
                "physical_available",
                "commit_total",
                "commit_available",
                "virtual_total",
                "virtual_available",
                "extended",
            )
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {"status": "unavailable"}
    return {
        key + "_mib": round(getattr(status, key) / (1024 * 1024), 1)
        for key in ("physical_total", "physical_available", "commit_total", "commit_available")
    }


def read_json(client, url):
    """Bound localhost replies, including older servers with heavyweight task details."""
    with client.open(url, timeout=5) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Diagnostic response too large")
        return json.loads(raw)


def collect(root):
    """Collect hardware and active queue metadata; never read .env or image files."""
    report = {
        "ai_requests": 0,
        "model_inference_calls": 0,
        "cpu_logical_count": os.cpu_count(),
        "memory": memory_report(),
    }
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        report["build"] = {
            key: manifest.get(key) for key in ("edition", "built_at", "release", "program_hotfix", "frontend_hotfix")
        }
    except (OSError, ValueError) as exc:
        report["build_error"] = type(exc).__name__
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        try:
            result = subprocess.run(
                [
                    nvidia,
                    "--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=8,
                check=True,
            )
            report["nvidia_gpu"] = result.stdout.strip().splitlines()[:8]
        except (OSError, subprocess.SubprocessError) as exc:
            report["gpu_probe_error"] = type(exc).__name__
    else:
        report["nvidia_gpu"] = "nvidia-smi unavailable; this alone does not prove absence of a GPU"
    try:
        runtime = json.loads((root / "data/portable-runtime.json").read_text(encoding="utf-8"))
        parsed = urlsplit(runtime["url"])
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or not parsed.port
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid local runtime address")
        base = f"http://127.0.0.1:{parsed.port}"
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        health = read_json(client, base + "/api/v1/health")
        report["health"] = {
            key: health.get(key)
            for key in ("pipeline_ready", "gpu_available", "providers", "model_providers", "local_inference")
        }
        records = read_json(client, base + "/api/v1/task-history/batch-tasks?limit=100")["data"]
        report["active_batches"] = []
        for row in records:
            if row.get("status") not in ("QUEUED", "PROCESSING"):
                continue
            # Request IDs come from this server, but still enforce their route syntax.
            task_id = row["task_id"]
            if not task_id.startswith("pw_task_") or not task_id.removeprefix("pw_task_").isalnum():
                continue
            entry = {
                key: row.get(key)
                for key in ("task_id", "status", "created_at", "total_images", "processed_images", "progress")
            }
            try:
                detail = read_json(client, base + "/api/v1/batch-tasks/" + task_id)["data"]
                # Only application-owned memory/AI waiting reasons; never collect error bodies or results.
                reason = detail.get("waiting_reason")
                entry["waiting_reason"] = (
                    reason
                    if isinstance(reason, str) and len(reason) < 1000 and "data:" not in reason and "http" not in reason
                    else None
                )
            except (OSError, ValueError, KeyError) as exc:
                entry["detail_error"] = type(exc).__name__
            report["active_batches"].append(entry)
            if len(report["active_batches"]) >= 5:
                break
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["running_server_error"] = type(exc).__name__
    return report


def main():
    """Write a small report while the affected program is still queued."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    report = collect(root)
    (root / "performance-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已生成 performance-check.json。请在仍然排队时采集；报告不包含图片或 AI Key，不加载第二份模型。")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
