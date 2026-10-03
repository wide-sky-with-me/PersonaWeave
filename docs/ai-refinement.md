# Conversational Whole-image AI Refinement

This is the canonical current contract for PersonaWeave AI refinement. The production path is a persistent, human-directed editing session, not the retired per-person scope form or a single mutable generation task. It preserves whole-image-only generation, immutable evidence and explicit human publication. The local face-swap engine and its separate masks/identity matching are unchanged.

## Architecture and ownership

Batch-triggered auto refinement persists the session association before joining its already scheduled turn.
The next batch image waits for that turn to finish (including `review_required`), not for human acceptance.
This bounds overlapping batch image contexts; joining never submits another turn. See [memory scheduling](memory-scheduling.md).

Restoring a session keeps historical failures in the conversation without replaying old dialogs. Newly observed turn failures require acknowledgement.

Studio displays rejected commands, failed turns and failed candidate reviews in acknowledgement dialogs, preserving server error details for rejected inputs. A failed review still retains the candidate for human inspection; dismissing a dialog never retries or generates anything. See `web/src/features/refinement/components/SessionWorkbench.tsx` and its colocated regression tests.

- `api/routes/refinement_sessions.py`: session commands, bounded summaries, details, verified artifact bytes and resumable SSE notifications.
- `api/refinement/contracts.py`: HTTP commands, session/turn/version projections and status literals.
- `api/refinement/service.py`: server-owned turn orchestration and bounded model calls.
- `api/refinement/store.py`: SQLite truth, immutable images, concurrency, events and exact-byte publication.
- `api/refinement/batch.py`: optional first automatic turn after a committed local result, using actual loaded references and stable assignment outcomes.
- `core/refinement/conversation.py`: three-evidence diagnosis/discussion, single-image generation and advisory review through `AIGateway`.
- `core/refinement/contracts.py`, `prompts.py`, `evidence.py`: score-free schemas, trusted prompts and per-person reference boards. Pure encoding/sizing/normalization lives in `image_ops.py`.
- `web/src/features/refinement/`: workbench, conversation, versions, reference inspection and lightweight client state. Viewing a version, choosing a generation base and accepting a final version are distinct operations.

There is no extra Pi Core service, duplicate model registry, arbitrary agent tool executor or autonomous acceptance loop. This is a bounded Python workflow with Pi-style conversation/tool separation. The gateway remains supplier-neutral native LiteLLM; routing details live in [model-catalog.md](model-catalog.md). Official prompting sources, retrieval limitations and concrete examples live in [refinement-prompts.md](refinement-prompts.md).

## Session, turn and version

A **session** freezes the original, local baseline, stable person mappings and actual reference image snapshots. Later library edits do not change its evidence. A **turn** records one user request, its explicit `base_version_id`, mode, diagnosis, plan, generation metadata and review. An immutable **version** has its own ID, parent version ID, digest and artifact URL. The baseline is a version too.

Selecting a historical version for viewing does not switch the next generation base or publish it. Explicitly selecting an old base creates a branch; it never overwrites that version or silently substitutes the newest candidate. The server stores `accepted_version_id` separately, so additional candidates do not replace an accepted result.

## Evidence and diagnosis

Each analysis uses these evidence roles:

1. **Original image** anchors pose, people count, occlusion relationships, composition and scene, not the original facial identity. Requested identity replacement alone is not a defect.
2. **Current selected version** is the complete canvas to assess.
3. **Reference boards** contain only the desired identity references belonging to each stable `person_id`. Every tile carries that same ID. Missing references remain explicit; list position, a fresh detector ordering or similarity guesses never rebind people.

The interactive creation snapshot also names each original detection’s fixed left-to-right face index and, when original dimensions are available, its measured face-center percentages from the top-left. These text locators keep stable person IDs distinct from image-specific indices and do not re-detect or reorder candidates. Missing dimensions omit coordinates rather than inventing them. Existing sessions keep their frozen locators and can be clarified through discussion. The reference sidebar shows person IDs and thumbnails, with long original-image locators collapsed by default. Implementation and regression: [session recipe](../web/src/features/refinement/sessionRecipe.ts), [recipe tests](../web/src/features/refinement/sessionRecipe.test.ts).

Original/current/before/candidate evidence uses a JPEG overview with a maximum edge of 3200 pixels (VISION_MAX_SIDE in the conversation service); smaller inputs retain their dimensions. This application evidence budget raises the previous 1600px overview limit to preserve more small-face detail without sending arbitrarily large images. It is not a provider capability claim. Reference boards retain their bounded tile layout; provider-side sampling and input limits can still restrict detail or reject requests. Existing decoded evidence budgets still apply.

