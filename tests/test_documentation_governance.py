"""Mechanical checks for the documentation layering rules in docs/documentation-governance.md."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / ".agents/notes"

DEFAULT_CAP = 12_000
# Legacy over-limit files keep a ratchet: the cap may be lowered, never raised. Split or compress instead.
CAPS = {
    "AGENTS.md": 9_000,
    "README.md": 6_500,
    "DESIGN.md": 6_000,
    "docs/ai-refinement.md": 13_000,
    "docs/refinement-prompts.md": 12_500,
}
NOTE_CAP = 6_000
HISTORY_WORDS = ("此前", "以前", "原先", "曾经", "现已", "如今", "不再", "previously", "formerly", "no longer")
NOTE_HEADINGS = ("## 背景", "## 决策", "## 被放弃的方案", "## 影响与验证")


def documents():
    names = ["AGENTS.md", "README.md", "DESIGN.md", "models/README.md"]
    return [ROOT / name for name in names] + sorted((ROOT / "docs").glob("*.md"))


def relative(path):
    return path.relative_to(ROOT).as_posix()


def is_fact_document(path):
    """Fact documents describe the current system; rules, evidence records and history are exempt."""
    name = relative(path)
    return not (
        name in {"AGENTS.md", "docs/documentation-governance.md"} or "validation" in name or "live-test" in name
    )


def notes():
    return sorted(path for path in NOTES.rglob("*.md") if path.name != "README.md")


def test_documents_stay_under_their_length_cap():
    too_long = {
        relative(path): len(path.read_text(encoding="utf-8"))
        for path in documents()
        if len(path.read_text(encoding="utf-8")) > CAPS.get(relative(path), DEFAULT_CAP)
    }
    assert not too_long, f"Split or compress instead of raising the cap: {too_long}"


def test_length_ratchet_has_no_slack_for_documents_that_became_small():
    slack = {
        name: cap
        for name, cap in CAPS.items()
        if len((ROOT / name).read_text(encoding="utf-8")) < cap * 0.6 and cap > DEFAULT_CAP * 0.6
    }
    assert not slack, f"Lower these caps: {slack}"


def test_fact_documents_do_not_narrate_history():
    offenders = {}
    for path in filter(is_fact_document, documents()):
        text = path.read_text(encoding="utf-8")
        found = {word: text.count(word) for word in HISTORY_WORDS if word in text}
        if found:
            offenders[relative(path)] = found
    assert not offenders, f"Write current facts; put history in CHANGELOG.md or a decision note: {offenders}"


def test_decision_notes_have_required_structure_and_no_global_index():
    assert notes(), "Decision notes must exist"
    for path in notes():
        text = path.read_text(encoding="utf-8")
        name = relative(path)
        assert re.fullmatch(r"\d{8}-[a-z0-9-]+\.md", path.name), f"{name}: use YYYYMMDD-slug.md"
        assert len(text) <= NOTE_CAP, f"{name}: keep one decision per note under {NOTE_CAP} characters"
        assert re.search(r"(?m)^状态：(有效|被 \S+ 取代)$", text), f"{name}: missing 状态"
        date = re.search(r"(?m)^日期：(\d{4})-(\d{2})-(\d{2})$", text)
        assert date and "".join(date.groups()) == path.name[:8], f"{name}: 日期 must match the file name"
        positions = [text.find(heading) for heading in NOTE_HEADINGS]
        assert all(position >= 0 for position in positions) and positions == sorted(positions), f"{name}: headings"
    assert not [p for p in NOTES.rglob("*") if p.name.lower() in {"index.md", "index.json"}]


def test_decision_note_links_resolve():
    broken = []
    for path in [*notes(), NOTES / "README.md"]:
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", path.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (path.parent / target).resolve().exists():
                broken.append(f"{relative(path)} -> {target}")
    assert not broken, broken


def test_agent_rules_point_to_every_documentation_layer():
    rules = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for link in ("docs/documentation-governance.md", ".agents/notes/README.md", "CHANGELOG.md"):
        assert link in rules


def all_markdown_outside_notes():
    top_level = [ROOT / name for name in ("AGENTS.md", "README.md", "DESIGN.md", "CHANGELOG.md", "models/README.md")]
    return [*top_level, *sorted((ROOT / "docs").glob("*.md")), ROOT / "tests/fixtures/README.md"]


def test_every_document_is_reachable_from_another_document():
    """No orphan documents: each docs/*.md must be linked from some other Markdown file."""
    texts = {path: path.read_text(encoding="utf-8") for path in all_markdown_outside_notes()}
    orphans = [
        relative(doc)
        for doc in (ROOT / "docs").glob("*.md")
        if not any(doc.name in text for path, text in texts.items() if path != doc)
    ]
    assert not orphans, f"Link these from README.md, DESIGN.md, AGENTS.md or a related document: {orphans}"


def test_every_decision_note_is_discoverable_without_an_index():
    """Notes have no global index, so a document, the rules or another note must link each one."""
    texts = {path: path.read_text(encoding="utf-8") for path in [*all_markdown_outside_notes(), *notes()]}
    unlinked = [
        relative(note) for note in notes() if not any(note.name in text for path, text in texts.items() if path != note)
    ]
    assert not unlinked, f"Link each note from the document it explains: {unlinked}"
