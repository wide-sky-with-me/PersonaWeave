import type { RefinementTaskData } from '../types';

const CANCEL_TERMINAL_STATUSES = new Set(['CANCEL_REQUESTED', 'CANCELLED', 'FAILED']);

/** Rejects stale poll responses and preserves a locally observed cancellation transition. */
export function isNewerRefinementTask(
  current: RefinementTaskData | null,
  incoming: RefinementTaskData,
): boolean {
  if (!current || current.task_id !== incoming.task_id) return true;
  if (incoming.revision !== current.revision) return incoming.revision > current.revision;
  if (current.status === 'CANCEL_REQUESTED') {
    return CANCEL_TERMINAL_STATUSES.has(incoming.status);
  }
  return true;
}