The VLM jointly considers identity appearance, anatomy, occlusion, blending, lighting, texture and composition. It returns `Diagnosis`: `assistant_message`, `needs_edit`, `mapping_uncertain`, `issues`, `people`, `edit_plan`, `uncertainties`. Each issue has a category, optional person ID, concrete region, observation, visible evidence, suggestion, severity and qualitative confidence/uncertainty. Each person has a visible locator, `mapping_confident` and `identity_status` (`match`, `mismatch`, `uncertain`, `unavailable`). These are visual comparisons, not verified real-world identities.

No score threshold or compulsory fault-finding is allowed. Clean images can return no issues and no edit plan. Small faces, side profiles, occlusion or insufficient comparable features can yield unknown/uncertain, not invented anatomy or identity truth. Without references, naturalness analysis is still available but identity status must be unavailable. Ambiguous person mapping requires clarification before generation.

A user can request a concrete whole-image change without an existing defect; put it in the plan rather than inventing an issue. User requirements remain separate from model observations. Images, OCR, user prose, historical messages and person metadata are untrusted task data, never system/tool/acceptance authority. Recent history is bounded text only; old-version mentions are not hidden image attachments.

## Execution modes and flow

The interactive workbench follows **检查问题 → 确认分析 → 补充意见与修改 → 对比并继续下一轮**. Opening or creating a session does not call models. First submit a `discuss` inspection; findings appear in collapsible groups (人物与五官、遮挡与融合、光影与质感、构图与场景), with specific locations, observations, evidence and suggestions. Uncertainty, person comparisons and the edit plan are separate expandable sections. A generation result prominently shows its advisory review; its input plan and earlier analysis are collapsed.

The human explicitly confirms the completed discussion for this exact base before the feedback field and generation become available. Feedback can amend or narrow AI suggestions; a clean diagnosis requires a new visual request to generate. Ambiguous person mapping instead enables clarification and discussion. Updating analysis requires a new confirmation. Selecting “以此版继续” explicitly changes the base, clears confirmation and the old feedback draft, and requires analysis of that version before another generation. The canvas initially shows the complete selected version; original/baseline split comparisons require an explicit selection, so the target is not silently hidden behind the original half. Viewing, choosing a base and accepting remain independent; generating never silently advances the next base or final version. Iterations are human-directed within the existing 200-turn limit.

The workbench sends `diagnosis_turn_id` with generation. The server validates session ownership, exact base, completed discussion and unambiguous mapping before enqueueing any model work, and persists the binding with the turn and idempotency fingerprint. It supplies this exact diagnosis as `previous_diagnosis`, even if another analysis is newer. API/CLI direct `generate` without a confirmed-analysis ID remains an explicit one-command analysis-and-generation operation; batch `auto` authorization also remains separate. These commands never fabricate a UI confirmation. Implementation and regressions: [workbench](../web/src/features/refinement/components/SessionWorkbench.tsx), [composer](../web/src/features/refinement/components/SessionComposer.tsx), [interaction tests](../web/src/features/refinement/components/SessionWorkbench.test.tsx), [session tests](../tests/test_refinement_sessions.py).

The backend, not the model, selects the mode:

- `discuss`: analyze/answer/propose; never generate.
- `generate`: analyze the explicit base and current request, then generate at most one candidate when a concrete plan exists and mapping is unambiguous.
- `auto`: the authorized first-pass workflow; generate at most one candidate only for supported issues and an actionable plan. No issue, no needed edit or uncertain mapping stops with an explanation.

A completed local result can start the authorized automatic first turn through the batch hook. Local success does not mean AI success or human acceptance; model errors remain separate. Selecting only local processing does not authorize that hook.

Session status is `idle` or `running`. Turn status follows:

`queued → analyzing → generating → reviewing → review_required`

Discussion, no-op or clarification ends at `completed` after analysis. Failures use `failed`; cancellation uses `cancel_requested → cancelled`. `review_required` is a terminal execution state waiting for human judgment, not an instruction to regenerate. New turns use new IDs and explicit bases. Acceptance changes the session's accepted pointer/publication record; it is not an AI-emitted turn status.

Each session has at most one active turn. The server limits concurrent sessions; navigating away does not stop a worker. Cancel is an explicit revision-checked command and cannot guarantee that an already transmitted upstream request is unbilled. Restart never silently reissues a paid operation: an interrupted turn without a durable candidate fails/cancels; one with a candidate returns to human review with an interruption warning.

