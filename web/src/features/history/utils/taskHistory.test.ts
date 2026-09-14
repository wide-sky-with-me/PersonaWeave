import { describe, expect, it } from 'vitest';
import type { RecentJob } from '@/stores/useJobActivityStore';
import type { BatchTaskSummary } from '@/types';
import { canReviewTaskResults, mergeTaskHistory } from './taskHistory';

const browserJob: RecentJob = {
  taskId: 'shared',
  targetIds: ['target-1'],
  targetNames: ['group.jpg'],
  status: 'PROCESSING',
  submittedAt: '2026-08-31T00:00:00Z',
  updatedAt: '2026-08-31T00:00:01Z',
};

const serverTask: BatchTaskSummary = {
  task_id: 'shared',
  status: 'COMPLETED',
  progress: 1,
  total_images: 1,
  processed_images: 1,
  success_count: 1,
  failure_count: 0,
  has_partial_failure: false,
  viewable_results: 1,
  created_at: '2026-08-31T00:00:00Z',
  completed_at: '2026-08-31T00:00:05Z',
};

describe('task history projection', () => {
  it('uses persisted server state while preserving browser-only target labels', () => {
    const [entry] = mergeTaskHistory([serverTask], [browserJob]);

    expect(entry.status).toBe('COMPLETED');
    expect(entry.targetNames).toEqual(['group.jpg']);
    expect(entry.viewableResults).toBe(1);
    expect(entry.browserLinked).toBe(true);
    expect(canReviewTaskResults(entry)).toBe(true);
  });

  it('shows server tasks created by another browser without inventing workspace linkage', () => {
    const [entry] = mergeTaskHistory([{ ...serverTask, task_id: 'server-only' }], []);

    expect(entry.targetNames).toEqual([]);
    expect(entry.browserLinked).toBe(false);
    expect(canReviewTaskResults(entry)).toBe(true);
  });

  it('keeps an unreported local active task available for reconnect', () => {
    const [entry] = mergeTaskHistory([], [browserJob]);

    expect(entry.taskId).toBe('shared');
    expect(entry.status).toBe('PROCESSING');
    expect(entry.browserLinked).toBe(true);
    expect(canReviewTaskResults(entry)).toBe(false);
  });
  it.each(['COMPLETED', 'PARTIAL', 'FAILED'] as const)('opens every terminal status including %s', (status) => {
    const [entry] = mergeTaskHistory([{ ...serverTask, status, viewable_results: 0 }], []);
    expect(canReviewTaskResults(entry)).toBe(true);
  });
});
