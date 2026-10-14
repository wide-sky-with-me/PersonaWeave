"""Keep the reader landing page complete without copying runtime contracts."""

from pathlib import Path

from scripts.documentation_links import local_link, markdown_links

ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "docs/README.md"


def linked_files(document: Path) -> set[Path]:
    """Resolve actual links rather than incidental filename mentions."""
    links = (local_link(ROOT, document, target) for target in markdown_links(document.read_text(encoding="utf-8")))
    return {link[0] for link in links if link is not None and link[0].is_file()}


def test_reader_landing_directly_lists_every_current_topic():
    current = {
        path
        for path in (ROOT / "docs").glob("*.md")
        if path != LANDING and "validation" not in path.name and "live-test" not in path.name
    }
    missing = current - linked_files(LANDING)
    assert not missing, f"Add these current topics to the reader landing: {missing}"


def test_reader_landing_is_exposed_by_user_and_contributor_entries():
    for name in ("README.md", "DESIGN.md", "CONTRIBUTING.md", "SECURITY.md"):
        assert LANDING in linked_files(ROOT / name), f"{name}: link the reader landing"


def test_reader_landing_links_back_to_user_and_maintenance_entries():
    expected = {ROOT / name for name in ("README.md", "DESIGN.md", "CONTRIBUTING.md", "AGENTS.md", "wiki/Home.md")}
    assert expected <= linked_files(LANDING)
