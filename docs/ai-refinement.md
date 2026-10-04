# Conversational Whole-image AI Refinement

This is the canonical contract for PersonaWeave AI refinement. The production path is a persistent, human-directed editing session: whole-image-only generation, immutable evidence and explicit human publication. It is not a per-person scope form or a single mutable generation task. The local face-swap engine, its masks and identity matching are separate ([facefusion-local-swap.md](facefusion-local-swap.md)).

Related documents: evidence, diagnosis and the untrusted-data boundary in [refinement-analysis.md](refinement-analysis.md); generation and review in [refinement-generation.md](refinement-generation.md); prompt version and official sources in [refinement-prompts.md](refinement-prompts.md); HTTP routes, storage, limits and deletion in [refinement-session-api.md](refinement-session-api.md); workbench flow and editor recovery in [refinement-workbench.md](refinement-workbench.md); routing in [model-catalog.md](model-catalog.md); the read-only legacy archive in [delivery-quality.md](delivery-quality.md).

## Architecture and ownership

- `api/routes/refinement_sessions.py`: session commands, bounded summaries, details, verified artifact bytes and resumable SSE notifications.
- `api/refinement/contracts.py`: HTTP commands, session/turn/version projections and status literals.
- `api/refinement/service.py`: server-owned turn orchestration and bounded model calls.
- `api/refinement/store.py`: SQLite truth, immutable images, concurrency, events and exact-byte publication.
- `api/refinement/batch.py`: optional first automatic turn after a committed local result, using the actually loaded references and stable assignment outcomes. It persists the session association before joining the already scheduled turn; the next batch image waits for that turn (including `review_required`), not for human acceptance, and joining never submits another turn ([memory-scheduling.md](memory-scheduling.md)).
- `core/refinement/conversation.py`: three-evidence diagnosis/discussion, single-image generation and advisory review through `AIGateway`.
- `core/refinement/contracts.py`, `prompts.py`, `evidence.py`: score-free schemas, trusted prompts and per-person reference boards; pure encoding/sizing/normalization lives in `image_ops.py`.
- `web/src/features/refinement/`: workbench, conversation, versions, reference inspection and lightweight client state. Viewing a version, choosing a generation base and accepting a final version are distinct operations.

The flow is a bounded Python workflow: no extra agent service, duplicate model registry, arbitrary tool executor or autonomous acceptance loop. The gateway is supplier-neutral native LiteLLM.

## Session, turn and version

A **session** freezes the original, local baseline, stable person mappings and actual reference image snapshots; later library edits do not change its evidence. A **turn** records one user request, its explicit `base_version_id`, mode, diagnosis, plan, generation metadata and review. An immutable **version** has its own ID, parent version ID, digest and artifact URL; the baseline is a version too.

Selecting a historical version for viewing does not change the next generation base or publish it. Explicitly selecting an old base creates a branch; it never overwrites that version or silently substitutes the newest candidate. The server stores `accepted_version_id` separately, so later candidates do not replace an accepted result.

## Execution modes and status

The backend, not the model, selects the mode:

- `discuss`: analyze/answer/propose; never generate.
- `generate`: analyze the explicit base and current request, then generate at most one candidate when a concrete plan exists and mapping is unambiguous.
- `auto`: the authorized first-pass workflow; generate at most one candidate only for supported issues and an actionable plan. No issue, no needed edit or uncertain mapping stops with an explanation.

A completed local result can start the authorized automatic first turn through the batch hook; local success is neither AI success nor acceptance, and selecting only local processing does not authorize the hook.

The workbench sends `diagnosis_turn_id` with generation. The server validates session ownership, exact base, completed discussion and unambiguous mapping before enqueueing any model work, persists the binding with the turn and its idempotency fingerprint, and supplies exactly that diagnosis as `previous_diagnosis`, even if another analysis is newer. API/CLI direct `generate` without a confirmed-analysis ID remains an explicit one-command analysis-and-generation operation, and batch `auto` authorization stays separate; neither fabricates a UI confirmation. Regression: [interaction tests](../web/src/features/refinement/components/SessionWorkbench.test.tsx), [session tests](../tests/test_refinement_sessions.py).

Session status is `idle` or `running`. Turn status follows:

`queued → analyzing → generating → reviewing → review_required`

Discussion, no-op or clarification ends at `completed` after analysis. Failures use `failed`; cancellation uses `cancel_requested → cancelled`. `review_required` is a terminal execution state waiting for human judgment, not an instruction to regenerate. New turns use new IDs and explicit bases. Acceptance updates the session's accepted pointer and publication record; it is not an AI-emitted turn status.

Each session has at most one active turn, the server limits concurrent sessions, and navigating away does not stop a worker. Cancel is an explicit revision-checked command and cannot guarantee that an already transmitted upstream request is unbilled. Restart never silently reissues a paid operation: an interrupted turn without a durable candidate fails or cancels; one with a candidate returns to human review with an interruption warning.

## Waiting limits

The default gateway request waits up to 300 seconds. Analysis and review each have a 330-second outer deadline, leaving room for evidence preparation and response validation. Image editing keeps its 600-second deployment timeout and 900-second generation-stage deadline. These are waiting limits, not duration estimates or retry settings; see [gateway configuration](../config/ai_gateway.yaml) and [session executor](../api/refinement/service.py).

## Evidence

- Code is listed under Architecture and ownership. Regression: `tests/test_refinement_conversation.py`, `tests/test_refinement_sessions.py`, `tests/test_batch_refinement.py`, `tests/test_legacy_refinement.py`, `tests/test_refinement_image_ops.py`; gateway transport `tests/test_litellm_gateway.py`; documentation invariants `tests/test_documentation_consistency.py`.
- Live test: the [2026-09-11 five-person test](refinement-live-test-20260911.md) records two real generated candidates, prompt/parent/digest checks, a preserved review failure and manual findings; both stayed unaccepted because of appearance drift and unresolved small occlusion details. Contract tests passing and candidates being generated do not establish visual quality.

Documentation rules: [documentation-governance.md](documentation-governance.md).
