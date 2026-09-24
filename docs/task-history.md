# Batch task history and result review

This document is the current contract for persisted batch-task history. Task-result history is a read-only projection of server-owned task state. Complete editor projects are saved separately and restored only through an explicit recovery action; viewing history must never silently replace current editing inputs.

## Data ownership

Batch tasks wait behind one batch executor before reference preparation. Detail snapshots expose optional
`waiting_reason` for memory or the current image's authorized AI turn; queue waiting does not invent progress.
Immutable request payloads are saved once rather than reserialized on each result. Memory-wait timeout preserves
completed artifacts and counts only actual attempts. See [memory scheduling](memory-scheduling.md).

- `SQLiteTaskStore` in `api/tasks/persistence.py` owns recoverable batch-task metadata and complete task snapshots.
- When inference returns zero visible swaps, a new failed result retains its real `faces_detected`, requested/swapped person indices, `person_outcomes`, similarities and model routing. It remains `FAILED`, has no processed URL and publishes no output image; it is not reported as zero detected faces unless detection actually returned zero. Failures before a pipeline result exists retain schema defaults. Historical rows with already-lost diagnostics are not retroactively reconstructed.
- Each newly successful batch result persists two durable artifacts below `outputs/api_results/{task_id}/`: a decoded original canvas (`original_NNNN.png`) and the processed output (`res_NNNN.jpg`). `ImageSwapResult` exposes only guarded `/api/v1/files/...` URLs.
- Browser `localStorage` stores only lightweight task-to-workspace correlation (`taskId`, target IDs/names, status, timestamps). It does not store image bytes and is not the authority for server status.
- Existing tasks created before durable original copies were introduced keep their historical `original_url`; review remains best-effort if that external URL still exists.

Task history remains available only while both its SQLite row and referenced output artifacts are retained. Missing artifacts remain explicit availability errors; the system never invents or reconstructs replacement images. On restart, interrupted tasks remain `FAILED` with an interruption message, but counts/progress are recomputed only from persisted results. Unprocessed images are not invented as failed attempts, and existing successful artifacts remain viewable.

## Deletion contract

`DELETE /api/v1/batch-tasks/{task_id}` permanently removes one terminal (`COMPLETED`, `PARTIAL`, or `FAILED`) task. A successful `204` removes the SQLite snapshot, in-memory task/request state, browser-local correlation after the client confirms success, and only the derived `outputs/api_results/{task_id}/` artifact directory. Unknown tasks return `404`; `QUEUED` and `PROCESSING` tasks return `409` so a running worker cannot recreate deleted state. Cleanup derives its directory from the server-owned task ID and configured output root, never from persisted image URLs, and never touches synchronous or refinement outputs.

## API read model

`GET /api/v1/task-history/batch-tasks?limit=N` returns bounded lightweight `BatchTaskSummary` rows. A summary contains status, progress, counts, timestamps, error text, and `viewable_results`; it deliberately excludes `results`, original URLs, processed URLs, request bodies, data URIs, and other image payloads.

The older `GET /api/v1/batch-tasks` complete-list response remains available as a deprecated compatibility endpoint so existing clients are not broken. New code must use the lightweight history projection plus per-task detail instead of expanding the legacy endpoint.

`GET /api/v1/batch-tasks/{task_id}` returns the complete `TaskDetailData` only after a user opens or reconnects that specific task. This summary/detail split prevents history-list cost from growing with image count or embedded inputs.

## Frontend behavior

1. Opening Task History fetches server summaries and merges browser labels by `task_id`.
2. Server status always wins. Browser metadata only supplies target names/IDs and proves that an active task belongs to this browser workspace.
3. All terminal tasks (`COMPLETED`, `PARTIAL`, `FAILED`) can open read-only details. The dialog keeps every persisted image row, including failures, with per-image errors/warnings and per-person matching/source/mask diagnostics when recorded. Only `SUCCESS`/`PARTIAL` results with an actual processed URL expose media viewing/downloading; before/after comparison additionally requires the original. A failed task may still have earlier valid artifacts; the task status does not hide them. Old records with absent diagnostics display an explicit unknown state rather than reconstructing evidence.
4. Viewing history never writes into `useStudioStore`. The separate “恢复工程” action explicitly restores a saved editor project. Only an active `QUEUED`/`PROCESSING` task with explicit browser linkage may reconnect to the mutable Studio pipeline.
5. A missing task or artifact is shown as an availability error. It must not be converted into an empty success or a reconstructed substitute.
6. Terminal rows expose an explicit delete action with irreversible confirmation. The row disappears only after the server confirms deletion; active rows cannot be deleted.

