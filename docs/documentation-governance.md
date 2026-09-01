# Documentation governance and evidence policy

PersonaWeave treats documentation as part of the shipped behavior. A code change is incomplete when it changes an externally visible workflow, default, model, status, safety rule, storage rule, or failure mode without updating the corresponding documentation and regression evidence.

## Authority hierarchy

When two descriptions disagree, use the narrowest authoritative source below and fix the other document in the same change. `CHANGELOG.md` is historical evidence, not a current specification.

| Topic | Canonical current document | Executable fact source | Regression evidence |
| --- | --- | --- | --- |
| Whole-image AI refinement | [ai-refinement.md](./ai-refinement.md) | [`api/refinement_task_manager.py`](../api/refinement_task_manager.py), [`core/refinement_service.py`](../core/refinement_service.py), [`api/schemas.py`](../api/schemas.py) | `tests/test_refinement_tasks.py`, `tests/test_refinement_service.py`, `tests/test_vlm_contracts.py` |
| VLM and image-edit routing | [model-catalog.md](./model-catalog.md) | [`config/ai_gateway.yaml`](../config/ai_gateway.yaml), [`core/ai_gateway.py`](../core/ai_gateway.py), `GET /api/v1/ai/models` | `tests/test_api.py`, `tests/test_dmx_gemini.py`, `tests/test_dmx_generations.py`, `tests/test_documentation_consistency.py` |
| Production local face swap | [facefusion-local-swap.md](./facefusion-local-swap.md) | [`core/pipeline.py`](../core/pipeline.py), [`core/model_router.py`](../core/model_router.py), [`core/occluder.py`](../core/occluder.py), [`core/region_parser.py`](../core/region_parser.py) | `tests/test_facefusion_pipeline.py`, `tests/test_swapper_policy.py`, `tests/test_task_swap_outcome.py` |
| Multi-person assignment | [facefusion-local-swap.md](./facefusion-local-swap.md) | `resolve_person_mapping_assignment()` in [`core/pipeline.py`](../core/pipeline.py) | `tests/test_multi_person_assignment.py`, `tests/test_facefusion_pipeline.py` |
| Portrait library and default reference selection | README summary plus this policy | [`api/portrait_library.py`](../api/portrait_library.py), [`web/src/utils/portraitAssetSelection.ts`](../web/src/utils/portraitAssetSelection.ts) | `tests/test_portrait_library.py`, `web/src/utils/portraitAssetSelection.test.ts`; real M01 evidence in [`data/portrait_expansions/README.md`](../data/portrait_expansions/README.md) |
| Multi-row person labels | README summary plus this policy | [`web/src/utils/faceTopology.ts`](../web/src/utils/faceTopology.ts) | `web/src/utils/faceTopology.test.ts` |
| Comparison geometry and face overlays | README summary plus this policy | [`web/src/utils/imageViewport.ts`](../web/src/utils/imageViewport.ts), `ComparisonViewer.tsx`, `TargetCanvas.tsx` | `web/src/utils/imageViewport.test.ts`, `ComparisonViewer.test.tsx`, `TargetCanvas.test.tsx` |
| Batch task history and result review | [task-history.md](./task-history.md) | [`api/task_manager.py`](../api/task_manager.py), [`api/task_persistence.py`](../api/task_persistence.py), [`web/src/utils/taskHistory.ts`](../web/src/utils/taskHistory.ts) | `tests/test_api.py`, `tests/test_task_restart_persistence.py`, `web/src/utils/taskHistory.test.ts`, `RecentJobsPopover.test.tsx` |
| Engineering boundaries and cleanup policy | [engineering-architecture.md](./engineering-architecture.md) | package boundaries, `config/ai_gateway.yaml`, `scripts/download_models.py` | `tests/test_package_boundaries.py`, `tests/test_ai_gateway_config.py`, `tests/test_download_models.py`, `tests/test_documentation_consistency.py` |

`README.md` is the user-facing capability summary. `DESIGN.md` explains architecture. Both must link to the canonical documents instead of maintaining a competing state machine or model inventory. `AGENTS.md` supplies mandatory engineering constraints. Historical changelog entries may describe retired behavior only when they are clearly labeled as historical or superseded.

## Evidence requirements

A documentation claim must be backed by at least one of:

- a schema, configuration entry, or production code path;
- a regression test asserting the behavior;
- a reproducible real-image or model-asset verification record with inputs, metrics, and checksums;
- an upstream primary source for third-party model limits, licenses, or algorithms, plus a local compatibility test when the claim affects production behavior.

Do not infer support from a filename, an unused module, a prompt sentence, an old changelog entry, or a provider marketing name. “Configured” means present in the active gateway configuration; “production path” means reachable from the current API/CLI path; “verified” means the evidence and reproduction route are recorded.

## Change checklist

For every behavior-changing pull request or local release:

1. identify the canonical document from the table above;
2. update code, canonical documentation, README/DESIGN summaries, and `CHANGELOG.md` together when each is affected;
3. link each non-obvious claim to code, configuration, tests, or a reproducible verification record;
4. remove or explicitly mark superseded descriptions—do not leave two active flows in different sections;
5. update `tests/test_documentation_consistency.py` when a new invariant can be checked mechanically;
6. run Ruff, the relevant Python tests, frontend tests/build, and the documentation consistency test.

The consistency test intentionally checks high-risk duplicated facts: refinement lifecycle, model-alias tables/default, portrait-selection count, and local relative links. It complements review; it does not prove that prose semantics are correct.
