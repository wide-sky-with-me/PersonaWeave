"""Regression tests for lightweight, side-effect-free package boundaries."""

import ast
import importlib
import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _loaded_modules_after(package: str) -> set[str]:
    script = f"import {package}, sys; print('\\n'.join(sorted(sys.modules)))"
    result = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)
    return set(result.stdout.splitlines())


def test_core_package_does_not_eagerly_load_inference_modules():
    loaded = _loaded_modules_after("core")

    assert "core.pipeline" not in loaded
    assert "core.detector" not in loaded


def test_api_package_does_not_eagerly_create_application():
    loaded = _loaded_modules_after("api")

    assert "api.app" not in loaded


@pytest.mark.parametrize(
    "package",
    ["api.routes", "api.projects", "api.tasks", "api.portraits", "api.refinement", "core.ai", "core.refinement"],
)
def test_domain_packages_have_no_runtime_side_effects(package):
    loaded = _loaded_modules_after(package)
    assert "api.app" not in loaded
    assert "api.refinement.service" not in loaded
    assert "api.refinement.store" not in loaded
    assert "api.refinement.legacy" not in loaded
    assert "core.pipeline" not in loaded
    assert "litellm" not in loaded


@pytest.mark.parametrize(
    "module",
    ["core.ai.configuration", "core.ai.types", "api.media", "api.tasks.provenance", "api.refinement.artifacts"],
)
def test_shared_boundaries_do_not_import_task_or_model_runtimes(module):
    loaded = _loaded_modules_after(module)
    assert "api.tasks.batch" not in loaded
    assert "api.refinement.service" not in loaded
    assert "api.refinement.store" not in loaded
    assert "api.refinement.legacy" not in loaded
    assert "core.pipeline" not in loaded
    assert "litellm" not in loaded


def _imported_modules(path: Path, node: ast.AST) -> list[str]:
    """Resolve relative imports and from-package aliases before enforcing layer boundaries."""
    if isinstance(node, ast.Import):
        return [item.name for item in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    package = ".".join(path.relative_to(ROOT).parent.parts)
    module = importlib.util.resolve_name("." * node.level + (node.module or ""), package)
    return [module, *(f"{module}.{item.name}" for item in node.names)]


@pytest.mark.parametrize(
    ("path", "statement", "expected"),
    [
        ("api/tasks/check.py", "from .. import app", "api.app"),
        ("api/tasks/check.py", "from ..routes import faces", "api.routes.faces"),
        ("api/tasks/check.py", "from api import lifecycle", "api.lifecycle"),
        ("core/ai/check.py", "from .. import detector", "core.detector"),
        ("core/ai/check.py", "import api.schemas", "api.schemas"),
    ],
)
def test_boundary_import_resolution(path, statement, expected):
    assert expected in _imported_modules(ROOT / path, ast.parse(statement).body[0])


def test_backend_dependencies_follow_domain_boundaries():
    """Block absolute, relative and from-package reverse imports across domain boundaries."""
    violations = []
    for root in (ROOT / "api", ROOT / "core"):
        for path in root.rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            forbidden = ("api",) if relative.startswith("core/") else ("api.app", "api.lifecycle", "api.routes")
            if relative == "api/app.py" or relative == "api/lifecycle.py" or relative.startswith("api/routes/"):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                for module in _imported_modules(path, node):
                    if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden):
                        violations.append(f"{relative}:{node.lineno} imports {module}")
    assert not violations, "\n".join(violations)


def test_cli_entrypoints_resolve_to_current_modules():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    for entrypoint in config["project"]["scripts"].values():
        module_name, function_name = entrypoint.split(":", 1)
        assert callable(getattr(importlib.import_module(module_name), function_name))


def test_production_image_io_does_not_pass_paths_to_opencv():
    """OpenCV filename APIs fail on Windows Unicode paths even when files exist."""
    violations = []
    for directory in ("api", "core", "utils"):
        for path in (ROOT / directory).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "cv2"
                    and node.func.attr in {"imread", "imwrite"}
                ):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not violations, "Use utils.image_io for Unicode-safe paths: " + ", ".join(violations)


def test_distribution_roots_preserve_supported_source_entrypoints():
    """Keep source, wheel and portable imports aligned while splitting domain internals."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    backend = config["tool"]["uv"]["build-backend"]
    assert backend["module-root"] == ""
    assert set(backend["module-name"]) == {"api", "core", "utils"}
    for package in backend["module-name"]:
        assert (ROOT / package / "__init__.py").is_file()
    assert {"cli.py", "config/ai_gateway.yaml"} <= set(backend["source-include"])
    assert (ROOT / "api" / "portable.py").is_file()
    assert config["project"]["scripts"] == {
        "personaweave": "api.cli:main",
        "personaweave-import-portraits": "api.portraits.importer:main",
    }
