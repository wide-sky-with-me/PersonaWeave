# Batch task history and result review

Current contract for persisted batch-task history. Task-result history is a read-only projection of server-owned task state. Complete editor projects are saved separately ([studio-projects.md](studio-projects.md)) and restored only through an explicit recovery action; viewing history never silently replaces current editing inputs.

## Data ownership

- `SQLiteTaskStore` in `api/tasks/persistence.py` owns recoverable batch-task metadata and complete task snapshots.
- Batch tasks wait behind one batch executor before reference preparation. Detail snapshots expose an optional `waiting_reason` (memory, or the current image's authorized AI turn); queue waiting never invents progress. Memory-wait timeout keeps completed artifacts and counts only actual attempts ([memory-scheduling.md](memory-scheduling.md)).
- Immutable request payloads are saved once, not reserialized per result. Queued workers hold only task IDs; the request is loaded at execution and released afterwards. Startup reads states incrementally without historical requests; explicit recovery and provenance load one request on demand. New failures omit inline originals from result metadata (inputs stay in the durable request); failed rows that already carry inline originals keep them in SQLite and hydrate only for detail/recovery, including rows with no request snapshot.
- A worker that dies while reading its request or during initial persistence turns the task `FAILED`, keeps completed items and releases the batch queue; no task stays `QUEUED` without an executor. Regression: `tests/test_batch_memory_queue.py::test_recipe_load_failure_terminates_dead_queued_task_and_releases_next`.
- When inference returns zero visible swaps, a new failed result keeps its real `faces_detected`, requested/swapped person indices, `person_outcomes`, similarities and model routing. It stays `FAILED`, has no processed URL and publishes no image; it reports zero detected faces only when detection actually returned zero. Failures before a pipeline result keep schema defaults; rows whose diagnostics are already gone are not reconstructed.
- Each new successful result persists two artifacts under `outputs/api_results/{task_id}/`: the decoded original canvas (`original_NNNN.png`) and the processed output (`res_NNNN.jpg`). `ImageSwapResult` exposes only guarded `/api/v1/files/...` URLs. Rows created before durable original copies keep their `original_url`; review is best-effort while that external URL exists.
- Browser `localStorage` keeps only lightweight task-to-workspace correlation (`taskId`, target IDs/names, status, timestamps): no image bytes, and never the authority for server status.

History is available only while both the SQLite row and the referenced artifacts are retained. Missing artifacts are explicit availability errors; nothing is reconstructed. After a restart, interrupted tasks stay `FAILED` with an interruption message, counts and progress are recomputed from persisted results only, unprocessed images are not invented as failed attempts, and successful artifacts stay viewable.

## Deletion

`DELETE /api/v1/batch-tasks/{task_id}` permanently removes one terminal (`COMPLETED`, `PARTIAL`, `FAILED`) task. `204` removes the SQLite snapshot, in-memory task/request state, the browser correlation (after the client confirms success) and only the derived `outputs/api_results/{task_id}/` directory. Unknown tasks return `404`; `QUEUED`/`PROCESSING` return `409` so a running worker cannot recreate deleted state. The directory is derived from the server-owned task ID and configured output root, never from persisted image URLs, and synchronous or refinement outputs are never touched.

## API read model

`GET /api/v1/task-history/batch-tasks?limit=N` returns bounded lightweight `BatchTaskSummary` rows: status, progress, counts, timestamps, error text and `viewable_results`, deliberately without `results`, original/processed URLs, request bodies, data URIs or other image payloads. `GET /api/v1/batch-tasks/{task_id}` returns the complete `TaskDetailData` only when a user opens or reconnects that task, so list cost does not grow with image count. The complete-list `GET /api/v1/batch-tasks` is a deprecated compatibility endpoint; new code uses the summary projection plus per-task detail.

## Frontend behavior

1. Opening Task History fetches server summaries and merges browser labels by `task_id`; server status always wins, and browser metadata only supplies target names/IDs and proves that an active task belongs to this workspace.
2. All terminal tasks can open read-only details. The dialog keeps every persisted image row, including failures, with per-image errors/warnings and per-person matching/source/mask diagnostics when recorded; absent diagnostics show an explicit unknown state. Only `SUCCESS`/`PARTIAL` results with a processed URL expose viewing/downloading, and before/after comparison also requires the original. A failed task may still hold valid earlier artifacts.
3. Viewing history never writes into `useStudioStore`. The separate “恢复工程” action restores a saved editor project; only an active `QUEUED`/`PROCESSING` task with explicit browser linkage may reconnect to the mutable Studio pipeline.
4. A missing task or artifact is an availability error, never an empty success or a reconstructed substitute.
5. Terminal rows expose a delete action with irreversible confirmation; a row disappears only after the server confirms deletion, and active rows cannot be deleted.

The history workspace also lists refinement sessions and legacy archives. Single and selected-record deletion confirms its scope first; running records cannot be selected; a server conflict refreshes the list and keeps the record; selection is pruned when records disappear or turn active; successful deletion clears the detail cache; partial batch outcomes remove only successes. The list distinguishes loading, request failure and an empty success; selection controls show a mixed state and lock while a deletion is pending. Implementation: [history workspace](../web/src/features/history/components/HistoryWorkspace.tsx), [legacy history](../web/src/features/history/components/LegacyQualityHistory.tsx), [selected-record deletion](../web/src/hooks/useRecordDeletion.ts). Merely opening a record or ticking a checkbox never deletes it.

## Complete Studio projects and explicit recovery

Editor project saving, recovery and export are defined in [studio-projects.md](studio-projects.md).

## Refinement session history

Refinement sessions are a separate server-owned aggregate; their routes, storage, deletion and limits are in [refinement-session-api.md](refinement-session-api.md) and their semantics in [ai-refinement.md](ai-refinement.md). History pages use `limit/offset` against the server rather than paginating the newest 50 in memory, and full versions stay detail-only. A session freezes original, baseline and references into its own content-addressed storage, so removing the original batch task does not remove a successfully created snapshot. A batch result may record `refinement_session_id`: an explicit navigation link, not permission to overwrite Studio sources/masks or to generate another image; local processing, AI turn status and human acceptance stay distinct. Opening a session does not mutate the local swap workspace; viewing a version, selecting `base_version_id` and setting `accepted_version_id` are separate actions.

The legacy single-task archive (`GET /api/v1/refinement/legacy-tasks`) is described in [delivery-quality.md](delivery-quality.md); old records are never silently converted into sessions or used to restart paid jobs.

## Verifiable evidence

- Summary schema and detail contract: [`api/schemas.py`](../api/schemas.py); persistence, artifact writes, terminal timestamps and projection: [`api/tasks/batch.py`](../api/tasks/batch.py), [`api/tasks/persistence.py`](../api/tasks/persistence.py); endpoints: [`api/routes/swapping.py`](../api/routes/swapping.py).
- Merge policy and UI: [`taskHistory.ts`](../web/src/features/history/utils/taskHistory.ts), [`RecentJobsPopover.tsx`](../web/src/features/history/components/RecentJobsPopover.tsx), [`TaskResultHistoryDialog.tsx`](../web/src/features/history/components/TaskResultHistoryDialog.tsx); fixture-based browser audit `scripts/smoke_studio_ui.py`.
- Regression: [`tests/test_api.py`](../tests/test_api.py), [`tests/test_task_restart_persistence.py`](../tests/test_task_restart_persistence.py), `taskHistory.test.ts`, `RecentJobsPopover.test.tsx`, `HistoryWorkspace.test.tsx`.

Design rationale and the rejected alternatives: [decision note](../.agents/notes/platform/20261004-history-is-a-read-only-projection.md). Repository-wide rules: [documentation-governance.md](./documentation-governance.md).
