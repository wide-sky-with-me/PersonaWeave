# Batch task history and result review

This document is the current contract for persisted batch-task history. History is a read-only projection of server-owned task state; it is not a saved Studio workspace and must never silently replace the user's current sources, targets, mappings, masks, or refinement state.

## Data ownership

- `SQLiteTaskStore` in `api/task_persistence.py` owns recoverable batch-task metadata and complete task snapshots.
- Each newly successful batch result persists two durable artifacts below `outputs/api_results/{task_id}/`: a decoded original canvas (`original_NNNN.png`) and the processed output (`res_NNNN.jpg`). `ImageSwapResult` exposes only guarded `/api/v1/files/...` URLs.
- Browser `localStorage` stores only lightweight task-to-workspace correlation (`taskId`, target IDs/names, status, timestamps). It does not store image bytes and is not the authority for server status.
- Existing tasks created before durable original copies were introduced keep their historical `original_url`; review remains best-effort if that external URL still exists.

Task history remains available only while both its SQLite row and referenced output artifacts are retained. Deletion/retention policy is a separate administrative feature; the UI must report missing records or files rather than inventing a result.

## API read model

`GET /api/v1/task-history/batch-tasks?limit=N` returns bounded lightweight `BatchTaskSummary` rows. A summary contains status, progress, counts, timestamps, error text, and `viewable_results`; it deliberately excludes `results`, original URLs, processed URLs, request bodies, data URIs, and other image payloads.

The older `GET /api/v1/batch-tasks` complete-list response remains available as a deprecated compatibility endpoint so existing clients are not broken. New code must use the lightweight history projection plus per-task detail instead of expanding the legacy endpoint.

`GET /api/v1/batch-tasks/{task_id}` returns the complete `TaskDetailData` only after a user opens or reconnects that specific task. This summary/detail split prevents history-list cost from growing with image count or embedded inputs.

## Frontend behavior

1. Opening Task History fetches server summaries and merges browser labels by `task_id`.
2. Server status always wins. Browser metadata only supplies target names/IDs and proves that an active task belongs to this browser workspace.
3. `COMPLETED` and `PARTIAL` tasks can open the read-only result dialog. The dialog fetches one detail record, supports per-result selection, before/after comparison, 50%–300% zoom, opening, and downloading.
4. Viewing history never writes into `useStudioStore`. Only an active `QUEUED`/`PROCESSING` task with explicit browser linkage may reconnect to the mutable Studio pipeline.
5. A missing task or artifact is shown as an availability error. It must not be converted into an empty success or a reconstructed substitute.

## Engineering rationale

This feature applies reusable project rules for future Agent development:

- separate list projections from heavyweight detail aggregates;
- keep durable server state authoritative and treat browser persistence as optional enrichment;
- preserve artifacts needed for later review at the moment a result is committed;
- isolate read-only history from mutable editor recovery;
- make compatibility behavior explicit instead of migrating old records by guessing;
- test payload boundaries, not only happy-path rendering.

## Verifiable evidence

- Summary schema and detail contract: [`api/schemas.py`](../api/schemas.py).
- Persistence, durable artifact writes, terminal timestamps, and summary projection: [`api/task_manager.py`](../api/task_manager.py) and [`api/task_persistence.py`](../api/task_persistence.py).
- Bounded list and detail endpoints: [`api/app.py`](../api/app.py).
- Server/browser merge policy: [`web/src/utils/taskHistory.ts`](../web/src/utils/taskHistory.ts).
- Read-only UI: [`web/src/components/layout/RecentJobsPopover.tsx`](../web/src/components/layout/RecentJobsPopover.tsx) and [`web/src/components/layout/TaskResultHistoryDialog.tsx`](../web/src/components/layout/TaskResultHistoryDialog.tsx).
- Regression tests: [`tests/test_api.py`](../tests/test_api.py), [`tests/test_task_restart_persistence.py`](../tests/test_task_restart_persistence.py), [`web/src/utils/taskHistory.test.ts`](../web/src/utils/taskHistory.test.ts), and [`web/src/components/layout/RecentJobsPopover.test.tsx`](../web/src/components/layout/RecentJobsPopover.test.tsx).

Repository-wide evidence and synchronization rules are defined in [documentation-governance.md](./documentation-governance.md).