## Whole-image generation and advisory review

Before generating, analysis receives the explicitly confirmed diagnosis when supplied; otherwise it receives the full latest completed (`completed` or `review_required`) diagnosis of the exact immutable `base_version_id` as `previous_diagnosis`, separately from bounded prose history. It rechecks those hypotheses against the images and combines supported findings with current human feedback into one plan. Current visual preferences override conflicting earlier suggestions; narrowed scope does not turn every issue into a mandatory edit. An unrelated version's diagnosis is never carried over as current findings. This uses the existing one analysis call per generation turn. Regression: `tests/test_refinement_sessions.py` and `tests/test_refinement_conversation.py`.

`generate()` sends **exactly one full-resolution lossless current image** and text to `AIGateway.edit_image`. Text contains current issues, requested changes, semantic preserve intentions and stable visible person locators. Analysis must translate reference observations into self-contained changes instead of instructing the generator to consult absent reference boards; discussion notices stay outside edit-plan instructions. It must not attach the original, reference boards, source photos, face crops, masks, keypoints, annotations or older revisions. The core also rejects uncertain mapping and no-op diagnoses before generation.

Coherent repainting of the entire image is allowed where required by the edit: hair, clothing, occlusion boundaries, lighting and background are not blocked by the retired face-only checkbox scope. Preserve intentions reduce unrelated drift but do not imply pixel equality. A single current image plus words may not repair severe identity mismatch; references used by the critic are not secretly available to the generator.

Generation size comes only from declared gateway metadata through `generation_parameters()`, never provider-name heuristics. The provider output is decoded and normalized for orientation/color/canvas dimensions; only whole-image center-crop/resize normalization is allowed. There is no landmark realignment, mask, local backfill or `apply_candidate`. The normalized result is retained directly as an immutable candidate **before** review. The workbench displays provider output and normalized canvas dimensions from generation metadata; when output was enlarged it explicitly states that upscaling does not restore original detail.

Review sees original + this turn's before image + candidate + the same reference boards. `CandidateReview` contains `summary`, `improved`, `remaining`, `new_issues`, `uncertainties`, `people`; no score, accept flag or automatic regeneration decision. It must reassess diagnosis hypotheses against visible evidence, not copy them as established facts. A requested non-face change is not automatically a regression. Review failure records `review_error` and preserves the candidate for human judgment.

Analysis and review each make one structured gateway call, supplying the full JSON schema (including descriptions of enforced cross-field consistency rules) initially and `max_text_repairs=0`. Invalid output is surfaced instead of adding automatic text-only repairs. Failures retain only safe classifications (upstream HTTP status when available, request timeout, or allowlisted schema error codes); provider bodies, model-output values, validation context and extra-field names never enter the public error. Explicit user-configured LiteLLM Router retries/fallbacks can still cause additional upstream attempts and charges. Other callers such as identity arbitration retain their documented default repair budget. No offline test implies paid service availability or real photo quality.

## HTTP contract

The executable schema is `api/refinement/contracts.py`; all routes below are under `/api/v1/refinement/sessions`.

| Operation | Route | Boundary |
| --- | --- | --- |
| Create | `POST /` | Freeze evidence; idempotent `request_id`; no model call |
| List | `GET /?limit=&offset=` | Bounded summaries without images, full conversations or requests |
| Detail | `GET /{session_id}` | Full aggregate only when opened |
| Delete | `DELETE /{session_id}?expected_revision=` | Idle, current revision only; delete the entire session and its owned files |
| Submit | `POST /{session_id}/turns` | `request_id`, `expected_revision`, explicit base, mode, message and optional `diagnosis_turn_id` |
| Cancel | `POST /{session_id}/cancel` | Explicit revision check; no silent base switch |
| Accept | `POST /{session_id}/accept` | Session ownership, revision, selected version and digest |
| Artifact | `GET /{session_id}/artifacts/{digest}` | Return the same byte buffer checked against database reference and digest |
| Events | `GET /{session_id}/events?after=` | SSE cursor or `Last-Event-ID`; lightweight snapshot notifications |

Root routes are registered at the prefix itself, without requiring a trailing slash. SSE sends session ID, revision and event kind, not model reasoning, image payloads or full messages. Clients fetch a fresh snapshot; reconnecting a stream never submits a generation. Reusing the same request ID with the same logical command is idempotent; a conflicting payload or stale revision returns `409`. Unavailable/corrupt artifacts use `410`; invalid commands use `422`; missing model configuration blocks model actions with `503`, not stored-session inspection or acceptance.

