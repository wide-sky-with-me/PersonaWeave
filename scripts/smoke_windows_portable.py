"""Non-production native Windows smoke test; use a disposable portable copy, never a user's data."""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path


def verify(root, report_path, gpu=False, startup_timeout=180):
    """Exercise real inference and persisted editor media across a backend restart."""
    if os.name != "nt":
        raise RuntimeError("Run this test with the shipped Windows Python on Windows")
    executable = root / "runtime/python.exe"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    report = {"platform": "Windows x64", "checks": []}
    process = None
    url = ""
    report_path.parent.mkdir(parents=True, exist_ok=True)

    def request(path, body=None, method=None, raw=False):
        payload = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            url + path, data=payload, method=method, headers={"Content-Type": "application/json"}
        )
        with opener.open(req, timeout=300) as response:
            data = response.read()
            return data if raw else json.loads(data)

    def launch(log):
        nonlocal process, url
        state = root / "data/portable-runtime.json"
        state.unlink(missing_ok=True)
        process = subprocess.Popen(
            [str(executable), "-u", "-X", "utf8", "-m", "api.portable", "--no-browser", "--port", "0"],
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        started = time.monotonic()
        deadline = started + startup_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Portable server exited: {process.returncode}")
            try:
                url = json.loads(state.read_text(encoding="utf-8"))["url"]
                if request("/api/v1/health")["pipeline_ready"]:
                    report.setdefault("startup_seconds", []).append(round(time.monotonic() - started, 2))
                    return
            except (OSError, ValueError):
                time.sleep(1)
        raise TimeoutError("Portable server did not become ready")

    def uri(relative):
        return "data:image/png;base64," + base64.b64encode((root / relative).read_bytes()).decode()

    with report_path.with_suffix(".log").open("w", encoding="utf-8") as log:
        try:
            launch(log)
            print("WINDOWS_READY", url, flush=True)
            health = request("/api/v1/health")
            assert health["gpu_available"] is gpu
            assert health["providers"] == (
                ["CUDAExecutionProvider", "CPUExecutionProvider"] if gpu else ["CPUExecutionProvider"]
            )
            report["health"] = health
            initial_projects = request("/api/v1/projects")["data"]
            assert b"<html" in request("/", raw=True).lower()
            samples = request("/api/v1/samples")
            assert len(samples["sources"]) == 2 and len(samples["targets"]) == 2
            for sample in samples["sources"] + samples["targets"]:
                assert len(request(sample["url"], raw=True)) > 100
            report["checks"].extend(["GPU startup" if gpu else "CPU startup", "built frontend", "sample downloads"])
            second = subprocess.run(
                [str(executable), "-X", "utf8", "-m", "api.portable", "--no-browser"],
                cwd=root,
                capture_output=True,
                timeout=15,
            )
            assert second.returncode == 0 and process.poll() is None
            report["checks"].append("single-instance guard")
            started = time.monotonic()
            swapped = request(
                "/api/v1/swap-sync",
                {
                    "source_manual_images": [uri("data/test_target/01.png")],
                    "target_image_url": uri("data/test_images/group-01.png"),
                    "person_index": 1,
                    "parameters": {"similarity_threshold": 0.38, "swapper_model": "auto"},
                    "return_base64": True,
                },
            )["data"]
            assert swapped["faces_detected"] >= 1 and swapped["faces_swapped"] >= 1
            image = base64.b64decode(swapped["processed_base64"].split(",", 1)[1])
            assert len(image) > 1000
            report["swap"] = {
                "seconds": round(time.monotonic() - started, 2),
                "faces_detected": swapped["faces_detected"],
                "faces_swapped": swapped["faces_swapped"],
                "sha256": hashlib.sha256(image).hexdigest(),
            }
            report_path.with_suffix(".jpg").write_bytes(image)
            print("WINDOWS_SWAP_OK", report["swap"], flush=True)
            if gpu:
                # Windows PowerShell otherwise emits the active OEM code page into this pipe.
                # Force UTF-8 so Chinese installation paths survive the DLL-origin audit.
                command = (
                    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
                    f"(Get-Process -Id {process.pid}).Modules | "
                    "Where-Object { $_.ModuleName -match 'cudnn|cublas|cufft|cudart|curand|nvrtc|nvJit' } | "
                    "Select-Object ModuleName,FileName | ConvertTo-Json -Compress"
                )
                libraries = json.loads(
                    subprocess.check_output(
                        [
                            str(Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
                            "-NoProfile",
                            "-Command",
                            command,
                        ],
                        encoding="utf-8",
                        timeout=30,
                    )
                )
                assert len(libraries) >= 4 and all(Path(item["FileName"]).is_relative_to(root) for item in libraries)
                report["gpu_libraries"] = libraries
                report["checks"].append("CUDA DLLs loaded exclusively from portable copy")
            project = request("/api/v1/projects", {}, "POST")["data"]
            endpoint = "/api/v1/projects/" + project["id"]
            source = request(endpoint + "/assets", {"image": uri("data/test_images/group-01.png")})["data"][0]
            result = request(endpoint + "/assets", {"image": swapped["processed_base64"]})["data"][0]
            document = {
                "schema": 1,
                "studio": {"targetImages": [{"id": "portable-test", "url": source, "processedUrl": result}]},
                "workbench": {"view": "prepare"},
                "warnings": [],
            }
            saved = request(endpoint, {"expected_revision": 0, "title": "Windows smoke", "document": document}, "PUT")[
                "data"
            ]
            # Force process termination only after completed requests to verify durable recovery.
            process.terminate()
            process.wait(timeout=15)
            launch(log)
            restored = request(endpoint + "/restore", {}, "POST")["data"]
            assert restored["document"] == document
            assert request(endpoint + "/assets/" + result["$asset"], raw=True) == image
            request(endpoint + f"?expected_revision={saved['revision']}", method="DELETE", raw=True)
            assert request("/api/v1/projects")["data"] == initial_projects
            report["checks"].extend(
                [
                    "real local face swap",
                    "project and media survive restart",
                    "exact result download",
                    "history deletion",
                ]
            )
            report["passed"] = True
            print("WINDOWS_SMOKE_PASSED", flush=True)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--gpu", action="store_true", help="Require CUDA instead of accepting a CPU fallback")
    parser.add_argument("--startup-timeout", type=int, default=180, help="Seconds allowed for each cold model startup")
    args = parser.parse_args()
    verify(args.root.resolve(), args.report.resolve(), args.gpu, args.startup_timeout)
