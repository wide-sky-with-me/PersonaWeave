# Documentation governance and evidence policy

PersonaWeave treats documentation as part of the shipped behavior. A code change is incomplete when it changes an externally visible workflow, default, model, status, safety rule, storage rule, or failure mode without updating the corresponding documentation and regression evidence.

## Authority hierarchy

When two descriptions disagree, use the narrowest authoritative source below and fix the other document in the same change. `CHANGELOG.md` is historical evidence, not a current specification.

| Topic | Canonical current document | Executable fact source | Regression evidence |
| --- | --- | --- | --- |
| Conversational whole-image refinement | [ai-refinement.md](./ai-refinement.md) | [`api/refinement/service.py`](../api/refinement/service.py), [`api/refinement/store.py`](../api/refinement/store.py), [`api/refinement/contracts.py`](../api/refinement/contracts.py), [`core/refinement/conversation.py`](../core/refinement/conversation.py) | `tests/test_refinement_sessions.py`, `tests/test_refinement_conversation.py`, `tests/test_batch_refinement.py`, `tests/test_legacy_refinement.py` |
| Refinement evidence and prompt contracts | [refinement-prompts.md](./refinement-prompts.md), subordinate to the refinement architecture | [`core/refinement/contracts.py`](../core/refinement/contracts.py), [`core/refinement/prompts.py`](../core/refinement/prompts.py), [`core/refinement/evidence.py`](../core/refinement/evidence.py) | `tests/test_refinement_conversation.py`; official sources and retrieval limitations are recorded in the prompt document |
| VLM and image-edit routing | [model-catalog.md](./model-catalog.md) | [`config/ai_gateway.yaml`](../config/ai_gateway.yaml), [`core/ai/configuration.py`](../core/ai/configuration.py), [`core/ai/gateway.py`](../core/ai/gateway.py), `GET /api/v1/ai/models` | `tests/test_api.py`, `tests/test_ai_gateway_config.py`, `tests/test_ai_readiness.py`, `tests/test_litellm_gateway.py`, `tests/test_documentation_consistency.py` |
| Production local face swap | [facefusion-local-swap.md](./facefusion-local-swap.md) | [`core/pipeline.py`](../core/pipeline.py), [`core/model_router.py`](../core/model_router.py), [`core/occluder.py`](../core/occluder.py), [`core/region_parser.py`](../core/region_parser.py) | `tests/test_facefusion_pipeline.py`, `tests/test_swapper_policy.py`, `tests/test_task_swap_outcome.py` |
| Multi-person assignment | [facefusion-local-swap.md](./facefusion-local-swap.md) | `resolve_person_mapping_assignment()` in [`core/pipeline.py`](../core/pipeline.py) | `tests/test_multi_person_assignment.py`, `tests/test_facefusion_pipeline.py` |
| Portrait library and default reference selection | README summary plus this policy | [`api/portraits/library.py`](../api/portraits/library.py), [`web/src/utils/portraitAssetSelection.ts`](../web/src/utils/portraitAssetSelection.ts) | `tests/test_portrait_library.py`, `web/src/utils/portraitAssetSelection.test.ts`; real M01 evidence in [`data/portrait_expansions/README.md`](../data/portrait_expansions/README.md) |
| Multi-row person labels | README summary plus this policy | [`web/src/utils/faceTopology.ts`](../web/src/utils/faceTopology.ts) | `web/src/utils/faceTopology.test.ts` |
| Comparison geometry and face overlays | README summary plus this policy | [`web/src/utils/imageViewport.ts`](../web/src/utils/imageViewport.ts), `ComparisonViewer.tsx`, `TargetCanvas.tsx` | `web/src/utils/imageViewport.test.ts`, `ComparisonViewer.test.tsx`, `TargetCanvas.test.tsx` |
| Batch task history and result review | [task-history.md](./task-history.md) | [`api/tasks/batch.py`](../api/tasks/batch.py), [`api/tasks/persistence.py`](../api/tasks/persistence.py), [`web/src/features/history/utils/taskHistory.ts`](../web/src/features/history/utils/taskHistory.ts) | `tests/test_api.py`, `tests/test_task_restart_persistence.py`, `web/src/features/history/utils/taskHistory.test.ts`, `RecentJobsPopover.test.tsx` |
| Offline benchmarks and historical measurements | [swap-benchmarks.md](./swap-benchmarks.md) | `scripts/compare_source_strategies.py`, `scripts/evaluate_swap_quality.py`, `scripts/diagnose_batch_strength.py` | `tests/test_source_strategy_comparison.py`, `tests/test_swap_benchmarks.py`, `tests/test_diagnose_batch_strength.py` |
| Engineering boundaries and cleanup policy | [engineering-architecture.md](./engineering-architecture.md) | package boundaries, `config/ai_gateway.yaml`, `scripts/download_models.py` | `tests/test_package_boundaries.py`, `tests/test_ai_gateway_config.py`, `tests/test_download_models.py`, `tests/test_documentation_consistency.py` |

`README.md` is the user-facing capability summary. `DESIGN.md` explains architecture. Both must link to the canonical documents instead of maintaining a competing state machine or model inventory. `AGENTS.md` supplies mandatory engineering constraints. Historical changelog entries may describe retired behavior only when they are clearly labeled as historical or superseded.

## Evidence requirements

A documentation claim must be backed by at least one of:

- a schema, configuration entry, or production code path;
- a regression test asserting the behavior;
- a reproducible real-image or model-asset verification record with inputs, metrics, and checksums;
- an upstream primary source for third-party model limits, licenses, or algorithms, plus a local compatibility test when the claim affects production behavior.

Do not infer support from a filename, an unused module, a prompt sentence, an old changelog entry, or a provider marketing name. “Configured” means present in the active gateway configuration; “production path” means reachable from the current API/CLI path; “verified” means the evidence and reproduction route are recorded.

For LiteLLM, consult the official [Router documentation](https://docs.litellm.ai/docs/routing) and [image edits documentation](https://docs.litellm.ai/docs/image_edits). Native provider support is endpoint-specific: chat support or an arbitrary base URL does not prove image-edit compatibility. Model examples are replaceable, not a verified service inventory; default `size: auto` is not a 4K claim, and unknown price must not be documented as free. Offline Router tests and configuration readiness checks do not prove paid upstream availability. No documentation check should read actual `.env`, make paid model calls, or modify runtime data.

## Change checklist

For every behavior-changing pull request or local release:

1. identify the canonical document from the table above;
2. update code, canonical documentation, README/DESIGN summaries, and `CHANGELOG.md` together when each is affected;
3. link each non-obvious claim to code, configuration, tests, or a reproducible verification record;
4. remove or explicitly mark superseded descriptions—do not leave two active flows in different sections;
5. update `tests/test_documentation_consistency.py` when a new invariant can be checked mechanically;
6. run the documentation consistency test for documentation changes; run Ruff when Python changes, relevant Python regressions for behavior changes, and frontend checks/tests/build when frontend code changes.

The consistency test intentionally checks high-risk duplicated facts: refinement lifecycle, native deployment/metadata alias tables and default, generic model/connection environment references, explicit generation/retry defaults, retired supplier-path exclusion, portrait-selection count, and local file/section links. It complements review; it does not prove that prose semantics are correct.