## Storage and exact-byte publication

An explicit delete command removes an idle session, all turns/versions/events/artifact references and every accepted publication belonging to it. This is the user's record-retention decision; acceptance alone still preserves history. A stale revision or active turn returns `409`, a missing session `404`, missing/invalid revision `422`, and storage failure `503`. AI configuration is not required. Original batch tasks and portrait-library assets remain independent; session references are frozen copies. Files already missing do not block deletion.

Deletion serializes with SQLite writers, derives private directory and publication names only from validated server IDs, and moves owned artifacts into private quarantine before committing removal. Database failure rolls back rows and restores moved files; symlinks themselves are removed without following external targets. Post-commit private trash removal is best-effort, as in batch deletion; it is not an atomic filesystem/SQLite transaction across process crashes or a secure disk-erasure guarantee. Successful deletion survives restart and old artifact URLs become unavailable. Implementation: [store](../api/refinement/store.py), [shared deletion I/O](../api/refinement/deletion.py); tests: `tests/test_refinement_sessions.py`.

The workbench and history list expose the same revision-checked deletion; history also supports selection and batch removal with individual outcomes. The row and cached detail disappear only after successful deletion (or a confirmed `404` on retry). A running record is ineligible; stale/failed rows remain and are refreshed. The open workspace, drafts and pending commands for that session are cleared, as is a matching lightweight `refinementSessionId` association; source images, local outputs, mappings and masks are not rewritten. Existing SSE subscriptions receive a `deleted` notification and close; reconnecting to a missing record returns `404`. Client behavior is covered by session workbench, cache and SSE tests under `web/src/features/refinement/`.

- SQLite: `data/refinement_sessions.sqlite3`, with sessions, turns, versions, artifact references, publications and events.
- Immutable private evidence and candidates: `outputs/refinement_staging/sessions/{session_id}/{digest}.png`. Original, baseline and references are snapshotted and hash-checked.
- Accepted files: `outputs/api_results/refinement/accepted/sessions/{session_id}-{version_id}.png`. Only explicit acceptance publishes here.

Acceptance verifies session ownership, `expected_revision`, `version_id`, `candidate_digest` and intact bytes. It rejects an active turn and stale/conflicting selections, and publishes the verified buffer without regeneration, compositing or re-encoding. A repeated acceptance of the already accepted intact version is idempotent. Any retained version, including an older branch or the baseline, can be explicitly selected; no AI score controls eligibility.

Unaccepted versions remain available for comparisons and branching after acceptance; publication does not erase session history. Publication uses a temporary private file and atomic rename, with database rollback cleanup and restart cleanup of uncommitted owned publication files. Private and accepted roots must support the same-filesystem atomic operation. Browser snapshots cannot override this server truth. Missing/corrupt bytes produce an availability error, never a reconstructed replacement. New-session artifacts are independent of original batch folders once snapshotted.

Server-owned lossless PNG snapshots use a bounded internal byte budget (`MAX_SNAPSHOT_BYTES` in `core/image_security.py`), not the external 20 MiB upload limit: a valid JPEG may expand beyond that limit when encoded losslessly. The 40-million-pixel per-image limit, memory checks, cumulative evidence budget, artifact ownership and SHA-256 checks still apply. Generation receives exactly one original-resolution decoded canvas, encoded losslessly at the gateway; external bytes/data URIs and provider responses retain the 20 MiB cap. Known creation input errors return specific safe messages, while unknown exceptions remain sanitized. Regression: `tests/test_large_refinement_snapshots.py`.

One application process owns startup recovery; this is not a distributed worker lease system. Each session permits at most 200 turns. Shared current-input limits are 24 people, 300 locator characters and 4000 message characters; references are limited to 8 per person and 64 total. `MAX_EVIDENCE_PIXELS` in `core/refinement/contracts.py` limits original, baseline and all reference occurrences to 120 million decoded pixels per creation, including repeated references even when snapshot bytes deduplicate. Each image is counted after decoding and before its snapshot is written; exceeding the cumulative budget rejects creation and removes only that attempt's unregistered snapshots, without consuming its request ID. Turn execution rechecks the same budget for original, selected base and references, including sessions without references. Oversized identity galleries require explicit selection, not silent truncation. An omitted locator is frozen from the original face index, never a new candidate detection order. A generated response enters a short cancellation-drained snapshot/register phase so graceful shutdown retains the candidate; failed or duplicate session creations remove only their own unregistered private directory.

