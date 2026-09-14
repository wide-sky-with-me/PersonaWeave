"""Keep evidence documentation aligned with the retained read-only history contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_evidence_archive_is_documented_as_separate_from_publication():
    authority = (ROOT / "docs/ai-refinement.md").read_text()
    assert "outputs/refinement_evidence/{task_id}/" in authority
    assert "read-only evidence, not published outputs" in authority
    assert "staged bytes, never an archive substitute" in authority
    quality = (ROOT / "docs/delivery-quality.md").read_text()
    assert "null" in quality and "expected_revision" in quality and "candidate_digest" in quality
    for name in ("README.md", "DESIGN.md", "CHANGELOG.md"):
        assert "docs/delivery-quality.md" in (ROOT / name).read_text()
