"""Guard public collaboration metadata and clean-checkout CI prerequisites."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_MARKDOWN = [
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / ".github/PULL_REQUEST_TEMPLATE.md",
    ROOT / "docs/public-project.md",
]


def test_public_collaboration_documents_are_reachable():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for path in PUBLIC_MARKDOWN[:2] + PUBLIC_MARKDOWN[3:]:
        assert path.name in readme
    assert "Community.md" in (ROOT / "wiki/Home.md").read_text(encoding="utf-8")
    assert "/wiki/Community" in (ROOT / "wiki/_Sidebar.md").read_text(encoding="utf-8")
    public = (ROOT / "docs/public-project.md").read_text(encoding="utf-8")
    assert "源码许可尚未指定" in public
    assert "models/README.md" in public and "tests/fixtures/README.md" in public
    assert not any((ROOT / name).exists() for name in ("LICENSE", "LICENSE.md", "LICENSE.txt"))


def test_public_markdown_relative_links_and_sections_exist():
    for document in PUBLIC_MARKDOWN:
        for raw in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            url = urlsplit(raw.strip().strip("<>"))
            if url.scheme:
                continue
            resolved = (document.parent / unquote(url.path)).resolve() if url.path else document
            assert resolved.is_file(), (document.name, raw)
            if url.fragment:
                headings = re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", resolved.read_text(encoding="utf-8"), re.M)
                anchors = {re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-") for heading in headings}
                assert unquote(url.fragment) in anchors, (document.name, raw)


def test_current_docs_do_not_depend_on_git_ignored_runtime_links():
    """Local runtime folders must not hide documentation gaps in a developer checkout."""
    documents = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), *PUBLIC_MARKDOWN]
    for document in documents:
        for raw in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            url = urlsplit(raw.strip().strip("<>"))
            if url.scheme or not url.path:
                continue
            resolved = (document.parent / unquote(url.path)).resolve()
            assert resolved.is_relative_to(ROOT), (document.name, raw)
            assert resolved.relative_to(ROOT).parts[0] not in {"data", "outputs", "dist", ".venv"}, (
                document.name,
                raw,
            )


@pytest.mark.parametrize("filename", ["bug_report.yml", "feature_request.yml"])
def test_issue_forms_have_unique_fields_and_required_context(filename):
    config = yaml.safe_load((ROOT / ".github/ISSUE_TEMPLATE" / filename).read_text(encoding="utf-8"))
    assert config["name"] and config["description"]
    fields = [item for item in config["body"] if item["type"] != "markdown"]
    ids = [item["id"] for item in fields]
    assert len(ids) == len(set(ids))
    assert all(item["attributes"].get("label") for item in fields)
    required = {item["id"] for item in fields if item.get("validations", {}).get("required")}
    expected = {"version", "installation", "environment", "reproduction", "expected", "actual"}
    assert (expected if filename == "bug_report.yml" else {"problem", "proposal"}) <= required
    if filename == "bug_report.yml":
        privacy = next(item for item in fields if item["id"] == "privacy")
        assert privacy["attributes"]["options"][0]["required"] is True


def test_ci_prepares_real_models_before_api_regressions():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    backend = workflow["jobs"]["backend"]["steps"]
    runs = [step.get("run", "") for step in backend]
    prepare = runs.index("uv run python scripts/download_models.py")
    regression = runs.index("uv run pytest -q")
    assert prepare < regression
    cache = next(step for step in backend if step.get("uses") == "actions/cache@v4")
    assert cache["with"]["path"] == "models"
    assert "scripts/download_models.py" in cache["with"]["key"]
    frontend = workflow["jobs"]["frontend"]["steps"]
    assert any(step.get("run") == "pnpm lint" for step in frontend)


def test_public_fixture_images_have_no_embedded_private_metadata():
    for filename in ["source-01.png", "source-02.png", "group-01.png", "group-02.png"]:
        with Image.open(ROOT / "tests/fixtures" / filename) as image:
            assert not image.getexif(), filename
            assert not image.info, filename