## Legacy archive boundary

`api/refinement/legacy.py` and `api/routes/legacy_refinement.py` exist only for persisted old task retrieval, candidate/evidence access, human quality evaluation and exact-byte human acceptance. Historical statuses/fields describe that archive, not the new turn lifecycle.

Legacy `outputs/refinement_evidence/{task_id}/` contains read-only evidence, not published outputs. Publication still uses verified staged bytes, never an archive substitute; evidence collection or quality evaluation cannot accept a candidate. Successful legacy acceptance removes the old active staging folder but preserves its independent content-addressed quality evidence.

Old `POST /refinement/review`, `/refinement/plan`, `/refinement/tasks`, `/refinement/tasks/{task_id}/cancel` and `/refinement/tasks/{task_id}/people/{person_id}/retry` return `410`; they do not translate requests or generate through a shadow pipeline. Retired aliases are not silently mapped. The old core service/plan and task generator are not production fallbacks. To continue editing, create an explicit new session from verified available evidence.

`GET /api/v1/refinement/legacy-tasks?limit=&offset=` provides bounded lightweight legacy summaries for selecting an existing record; it does not expose full request/image payloads or require guessing a task ID. The legacy [human quality evidence archive](delivery-quality.md) remains separate from current session turns; it does not turn old reviews into new-session acceptance authority. Keep this archive only while persisted legacy records require it; removing it requires an explicit retention/migration decision and corresponding regression updates.

Individual archive retention is controlled by explicit `DELETE /api/v1/refinement/tasks/{task_id}?expected_revision=`. Only `REVIEW_REQUIRED`, `ACCEPTED`, `FAILED` and `CANCELLED` records at the current revision can be removed. Deletion clears the old request/snapshot, candidate, accepted file and task-owned initial/generation/candidate/evaluation quality records and evidence; it never removes new session storage. History supports single and selected-record deletion, and closes a deleted quality detail. The same private quarantine/rollback boundary applies. The reserved legacy ID `sessions` cannot be deleted because that directory belongs to the new workflow. Regression: `tests/test_legacy_refinement.py`, `tests/test_legacy_refinement_api.py` and `LegacyQualityHistory.test.tsx`.

## Verifiable evidence

Core behavior: [conversation service](../core/refinement/conversation.py), [contracts](../core/refinement/contracts.py), [prompt/evidence specification](refinement-prompts.md), [core regression](../tests/test_refinement_conversation.py).

Session behavior: [HTTP contracts](../api/refinement/contracts.py), [executor](../api/refinement/service.py), [store](../api/refinement/store.py), [routes](../api/routes/refinement_sessions.py), [batch hook](../api/refinement/batch.py). Regression suites: `tests/test_refinement_sessions.py`, `tests/test_batch_refinement.py`, `tests/test_legacy_refinement.py`, `tests/test_refinement_image_ops.py`. Gateway transport/endpoint behavior is covered by `tests/test_litellm_gateway.py`; documentation invariants by `tests/test_documentation_consistency.py`. These are implementation/contract evidence, not a claim of real paid image-quality validation.

Authority and synchronization rules: [documentation-governance.md](documentation-governance.md).

## Limited live validation

The default gateway request waits up to 300 seconds. Analysis and review each have a 330-second outer deadline, leaving room for evidence preparation and response validation. Image editing retains its 600-second deployment timeout and 900-second generation-stage deadline. These are waiting limits, not duration estimates or automatic retry settings; see [gateway configuration](../config/ai_gateway.yaml) and [session executor](../api/refinement/service.py).

The [2026-09-11 five-person test](refinement-live-test-20260911.md) records two real generated candidates, prompt/parent/digest checks, preserved review failure and manual quality findings. Both candidates remained unaccepted because of appearance drift and unresolved small occlusion details. Contract tests passing and candidates being generated do not establish visual quality.

## Restoring the editor around a session

