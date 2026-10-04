"""Keep evidence documentation aligned with the retained read-only history contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_evidence_archive_is_documented_as_separate_from_publication():
    quality = (ROOT / "docs/delivery-quality.md").read_text()
    assert "outputs/refinement_evidence/{task_id}/" in quality
    assert "只读证据，不是已发布输出" in quality
    assert "已验证的暂存字节，不能用档案内容替代" in quality
    assert "delivery-quality.md" in (ROOT / "docs/ai-refinement.md").read_text()
    assert "null" in quality and "expected_revision" in quality and "candidate_digest" in quality
    for name in ("README.md", "DESIGN.md", "CHANGELOG.md"):
        assert "docs/delivery-quality.md" in (ROOT / name).read_text()