## Refinement session history

Conversational refinement has a separate server-owned aggregate in `data/refinement_sessions.sqlite3`. `GET /api/v1/refinement/sessions?limit=&offset=` returns bounded summaries without image URLs, reference arrays, full messages or generation requests. Opening one session fetches its detail and guarded artifact URLs; SSE events carry only snapshot notifications, and reconnection never submits a turn.

The session freezes original, baseline and reference images into its own content-addressed storage. Removing an original batch task therefore does not remove a successfully created session snapshot. A batch result may record `refinement_session_id`; this is an explicit navigation link, not permission to overwrite Studio sources/masks or to generate another image. Local processing status, AI turn status and human acceptance are distinct.

Opening a session can resume its editing context without mutating the local swap workspace. Viewing a historical version, selecting `base_version_id` for the next turn, and setting `accepted_version_id` are separate actions. Refresh restores server versions and turns; historical messages provide bounded branch-relevant text, not implicit old-image inputs. Explicitly selecting an older base creates a new branch. Acceptance publishes only the chosen digest and does not delete versions needed for later comparison.

Legacy single-task refinement remains an archive: `GET /api/v1/refinement/legacy-tasks?limit=&offset=` lists bounded lightweight summaries for selecting old records; old task detail/candidate/evidence, human quality evaluation and exact-byte acceptance are preserved, but old create/review/plan/cancel/retry operations return 410. These old records are not silently converted to new sessions or used to restart paid jobs. New session semantics and ownership are defined only in [ai-refinement.md](ai-refinement.md).

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
- Persistence, durable artifact writes, terminal timestamps, and summary projection: [`api/tasks/batch.py`](../api/tasks/batch.py) and [`api/tasks/persistence.py`](../api/tasks/persistence.py).
- Bounded list and detail endpoints: [`api/routes/swapping.py`](../api/routes/swapping.py).
- Server/browser merge policy: [`web/src/features/history/utils/taskHistory.ts`](../web/src/features/history/utils/taskHistory.ts).
- Read-only UI: [`web/src/features/history/components/RecentJobsPopover.tsx`](../web/src/features/history/components/RecentJobsPopover.tsx) and [`web/src/features/history/components/TaskResultHistoryDialog.tsx`](../web/src/features/history/components/TaskResultHistoryDialog.tsx).
- Regression tests: [`tests/test_api.py`](../tests/test_api.py), [`tests/test_task_restart_persistence.py`](../tests/test_task_restart_persistence.py), [`web/src/features/history/utils/taskHistory.test.ts`](../web/src/features/history/utils/taskHistory.test.ts), and [`web/src/features/history/components/RecentJobsPopover.test.tsx`](../web/src/features/history/components/RecentJobsPopover.test.tsx).

Repository-wide evidence and synchronization rules are defined in [documentation-governance.md](./documentation-governance.md).

## Deleting refinement records

The history workspace supports single and selected-record deletion for local batch tasks, refinement sessions and legacy refinement archives. Every action confirms its record/file scope first. Running records cannot be selected for deletion; a server conflict refreshes the list and retains the failed record. Selection is pruned when records disappear or become active, and successful deletion clears the corresponding detail cache. Partial batch outcomes remove only successes.

Session deletion removes the entire session, its original/reference snapshots, all versions and accepted publications, while preserving the original local batch and portrait library. The open session/draft/pending command and matching lightweight browser association are cleared after success; historical source/target/mapping/mask data never hydrate the preparation store. Legacy deletion removes only its retained candidate/publication/evidence/quality records. Both require `expected_revision` and remain usable without AI credentials. Exact endpoints, storage/rollback boundaries and regressions are defined in [the refinement contract](ai-refinement.md).

Implementation: [history workspace](../web/src/features/history/components/HistoryWorkspace.tsx), [legacy history](../web/src/features/history/components/LegacyQualityHistory.tsx), [shared selected-record deletion](../web/src/hooks/useRecordDeletion.ts). The window confirmation is user-controlled; merely opening a record or selecting checkboxes never deletes it.

## List interaction feedback

