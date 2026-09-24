"""Build small source and app-local VC runtime repairs for Windows; no model calls."""

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_windows_portable import MSVC_FILES, check_public_asset, sha256  # noqa: E402

LAUNCHER = '''"""Apply only the patch for this installation's declared edition."""
import json
import sys
from pathlib import Path

payload = Path(__file__).resolve().parent
root = payload.parent
sys.path.insert(0, str(payload))
from engine import apply_update

try:
    edition = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["edition"]
    if edition not in {"windows-x64-cpu", "windows-x64-gpu"}:
        raise ValueError("不支持此安装版本")
    apply_update(root, payload / edition, label="程序修复")
except (OSError, ValueError, RuntimeError, KeyError) as exc:
    print(f"修复未完成：{exc}")
    raise SystemExit(1) from exc
'''

INSTRUCTIONS = """PersonaWeave Windows CPU/GPU 程序修复包

适用：原始交付的 Windows CPU/GPU 完整包（安装器核对具体版本摘要）。
包含：中文路径图片读写、素材库预览图工程归档、AI 本地路由错误分类修复。
若提供 Repair-Runtime.cmd，还包含同版本的五个 Microsoft VC++ 运行库 DLL。
不包含：Python、模型权重、GPU 驱动、素材库、AI Key、个人图片和历史。

已打过补丁或不确定版本：先双击 Check-Install.cmd，提供生成的 install-check.json。
自检不修改程序/工程，只写脱敏报告并临时验证图片归档，不调用 AI。
若原来是从已修复的 E 盘目录复制，请关闭后端后直接运行 Repair-Runtime.cmd。
它支持已有本地源码补丁，补齐 VC++ DLL、备份原 DLL 并更新清单，不需要开发环境。
原始未打补丁的发行包应先执行 Apply-Fix.cmd，再执行 Repair-Runtime.cmd。

1. 若当前网页有未保存修改，先保留网页，不要刷新。
2. 在程序窗口按 Ctrl+C 停止后端；已发出的 AI 请求不能保证退款。
3. 将本压缩包解压到程序目录，让 Apply-Fix.cmd 与 Start.cmd 位于同一层。
4. 双击 Apply-Fix.cmd，等待显示“程序修复完成”。版本不匹配时停止，不强行覆盖。
5. 双击 Start.cmd；回到原网页点“保存工程”，确认已保存后再刷新。

程序配置、个人图片、工程和任务历史保留；不执行生图或换脸。
若仍有 422，请提供控制台对应 POST/PUT /api/v1/... 及状态码那一行。
不要提供 API Key。此补丁不证明所有 422 都由旧版本造成。
素材库需要单独提供合法素材；空白原始便携包只带四张示例图。
"""


def build(bases, output, *, source=ROOT, msvc_dir=None):
    """Bundle changed application bytes, preserving each edition's runtime and model manifest."""
    msvc_hashes = {name: sha256(msvc_dir / name) for name in MSVC_FILES} if msvc_dir is not None else None
    files = {
        str(p.relative_to(source)): p for folder in ("api", "core", "utils") for p in (source / folder).rglob("*.py")
    }
    files.update(
        {
            "README.md": source / "docs/windows-portable.md",
            "docs/windows-portable.md": source / "docs/windows-portable.md",
        }
    )
    editions = set()
    prepared = []
    for base in bases:
        original = (base / "manifest.json").read_bytes()
        old = json.loads(original)
        edition = old["edition"]
        if edition not in {"windows-x64-cpu", "windows-x64-gpu"} or edition in editions:
            raise ValueError("Expected distinct Windows CPU/GPU delivery manifests")
        editions.add(edition)
        new = {**old, "files": dict(old["files"])}
        changed = {}
        for name, path in files.items():
            check_public_asset(name)
            checksum = sha256(path)
            if old["files"].get(name) == checksum:
                continue
            installed = base / name
            if name in old["files"]:
                if not installed.is_file() or sha256(installed) != old["files"][name]:
                    raise ValueError(f"Base file differs from its delivery manifest: {name}")
            elif installed.exists():
                raise ValueError(f"Untracked application file in base: {name}")
            changed[name] = path
            new["files"][name] = checksum
        encoded = json.dumps(new, indent=2).encode()
        update = {
            "base_manifest_sha256": hashlib.sha256(original).hexdigest(),
            "new_manifest_sha256": hashlib.sha256(encoded).hexdigest(),
            "base_files": old["files"],
        }
        prepared.append((edition, encoded, update, changed))
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        package.writestr("windows-fix/apply.py", LAUNCHER)
        package.write(ROOT / "scripts/upgrade_windows_gpu.py", "windows-fix/engine.py")
        package.write(ROOT / "scripts/check_windows_portable.py", "windows-fix/check.py")
        if msvc_dir is not None:
            package.writestr("windows-fix/vc-runtime.json", json.dumps(msvc_hashes))
            for name in MSVC_FILES:
                package.write(msvc_dir / name, "windows-fix/vc-runtime/" + name)
            package.writestr(
                "windows-fix/repair-runtime.ps1",
                (ROOT / "scripts/repair_windows_runtime.ps1").read_text().encode("utf-8-sig"),
            )
            package.writestr(
                "Repair-Runtime.cmd",
                b'@echo off\r\nchcp 65001 >nul\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows-fix\\repair-runtime.ps1"\r\npause\r\n',
            )
        package.writestr("修复使用说明.txt", INSTRUCTIONS.encode("utf-8-sig"))
        package.writestr(
            "Check-Install.cmd",
            (
                b"@echo off\r\nchcp 65001 >nul\r\n"
                b'"%~dp0runtime\\python.exe" -X utf8 "%~dp0windows-fix\\check.py"\r\npause\r\n'
            ),
        )
        package.writestr(
            "Apply-Fix.cmd",
            (
                "@echo off\r\nchcp 65001 >nul\r\n"
                'if not exist "%~dp0runtime\\python.exe" (\r\n'
                "echo 请解压到程序目录，与 Start.cmd 放在一起。\r\npause\r\nexit /b 1\r\n)\r\n"
                '"%~dp0runtime\\python.exe" -X utf8 "%~dp0windows-fix\\apply.py"\r\npause\r\n'
            ).encode(),
        )
        for edition, encoded, update, changed in prepared:
            prefix = f"windows-fix/{edition}/"
            package.writestr(prefix + "manifest.json", encoded)
            package.writestr(prefix + "update.json", json.dumps(update))
            for name, path in sorted(changed.items()):
                package.write(path, prefix + "files/" + name)
    output.with_suffix(output.suffix + ".sha256").write_text(sha256(output) + "  " + output.name + "\n")
    return {edition: sorted(changed) for edition, _, _, changed in prepared}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu", type=Path, default=ROOT / "dist/PersonaWeave-Windows-x64")
    parser.add_argument("--gpu", type=Path, default=ROOT / "dist/PersonaWeave-Windows-GPU-x64")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--msvc-dir", type=Path, help="Verified matching Microsoft VC runtime DLLs")
    args = parser.parse_args()
    print(json.dumps(build([args.cpu, args.gpu], args.output, msvc_dir=args.msvc_dir), ensure_ascii=False))