Complete Studio projects preserve the preparation workspace together with per-session viewed/base versions, confirmed analysis, unsubmitted feedback and model/comparison/zoom/pane choices. Same-tab refresh also preserves lightweight selections; uncertain pending commands keep their original idempotency keys and never replay automatically. Opening a session remains separate from explicitly restoring its linked project. Recovery does not change server turns, acceptance or stable identity bindings. Old sessions without a complete project can recover only their retained evidence and explicit mappings, with visible missing-state warnings. Storage, ownership, conflict and deletion semantics are defined in [task history and project recovery](task-history.md#complete-studio-projects-and-explicit-recovery).

Server-confirmed acceptance is projected to the associated editor target's `refinedUrl`; the original and `processedUrl` local baseline remain intact. The preparation canvas, comparison, filmstrip and current-image download use that final result. A global observer refreshes explicitly linked sessions even outside the refinement view, so already accepted results also appear after project restoration. Session ID and target ID must both match, reprocessing targets are excluded, and older revisions cannot replace newer acceptance. Viewing a candidate or unrelated history never changes the editor result. The saved project keeps an independent image copy and a version/revision projection; this metadata does not authorize server acceptance. Implementation and regressions: `acceptedResults.ts`, `useAcceptedResults.ts`, `SessionWorkbench.test.tsx` and `acceptedResults.test.ts` under `web/src/features/refinement/`.

Batch session creation uses only actual per-image assignments. A skipped batch role with no assigned face is not bound to a guessed location and does not prevent other confirmed people from entering refinement; an inconsistent successful outcome without a face assignment still fails. Every project restore entry can reconstruct a missing creation recipe from retained explicit references and assignment outcomes after verifying both canvases (equal digests or exact decoded pixels), without rerunning local swap. Running batch input is additionally saved with its task ID so a refreshed, explicitly associated workspace can finish the same recipe when results arrive. See `api/refinement/batch.py`, `api/projects/recovery.py`, `web/src/hooks/useBatchPipeline.ts` and their regression tests.

### Availability after deletion and export verification

Confirmed deletion clears matching session-backed accepted URLs and metadata and persists an unavailable-result flag. The canvas explicitly identifies the remaining local draft; it cannot be downloaded as the accepted final. Independent project-frozen data URI copies survive deletion. Single-image and ZIP exports verify the associated session and use its current accepted publication; only a confirmed 404/410 permits a frozen image fallback. Network errors, server failures and session/target mismatches fail visibly instead of silently exporting stale acceptance. Implementation: `sessionCache.ts`, `acceptedResults.ts`, `workspaceExport.ts`; regressions: `sessionCache.test.ts`, `workspaceExport.test.ts`, `useSingleSwap.test.tsx`.

## Read-only image display and saved acceptance

Snapshot creation validates full image pixels before committing the digest. Reading or accepting that exact persisted digest verifies ownership, byte size and SHA-256 without decoding the full image again. Actual model evidence loading still decodes with the pixel/memory limits. Thus opening multiple version thumbnails does not require server-side RGB allocations per thumbnail, and low decode headroom cannot by itself hide an intact saved image. Tampered/missing artifacts remain errors; there is no replacement image or acceptance bypass. Regression: `test_history_image_read_and_accept_do_not_decode_full_canvas_under_memory_pressure` in `tests/test_refinement_sessions.py`.

The workbench lazy-loads reference/version thumbnails and offers explicit image-only reload after temporary failures. Reload never creates a turn. After explicit acceptance, the linked editor waits for project save; a failure says that server acceptance succeeded but the project still needs saving. Acceptance in the session remains authoritative.

### 分阶段证据与磁盘缩略图

精修轮次及建会话的图片冻结共享本地推理内存槽位；整个 AI 轮次持槽，上游等待时本地任务也等待，
不会重试付费请求。`DeferredImage` 仅是内部可信加载描述符，绑定摘要校验过的图片尺寸，实际
读取仍核对摘要及尺寸。分析/复评逐张加载证据；每人参考板只同时解码一张参考。生成仍只读取明确
选定的当前整图，原分辨率传入 `AIGateway`，候选落盘后释放数组再复评。实现与排队边界见
[内存调度](memory-scheduling.md)，取消读取线程结束前不会释放槽位。

会话版本返回 `thumbnail_url`，人物返回 `reference_thumbnail_urls`；对应受控
`GET /api/v1/refinement/sessions/{session_id}/artifacts/{digest}/thumbnail` 为最长边 384px JPEG。
缩略图在快照写入时生成，旧会话缺缓存时经共享队列补建；缓存有独立摘要，源图按流校验摘要和
会话归属，源图失效不得用缩略图掩盖。`.preview` 为可重建磁盘缓存，随会话目录删除，不进入
模型、接受发布或下载。完整图继续走原始 `image_url`。前端仅在连接未返回缩略图字段的旧后端时
回退原图 URL；新后端始终提供字段，缓存缺失由服务端重建。回归在 `tests/test_refinement_sessions.py`、
`tests/test_deferred_evidence.py` 与 `SessionWorkbench.test.tsx`。