The history workspace distinguishes loading, request failure and an empty successful response. Batch status labels are shared with the detail viewer; timestamps use Chinese locale formatting in the current browser timezone. Selection controls show a mixed state for partially selected pages and are locked while a deletion is pending. Lists remain bounded summary projections; visual grouping does not change record ownership or delete authorization. Evidence: `HistoryWorkspace.tsx`, `RecordSelectionToolbar.tsx`, and the fixture-based browser audit `scripts/smoke_studio_ui.py`.

## Complete Studio projects and explicit recovery

Task results remain a read-only execution record. A separate **editor project** now saves the mutable Studio context in `data/projects.sqlite3`, with independent content-addressed image files below `outputs/api_results/projects/{project_id}/`. The project is the latest saved editing state shared by its explicitly linked task/session records, not a rewrite of the original execution recipe. Multiple task/session rows may link to one project. The first owning link is preserved; later saves do not reassign another project's records.

The workbench autosaves after editing pauses and displays dirty/saving/saved/error states. It saves target images and results, source people/references, stable original face topology and mappings, all editable parameters, per-image protection mask plus undo/redo frames, active target, brush/view settings, refinement session associations, viewed/base version choices, analysis confirmation, model/comparison/zoom/pane choices and unsubmitted feedback. Managed portrait originals remain the processing references; display thumbnails are archived separately, byte-for-byte, via SQLite opaque-ID resolution with managed-root containment checks. Restoring the project does not require the original library to remain present (see `api/projects/recovery.py` and `tests/test_projects.py`). Execution references become frozen manual references while person IDs and labels remain stable. Every required picture must upload successfully before a document commits. A failed save leaves the previous saved revision intact and keeps a visible error; it does not claim that unsaved edits are durable.

Images use digest references inside the JSON document. Uploads validate existing image byte/pixel limits, deduplicate within a project and preserve encoded bytes. Documents are limited to 4 MiB, excluding uploaded picture bytes, and reject unsupported schema versions, transient blob/data images, prototype keys and excessive nesting. Restore fetches and SHA-256-verifies all referenced images before atomically updating either editor store; a missing/corrupt image leaves the current editor unchanged. The only browser-local project metadata is the current opaque project ID. Refresh automatically loads that saved project; another browser can explicitly choose it from history. Lightweight refinement selections/pending commands additionally survive same-tab refresh in sessionStorage; pending commands are not replayed by project restore.

“查看” / opening a session still only views history. **“恢复工程”** explicitly saves the current editor first, restores the complete saved project and navigates back to editing. Current local execution blocks switching projects. Runtime flags/active task IDs, callbacks, server progress and accepted-version pointers are not hydrated from project data. Original task/session services remain the authority for progress and acceptance; recovery never starts a swap, sends an AI turn, accepts a candidate or re-detects/reorders saved faces. An interrupted PROCESSING preview is restored as idle, not as newly completed work. Active task reconnection still requires an exact existing browser-to-target association.

“新工程” saves the current editor before clearing editing inputs. Closing/reloading while a save is pending uses the browser's unsaved-change protection. Saving uses optimistic revision checks and serializes asynchronous media uploads; a stale editor receives a conflict instead of overwriting another tab's work. “另存为新工程” preserves a conflicted local editor as a separate project; refreshing reads the server copy. The UI must be allowed to finish saving before the saved-state guarantee applies. No browser crash can preserve changes that have not reached the server.

### Project endpoints

- `GET /api/v1/projects?limit=`: bounded summaries (`id/title/revision/updated_at`), no document or pictures.
- `POST /api/v1/projects`: create an upload destination; empty unsaved rows are excluded from history.
- `GET /api/v1/projects/{id}`: one complete saved editor document.
- `POST /api/v1/projects/{id}/restore`: open a saved editor and repair missing refinement inputs from its linked batch records. Project-list restore and page refresh use this command; plain GET remains read-only.
- `PUT /api/v1/projects/{id}`: revision-checked document save and explicit task/session associations.
- `POST /api/v1/projects/{id}/assets`: freeze an image or original portrait asset/identity selection. This never invokes a model.
- `GET /api/v1/projects/{id}/assets/{digest}`: owned, digest-verified image bytes.
- `POST /api/v1/projects/recover/{batch|session}/{record_id}`: open the owning project, or explicitly migrate the available old record into a new partial project.
- `DELETE /api/v1/projects/{id}?expected_revision=`: remove this project's document, links and own copies only. Task results, refinement versions and the portrait library remain. Conversely, removing task/session history does not remove independently saved project images, though a deleted session's turns/acceptance cannot be recreated from the editor snapshot.

