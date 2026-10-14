"""Synthetic regressions for offline documentation links and rooted navigation."""

from pathlib import Path

import pytest

from scripts.documentation_links import (
    REPOSITORY,
    heading_anchors,
    link_errors,
    local_link,
    markdown_links,
    reachable_documents,
    repository_documents,
)


def test_heading_anchors_preserve_duplicates_and_ignore_code_examples():
    assert heading_anchors("# Heading\n## Heading\n```md\n# Fake\n```\n## 中文 `字段`\n") == {
        "heading",
        "heading-1",
        "中文-字段",
    }


def test_only_real_markdown_links_count_as_navigation():
    text = "mentioned.md\n[real](real.md)\n```md\n[fake](fake.md)\n```\n~~~\n[other](other.md)\n~~~\n"
    assert markdown_links(text) == ["real.md"]


def test_inline_code_comments_and_code_formatted_labels_are_distinct():
    text = "`[fake](fake.md)`\n<!-- [hidden](hidden.md) -->\n[`api.py`](api.py)\n"
    assert markdown_links(text) == ["api.py"]
    assert heading_anchors("<!--\n# Hidden\n-->\n# Visible\n") == {"visible"}


@pytest.mark.parametrize(
    ("raw", "relative", "fragment"),
    [
        ("../README.md?view=1#section", "README.md", "section"),
        ("<../some%20file.md#%E4%B8%AD%E6%96%87>", "some file.md", "中文"),
        ("#local", "docs/topic.md", "local"),
        (f"{REPOSITORY}/blob/main/DESIGN.md#module", "DESIGN.md", "module"),
        (f"{REPOSITORY}/tree/main/core", "core", ""),
        (f"{REPOSITORY}/wiki/Architecture", "wiki/Architecture.md", ""),
    ],
)
def test_repository_and_relative_links_share_resolution(tmp_path, raw, relative, fragment):
    assert local_link(tmp_path, tmp_path / "docs/topic.md", raw) == (tmp_path / relative, fragment)


def test_external_urls_are_not_fetched(tmp_path):
    assert local_link(tmp_path, tmp_path / "README.md", "https://example.test/missing.md") is None
    assert local_link(tmp_path, tmp_path / "README.md", "mailto:maintainer@example.test") is None
    assert local_link(tmp_path, tmp_path / "README.md", "//example.test/missing.md") is None


def test_broken_files_fragments_and_outside_links_have_distinct_errors(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    entry = root / "README.md"
    entry.write_text("[ok](topic.md#heading)\n[bad](topic.md#missing)\n[gone](missing.md)\n[out](../outside.md)\n")
    (root / "topic.md").write_text("# Heading\n")
    errors = link_errors(root, [entry])
    assert len(errors) == 3
    assert any("missing section" in error for error in errors)
    assert any("missing file" in error for error in errors)
    assert any("outside repository" in error for error in errors)


def test_rooted_navigation_excludes_mentions_history_and_disconnected_cycles(tmp_path):
    contents = {
        "README.md": "mention.md\n[guide](guide.md)\n[history](CHANGELOG.md)\n",
        "guide.md": "[evidence](docs/validation/record.md)\n",
        "docs/validation/record.md": "# Scoped evidence\n",
        "mention.md": "# Mentioned only\n",
        "CHANGELOG.md": "[historical](history-only.md)\n",
        "history-only.md": "# History only\n",
        "cycle-a.md": "[b](cycle-b.md)\n",
        "cycle-b.md": "[a](cycle-a.md)\n",
    }
    for name, text in contents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    seen = reachable_documents(tmp_path, [tmp_path / name for name in contents], [tmp_path / "README.md"])
    assert {path.relative_to(tmp_path).as_posix() for path in seen} == {
        "README.md",
        "guide.md",
        "docs/validation/record.md",
        "CHANGELOG.md",
    }


def test_document_inventory_includes_nested_evidence_but_not_runtime_trees(tmp_path):
    for name in ("docs/validation/record.md", "wiki/Home.md", ".agents/notes/platform/note.md", "data/private.md"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Document\n")
    documents = repository_documents(tmp_path, include_notes=True)
    assert tmp_path / "docs/validation/record.md" in documents
    assert tmp_path / ".agents/notes/platform/note.md" in documents
    assert not any(path.is_relative_to(tmp_path / "data") for path in documents)
    assert all(isinstance(path, Path) for path in documents)
