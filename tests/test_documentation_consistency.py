"""Guard high-risk documentation facts that are duplicated for human readers."""

import re
from pathlib import Path

import yaml

from scripts.documentation_links import link_errors, repository_documents

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    next_heading = text.find("\n## ", start)
    return text[start:] if next_heading == -1 else text[start:next_heading]


def _first_column_aliases(section: str) -> set[str]:
    return set(re.findall(r"^\| `([^`]+)` \|", section, flags=re.MULTILINE))


def test_refinement_prompt_version_matches_its_documentation():
    version = re.search(r'^PROMPT_VERSION = "([^"]+)"', _read("core/refinement/prompts.py"), re.MULTILINE)
    assert version is not None
    assert f"Prompt version: `{version.group(1)}`." in _read("docs/refinement-prompts.md")


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

    configured_images = {item["model_name"] for item in config["image_models"]}
    configured_vlm = {item["model_name"] for item in config["model_list"]} - configured_images
    documented_vlm = _first_column_aliases(_section(catalog, "## VLM aliases"))
    documented_images = _first_column_aliases(_section(catalog, "## Image-edit aliases"))

    assert documented_vlm == configured_vlm
    assert documented_images == configured_images
    assert f"Default image alias: `{config['default_image_alias']}`." in catalog

    vlm_rows = _table_rows(_section(catalog, "## VLM aliases"))
    for item in config["model_list"]:
        if item["model_name"] in configured_vlm:
            assert vlm_rows[item["model_name"]][0] == item["litellm_params"]["model"]

    image_rows = _table_rows(_section(catalog, "## Image-edit aliases"))
    for item in config["image_models"]:
        documented_display, documented_model, documented_provider = image_rows[item["model_name"]]
        assert documented_display == item["display_name"]
        deployments = [item_ for item_ in config["model_list"] if item_["model_name"] == item["model_name"]]
        assert documented_model == ",".join(dict.fromkeys(row["litellm_params"]["model"] for row in deployments))
        assert documented_provider == "litellm"
        assert set(item) == {"model_name", "display_name", "description", "generation"}
        assert item.get("generation"), f"{item['model_name']} is missing its generation capability policy"

    documented_roles = _table_rows(_section(catalog, "## VLM role routing"))
    assert set(documented_roles) == set(config["vision_roles"])
    for role, alias in config["vision_roles"].items():
        assert documented_roles[role] == [alias]

    assert config["router_settings"]["fallbacks"] == []
    assert "`fallbacks: []`" in catalog
    timeout = config["router_settings"]["timeout"]
    assert f"Default gateway request timeout: `{timeout}` seconds" in catalog
    stage_timeout = re.search(
        r"^VISION_STAGE_TIMEOUT_SECONDS = (\d+)$", _read("api/refinement/service.py"), re.MULTILINE
    )
    assert stage_timeout is not None
    assert int(stage_timeout.group(1)) > timeout
    assert f"{stage_timeout.group(1)}-second outer deadline" in _read("docs/ai-refinement.md")


def test_gateway_urls_and_keys_are_environment_backed():
    """Only inspect the public template: actual local credentials are never test inputs."""
    config = yaml.safe_load(_read("config/ai_gateway.yaml"))
    example = _read(".env.example")
    catalog = _read("docs/model-catalog.md")
    image_aliases = {item["model_name"] for item in config["image_models"]}
    for deployment in config["model_list"]:
        prefix = "AI_IMAGE" if deployment["model_name"] in image_aliases else "AI_VISION"
        params = deployment["litellm_params"]
        for field, suffix in (("model", "MODEL"), ("api_base", "API_BASE"), ("api_key", "API_KEY")):
            variable = f"{prefix}_{suffix}"
            assert params[field] == f"os.environ/{variable}"
            assert re.search(rf"^{variable}=", example, re.MULTILINE)
            assert f"`{variable}`" in catalog
    assert "AI_VISION_MODEL=openai/gpt-4o" in example
    assert "AI_IMAGE_MODEL=openai/gpt-image-1" in example
    for variable in ("AI_VISION_API_BASE", "AI_VISION_API_KEY", "AI_IMAGE_API_BASE", "AI_IMAGE_API_KEY"):
        assert re.search(rf"^{variable}=\s*$", example, re.MULTILINE)
    assert "DMXAPI" not in example


def test_default_catalog_is_generic_metadata_with_explicit_cost_and_size_policy():
    """Guard shipped defaults, not user-defined catalogs or provider billing behavior."""
    config = yaml.safe_load(_read("config/ai_gateway.yaml"))
    assert {item["model_name"] for item in config["model_list"]} == {
        "personaweave-vision-default",
        "personaweave-refine-default",
    }
    assert set(config["vision_roles"].values()) == {"personaweave-vision-default"}
    assert config["default_image_alias"] == "personaweave-refine-default"
    assert config["router_settings"]["num_retries"] == 0
    for image in config["image_models"]:
        assert image["generation"] == {"size": "auto"}
        for deployment in config["model_list"]:
            if deployment["model_name"] == image["model_name"]:
                assert deployment["litellm_params"]["max_retries"] == 0
    assert not re.search(r"(?:input|output)_cost_per_\w+", _read("config/ai_gateway.yaml"))