### Older records without an editor snapshot

Old batch requests can retain parameters, mappings, references and final masks. Recovery reuses those actual fields, durable originals/results and any existing refinement link. Old refinement sessions retain original/baseline and individual reference images; these can populate a partial preparation workspace and preserve the exact stored person/face-index binding. Reference-board composites are never substituted for original portrait references. No missing face index is guessed; no automatic detector or paid request runs during migration.

For batch results without a session, recovery also reconstructs missing refinement input from the saved request's explicit content-addressed references and each result's actual `person_outcomes` face assignments. Unassigned skipped roles from other images in the batch are excluded; they do not block the confirmed people in this image. Both the original and local result must match the editor's images: equal asset digests pass directly; different encodings require exact decoded-pixel equality. Previously restored projects are repaired through all restore entries (project list, batch/session history and page refresh), preserving existing recipes, edits and drafts. Single-source requests without a requested face index use actual outcome assignments, not inferred face numbers. Missing assignments/references or historical identity membership that was not frozen remain unavailable; current library selections cannot stand in for execution evidence. Deleted batch records do not block an independent saved project. Regression: `tests/test_projects.py` and `tests/test_batch_refinement.py`.

For new running batch tasks, the editor saves `pendingRefinement` (task ID plus frozen reference input) before results arrive. On an explicitly associated active-task reconnect, only the matching task may complete that recipe with its immutable original/result URLs; it never substitutes current editor selections or starts a model request. This field records input provenance and cannot reconnect or execute a task by itself. Switching between refinement targets with no session yet is also autosaved. Regression: `BatchControls.test.tsx`, `projectController.test.ts` and `projectSnapshot.test.ts`. Pending input must reach the server before a crash or refresh for the normal saved-state guarantee to apply.

### Final workspace results and ZIP export

The preparation workspace displays each explicitly linked session's accepted result ahead of its local baseline, including after restoration. Export collects completed targets from the current editor, so it also works without an active batch. For linked sessions it reads current server acceptance before downloading: accepted publication bytes take priority; an unaccepted candidate never does. A frozen accepted project copy remains usable if the session was removed. If the selected accepted file is unavailable, that image fails with an export manifest instead of silently reverting to local output. ZIP entries preserve downloaded bytes and use the detected PNG/JPEG/WebP extension, with unique filenames. Task-history detail remains the immutable local execution record; its original result download is separate from current-editor final export. See `web/src/utils/workspaceExport.ts` and `workspaceExport.test.ts`.

Older projects can be missing a session link despite having an accepted result. During explicit restore, a missing link is repaired only when exactly one accepted session matches the target ID, normalized original/local canvas digests, and every identity-reference binding (person ID, face index and reference digests). Unreferenced bystanders do not introduce another identity binding. Existing links are not reassigned, changed canvases or references are rejected, and multiple matching accepted sessions remain unresolved. This read/repair path never accepts a version or creates a model turn. See `matching_accepted_sessions` in `api/refinement/store.py`, `restore_accepted_session_links` in `api/projects/recovery.py` and `tests/test_projects.py`.

Unavailable files, absent original requests, missing face boxes, unsaved feedback, original UI names and mask undo/redo history cannot be recovered retroactively. The restored project displays the missing-information list. Missing boxes remain an explicit empty set, not newly inferred geometry; missing parameters use current UI defaults with a warning. Available media can be edited further, and the existing AI session can still be opened. Old legacy single-task archives retain their existing read-only/acceptance boundary and are not converted into new production sessions.

Implementation: [`api/projects/store.py`](../api/projects/store.py), [`api/projects/recovery.py`](../api/projects/recovery.py), [`api/routes/projects.py`](../api/routes/projects.py), [`project controller`](../web/src/features/projects/projectController.ts), [`snapshot media`](../web/src/features/projects/projectSnapshot.ts). API input contracts remain in [`api/schemas.py`](../api/schemas.py). Tests: [`test_projects.py`](../tests/test_projects.py), `projectSnapshot.test.ts`, `projectController.test.ts` and the non-production browser recovery audit `scripts/smoke_project_recovery.py`.
