"""Guard high-risk documentation facts that are duplicated for human readers."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    next_heading = text.find("\n## ", start)
    return text[start:] if next_heading == -1 else text[start:next_heading]


def _first_column_aliases(section: str) -> set[str]:
    return set(re.findall(r"^\| `([^`]+)` \|", section, flags=re.MULTILINE))


def _table_rows(section: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        rows[cells[0]] = cells[1:]
    return rows


def test_model_catalog_matches_gateway_configuration():
    """A documented selectable model must exactly match the executable YAML catalog."""
    config = yaml.safe_load(_read("config/ai_gateway.yaml"))
    catalog = _read("docs/model-catalog.md")

    configured_vlm = {item["model_name"] for item in config["model_list"]}
    configured_images = {item["model_name"] for item in config["image_models"]}
    documented_vlm = _first_column_aliases(_section(catalog, "## VLM aliases"))
    documented_images = _first_column_aliases(_section(catalog, "## Image-edit aliases"))

    assert documented_vlm == configured_vlm
    assert documented_images == configured_images
    assert f"Default image alias: `{config['default_image_alias']}`." in catalog

    vlm_rows = _table_rows(_section(catalog, "## VLM aliases"))
    for item in config["model_list"]:
        assert vlm_rows[item["model_name"]][0] == item["litellm_params"]["model"].removeprefix("openai/")

    image_rows = _table_rows(_section(catalog, "## Image-edit aliases"))
    for item in config["image_models"]:
        documented_display, documented_model, documented_provider = image_rows[item["model_name"]]
        assert documented_display == item["display_name"]
        assert documented_model == item["params"]["model"].removeprefix("openai/")
        assert documented_provider == item["provider"]
        assert item.get("generation"), f"{item['model_name']} is missing its generation capability policy"

    documented_roles = _table_rows(_section(catalog, "## VLM role routing"))
    assert set(documented_roles) == set(config["vision_roles"])
    for role, alias in config["vision_roles"].items():
        assert documented_roles[role] == [alias]

    refinement_alias = config["vision_roles"]["refinement"]
    refinement_fallback = next(
        item[refinement_alias] for item in config["router_settings"]["fallbacks"] if refinement_alias in item
    )
    documented_chain = " → ".join([refinement_alias, *refinement_fallback])
    assert f"`{documented_chain}`" in catalog


def test_current_refinement_lifecycle_is_not_mixed_with_retired_states():
    refinement = _read("docs/ai-refinement.md")
    design = _read("DESIGN.md")
    success_flow = "QUEUED → REFINING → REVIEWING → REVIEW_REQUIRED → ACCEPTING → ACCEPTED"

    assert refinement.count(success_flow) == 1
    assert success_flow in design
    assert "QUEUED → PREFLIGHTING" not in refinement + design
    assert "DIAGNOSIS_REQUIRED →" not in refinement + design
    assert "CandidateQualityReview" not in design
    assert "GlobalProtectionReview" not in design
    assert "review_local_candidate" not in design


def test_portrait_selection_summary_matches_frontend_policy():
    readme = _read("README.md")
    selection = _read("web/src/utils/portraitAssetSelection.ts")

    assert "默认最多选择 8 张" in readme
    assert "默认预选正脸/微侧/微笑等 6 张" not in readme
    assert "limit = 8" in selection
    assert "asset.category !== 'contact_sheet'" in selection


def test_agent_rules_use_current_mapping_and_review_contracts():
    agent_rules = _read("AGENTS.md")

    assert "target_reference_person_index" in agent_rules
    assert "source_asset_ids" in agent_rules
    assert "CandidateQualityReview" not in agent_rules
    assert "docs/documentation-governance.md" in agent_rules


def test_task_history_contract_uses_summary_then_detail():
    history_doc = _read("docs/task-history.md")
    app = _read("api/app.py")
    frontend = _read("web/src/components/layout/RecentJobsPopover.tsx")

    assert "/api/v1/task-history/batch-tasks" in history_doc
    assert "response_model=BatchTaskListResponse" in app
    assert "list_task_summaries" in app
    assert "listBatchTaskHistory" in frontend
    assert "TaskResultHistoryDialog" in frontend


def test_current_architecture_does_not_reference_removed_shadow_paths():
    current_docs = _read("README.md") + _read("DESIGN.md") + _read("models/README.md")

    assert "core/localized_refinement.py" not in current_docs
    assert "core/enhancer.py" not in current_docs
    assert "required by the current pipeline constructor" not in current_docs
    assert "docs/engineering-architecture.md" in _read("AGENTS.md")


def test_current_document_relative_links_exist():
    """Catch renamed or incorrectly rooted local evidence links before release."""
    documents = [ROOT / "AGENTS.md", ROOT / "README.md", ROOT / "DESIGN.md", *sorted((ROOT / "docs").glob("*.md"))]
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

    missing: list[str] = []
    for document in documents:
        for raw_target in link_pattern.findall(document.read_text(encoding="utf-8")):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.exists():
                missing.append(f"{document.relative_to(ROOT)} -> {raw_target}")

    assert not missing, "Missing local documentation links:\n" + "\n".join(missing)
