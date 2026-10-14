"""Validate versioned Wiki navigation without network, credentials, or runtime data."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from scripts.documentation_links import REPOSITORY, heading_anchors, local_link, markdown_links

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "wiki"


def test_all_wiki_topics_are_reachable_in_source_and_published_sidebar():
    pages = sorted(WIKI.glob("*.md"))
    assert {"Home.md", "_Sidebar.md", "_Footer.md"} <= {page.name for page in pages}
    home = (WIKI / "Home.md").read_text(encoding="utf-8")
    sidebar = (WIKI / "_Sidebar.md").read_text(encoding="utf-8")
    for page in pages:
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*\.md|_(?:Sidebar|Footer)\.md", page.name)
        text = page.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert len(text) <= 6_000
        if page.name.startswith("_"):
            continue
        assert f"{REPOSITORY}/wiki/{page.stem}" in sidebar
        if page.name != "Home.md":
            assert f"{REPOSITORY}/blob/main/wiki/{page.name}" in home
            assert f"{REPOSITORY}/blob/main/wiki/Home.md" in text


def test_wiki_repository_links_and_heading_fragments_resolve_locally():
    """Check absolute GitHub links against this checkout, not transient network access."""
    checked = 0
    for page in WIKI.glob("*.md"):
        for raw_target in markdown_links(page.read_text(encoding="utf-8")):
            url = urlsplit(raw_target)
            assert url.scheme == "https" and url.netloc == "github.com", (page.name, raw_target)
            link = local_link(ROOT, page, raw_target)
            assert link is not None, (page.name, raw_target)
            resolved, fragment = link
            assert resolved.is_relative_to(ROOT) and resolved.exists(), (page.name, raw_target)
            repository_path = urlsplit(REPOSITORY).path
            if url.path == repository_path or url.path.startswith(f"{repository_path}/tree/main/"):
                assert resolved.is_dir(), (page.name, raw_target)
            else:
                assert resolved.is_file(), (page.name, raw_target)
            if fragment:
                assert resolved.suffix == ".md"
                assert unquote(url.fragment) in heading_anchors(resolved.read_text(encoding="utf-8")), (
                    page.name,
                    raw_target,
                )
            checked += 1
    assert checked > 0


def test_each_wiki_topic_links_to_canonical_documentation():
    for page in WIKI.glob("*.md"):
        if page.name.startswith("_"):
            continue
        links = markdown_links(page.read_text(encoding="utf-8"))
        assert any(link.startswith(f"{REPOSITORY}/blob/main/docs/") for link in links), page.name


def test_readme_exposes_wiki_source_and_publication_contract():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "wiki/Home.md" in readme
    assert "docs/wiki-publication.md" in readme
    publication = (ROOT / "docs/wiki-publication.md").read_text(encoding="utf-8")
    assert "test_wiki_documentation.py" in publication
    assert "独立 Wiki 发布成功" in publication
