"""Validate versioned Wiki navigation without network, credentials, or runtime data."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "wiki"
REPOSITORY = "https://github.com/wide-sky-with-me/PersonaWeave"
LINKS = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _anchors(text: str) -> set[str]:
    """Match GitHub heading slugs, including repeated headings."""
    anchors = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        anchor, suffix = slug, 0
        while anchor in anchors:
            suffix += 1
            anchor = f"{slug}-{suffix}"
        anchors.add(anchor)
    return anchors


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
    repository_path = urlsplit(REPOSITORY).path
    checked = 0
    for page in WIKI.glob("*.md"):
        for raw_target in LINKS.findall(page.read_text(encoding="utf-8")):
            url = urlsplit(raw_target)
            assert url.scheme == "https" and url.netloc == "github.com", (page.name, raw_target)
            path = unquote(url.path)
            if path == repository_path:
                resolved = ROOT
            elif path.startswith(f"{repository_path}/blob/main/"):
                resolved = ROOT / path.removeprefix(f"{repository_path}/blob/main/")
                assert resolved.is_file(), (page.name, raw_target)
            elif path.startswith(f"{repository_path}/tree/main/"):
                resolved = ROOT / path.removeprefix(f"{repository_path}/tree/main/")
                assert resolved.is_dir(), (page.name, raw_target)
            else:
                assert path.startswith(f"{repository_path}/wiki/"), (page.name, raw_target)
                resolved = WIKI / (path.removeprefix(f"{repository_path}/wiki/") + ".md")
                assert resolved.is_file(), (page.name, raw_target)
            assert resolved.resolve().is_relative_to(ROOT)
            if url.fragment:
                assert resolved.suffix == ".md"
                assert unquote(url.fragment) in _anchors(resolved.read_text(encoding="utf-8")), (
                    page.name,
                    raw_target,
                )
            checked += 1
    assert checked > 0


def test_each_wiki_topic_links_to_canonical_documentation():
    for page in WIKI.glob("*.md"):
        if page.name.startswith("_"):
            continue
        links = LINKS.findall(page.read_text(encoding="utf-8"))
        assert any(link.startswith(f"{REPOSITORY}/blob/main/docs/") for link in links), page.name


def test_readme_exposes_wiki_source_and_publication_contract():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "wiki/Home.md" in readme
    assert "docs/wiki-publication.md" in readme
    publication = (ROOT / "docs/wiki-publication.md").read_text(encoding="utf-8")
    assert "test_wiki_documentation.py" in publication
    assert "独立 Wiki 发布成功" in publication