def test_native_routing_documentation_excludes_retired_supplier_paths():
    documents = (
        "README.md",
        "DESIGN.md",
        "AGENTS.md",
        "docs/ai-refinement.md",
        "docs/model-catalog.md",
        "docs/engineering-architecture.md",
        "docs/documentation-governance.md",
    )
    for path in documents:
        text = _read(path)
        assert "core/ai/providers/dmx_" not in text
        assert "core/providers/dmx_" not in text
        assert "tests/test_dmx_" not in text
        aliases = set(re.findall(r"personaweave-(?:vision|refine)-[a-z0-9-]+", text))
        assert aliases <= {"personaweave-vision-default", "personaweave-refine-default"}
    assert not list((ROOT / "core/ai/providers").glob("dmx_*.py"))
    catalog = _read("docs/model-catalog.md")
    for term in (
        "Router.acompletion",
        "Router.aimage_edit",
        "https://docs.litellm.ai/docs/routing",
        "https://docs.litellm.ai/docs/image_edits",
        "endpoint-specific",
        "does not promise 4K",
        "identical `generation`",
        "expected_revision",
        "candidate_digest",
        "not silently mapped",
    ):
        assert term in catalog
    migration = _section(catalog, "## Migration")
    assert "DMXAPI_API_BASE" in migration
    assert "incompatible" in migration
    assert "已退役历史" in _read("CHANGELOG.md")


def test_current_refinement_lifecycle_is_not_mixed_with_retired_states():
    refinement = _read("docs/ai-refinement.md")
    design = _read("DESIGN.md")
    success_flow = "queued → analyzing → generating → reviewing → review_required"

    assert refinement.count(success_flow) == 1
    assert "docs/ai-refinement.md" in design
    from typing import get_args

    from api.refinement.contracts import SessionTurn

    states = set(get_args(SessionTurn.model_fields["status"].annotation))
    assert set(success_flow.split(" → ")) <= states
    assert {"completed", "failed", "cancel_requested", "cancelled"} <= states
    assert "accepted" not in states
    assert "accepted_version_id" in refinement
    assert "QUEUED → REFINING" not in refinement
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
    app = _read("api/routes/swapping.py")
    frontend = _read("web/src/features/history/components/RecentJobsPopover.tsx")

    assert "/api/v1/task-history/batch-tasks" in history_doc
    assert "response_model=BatchTaskListResponse" in app
    assert "list_task_summaries" in app
    assert "listBatchTaskHistory" in frontend
    assert "TaskResultHistoryDialog" in frontend
    assert "DELETE /api/v1/batch-tasks/{task_id}" in history_doc
    assert "status_code=status.HTTP_204_NO_CONTENT" in app
    assert "deleteBatchTask" in frontend


def test_current_architecture_does_not_reference_removed_shadow_paths():
    current_docs = _read("README.md") + _read("DESIGN.md") + _read("models/README.md")

    assert "core/localized_refinement.py" not in current_docs
    assert "core/enhancer.py" not in current_docs
    assert "required by the current pipeline constructor" not in current_docs
    assert "docs/engineering-architecture.md" in _read("AGENTS.md")


def test_current_document_relative_links_exist():
    """Check current, historical, public and Wiki links with the same offline resolver."""
    missing = link_errors(ROOT, repository_documents(ROOT))
    assert not missing, "Missing local documentation links:\n" + "\n".join(missing)


def test_source_recipe_and_diagnostic_unknowns_are_documented():
    import numpy as np

    from core.swap_diagnostics import select_source_embedding

    refs = [{"embedding": np.array([1.0, 0.0])}, {"embedding": np.array([0.0, 1.0])}]
    _, recipe = select_source_embedding(0, refs, np.array([1.0, 0.0]))
    local_swap = (ROOT / "docs/facefusion-local-swap.md").read_text(encoding="utf-8")
    for field in ("gallery_weight", "best_reference_weight"):
        assert f"{round(recipe[field] * 100)}%" in local_swap
    for term in ("gallery_weights_known=false", "assignment_conflict", "target_reference_unavailable"):
        assert term in local_swap
    assert recipe["gallery_reference_weights"] is None
    assert "SWAPPED still means" in local_swap
    assert (ROOT / "docs/swap-benchmarks.md").exists()


def test_documented_evidence_pixel_budget_matches_shared_contract():
    from core.refinement.contracts import MAX_EVIDENCE_PIXELS

    refinement = _read("docs/refinement-session-api.md")
    assert "`MAX_EVIDENCE_PIXELS`" in refinement
    assert f"{MAX_EVIDENCE_PIXELS // 1_000_000} million decoded pixels per creation" in refinement
    assert MAX_EVIDENCE_PIXELS % 1_000_000 == 0


def test_confirmed_analysis_binding_is_documented_with_direct_command_boundary():
    from api.refinement.contracts import TurnCreateRequest

    field = TurnCreateRequest.model_fields["diagnosis_turn_id"]
    assert field.default is None
    refinement = _read("docs/ai-refinement.md")
    assert "`diagnosis_turn_id`" in refinement
    assert "session ownership, exact base, completed discussion and unambiguous mapping" in refinement
    assert "API/CLI direct `generate` without a confirmed-analysis ID" in refinement
    assert "diagnosis_turn_id" in _read("DESIGN.md")


def test_vision_overview_budget_matches_the_current_evidence_contract():
    match = re.search(r"^VISION_MAX_SIDE = (\d+)$", _read("core/refinement/conversation.py"), re.MULTILINE)
    assert match
    for path in ("docs/refinement-analysis.md", "docs/refinement-prompts.md"):
        assert f"maximum edge of {match.group(1)} pixels" in _read(path)
