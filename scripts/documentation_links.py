"""Non-production, offline Markdown checks shared by documentation regressions.

Only repository documentation is enumerated; never import the application or read runtime data.

Decision: .agents/notes/platform/20261014-rooted-documentation-navigation.md
"""

import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPOSITORY = "https://github.com/wide-sky-with-me/PersonaWeave"
LINKS = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def markdown_body(text: str) -> str:
    """Remove fenced examples so code samples cannot count as navigation or headings."""
    body = []
    fence = ""
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if marker:
            token, suffix = marker.groups()
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not suffix.strip():
                fence = ""
        elif not fence:
            body.append(line)
    return re.sub(r"<!--.*?-->", "", "\n".join(body), flags=re.S)


def markdown_links(text: str) -> list[str]:
    """Return inline links, excluding examples but preserving code-formatted link labels."""
    body = markdown_body(text)
    code_spans = [(match.start(), match.end()) for match in re.finditer(r"(`+).*?\1", body, re.S)]
    return [
        match.group(1)
        for match in LINKS.finditer(body)
        if not any(start <= match.start() < end for start, end in code_spans)
    ]


def heading_anchors(text: str) -> set[str]:
    """Match the project's GitHub heading slugs, including repeated headings."""
    anchors = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", markdown_body(text), re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        anchor, suffix = slug, 0
        while anchor in anchors:
            suffix += 1
            anchor = f"{slug}-{suffix}"
        anchors.add(anchor)
    return anchors


def local_link(root: Path, document: Path, raw: str) -> tuple[Path, str] | None:
    """Resolve local and this repository's main/Wiki links without network access."""
    url = urlsplit(raw.strip().strip("<>"))
    repository = urlsplit(REPOSITORY)
    if url.scheme or url.netloc:
        if url.scheme != "https" or url.netloc != repository.netloc:
            return None
        prefix = repository.path
        if url.path.startswith((f"{prefix}/blob/main/", f"{prefix}/tree/main/")):
            target = root / unquote(url.path.split("/main/", 1)[1])
        elif url.path in {prefix, prefix + "/"}:
            target = root
        elif url.path == f"{prefix}/wiki":
            target = root / "wiki/Home.md"
        elif url.path.startswith(f"{prefix}/wiki/"):
            target = root / "wiki" / (unquote(url.path.removeprefix(f"{prefix}/wiki/")) + ".md")
        else:
            return None
    else:
        target = document.parent / unquote(url.path) if url.path else document
    return target.resolve(), unquote(url.fragment)


def repository_documents(root: Path, *, include_notes: bool = False) -> list[Path]:
    """Enumerate maintained documentation explicitly, excluding data and build trees."""
    names = (
        "AGENTS.md",
        "README.md",
        "DESIGN.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "models/README.md",
        "tests/fixtures/README.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
    )
    paths = [root / name for name in names]
    paths.extend(sorted((root / "docs").rglob("*.md")))
    paths.extend(sorted((root / "wiki").glob("*.md")))
    if include_notes:
        paths.extend(sorted((root / ".agents/notes").rglob("*.md")))
    return paths


def link_errors(root: Path, documents: Iterable[Path]) -> list[str]:
    """Check containment, existence and Markdown fragments of repository link targets."""
    errors = []
    for document in documents:
        for raw in markdown_links(document.read_text(encoding="utf-8")):
            link = local_link(root, document, raw)
            if link is None:
                continue
            target, fragment = link
            issue = ""
            if not target.is_relative_to(root):
                issue = "outside repository"
            elif not target.exists():
                issue = "missing file"
            elif (
                fragment
                and target.suffix == ".md"
                and fragment not in heading_anchors(target.read_text(encoding="utf-8"))
            ):
                issue = "missing section"
            if issue:
                errors.append(f"{document.relative_to(root)} -> {raw} ({issue})")
    return errors


def reachable_documents(root: Path, documents: Iterable[Path], entries: Iterable[Path]) -> set[Path]:
    """Traverse real links from reading/platform entries, never Changelog outlinks."""
    documents = set(documents)
    graph = {}
    for document in documents:
        graph[document] = set()
        if document == root / "CHANGELOG.md":
            continue
        for raw in markdown_links(document.read_text(encoding="utf-8")):
            link = local_link(root, document, raw)
            if link and link[0] in documents:
                graph[document].add(link[0])
    seen = set(entries)
    pending = list(seen)
    while pending:
        for target in graph.get(pending.pop(), set()) - seen:
            seen.add(target)
            pending.append(target)
    return seen
