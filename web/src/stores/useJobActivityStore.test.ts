import { beforeEach, describe, expect, it } from 'vitest';
import { newestActiveRecentJob, newestRecentJob, useJobActivityStore } from './useJobActivityStore';

const makeJob = (taskId: string, status: 'PROCESSING' | 'COMPLETED' = 'PROCESSING') => ({
  taskId,
  targetIds: ['target-1'],
  targetNames: ['portrait.jpg'],
  status,
  submittedAt: '2025-01-01T00:00:00.000Z',
  updatedAt: '2025-01-01T00:00:00.000Z',
});

describe('useJobActivityStore', () => {
  beforeEach(() => {
    localStorage.clear();
    useJobActivityStore.setState({ jobs: [], isOpen: false });
  });

  it('stores lightweight metadata without image payloads', () => {
    useJobActivityStore.getState().upsertJob(makeJob('batch-1'));
    const stored = localStorage.getItem('personaweave-recent-jobs') ?? '';
    expect(stored).toContain('batch-1');
    expect(stored).toContain('portrait.jpg');
    expect(stored).not.toContain('base64');
    expect(stored).not.toContain('data:image');
  });

  it('updates existing jobs instead of duplicating them', () => {
    const store = useJobActivityStore.getState();
    store.upsertJob(makeJob('batch-1'));
    useJobActivityStore.getState().updateJob('batch-1', { status: 'COMPLETED' });
    expect(useJobActivityStore.getState().jobs).toHaveLength(1);
    expect(useJobActivityStore.getState().jobs[0].status).toBe('COMPLETED');
  });

  it('clears finished jobs but keeps active recovery entries', () => {
    useJobActivityStore.getState().upsertJob(makeJob('active'));
    useJobActivityStore.getState().upsertJob(makeJob('done', 'COMPLETED'));
    useJobActivityStore.getState().clearFinished();
    expect(useJobActivityStore.getState().jobs.map((job) => job.taskId)).toEqual(['active']);
  });


  it('does not invent a recoverable job after storage is cleared', () => {
    expect(useJobActivityStore.getState().jobs).toEqual([]);
  });

  it('returns no recovery job for an untouched browser workspace', () => {
    expect(newestRecentJob([])).toBeNull();
  });

  it('recovers only the newest explicitly persisted browser job', () => {
    const older = makeJob('older');
    const newer = { ...makeJob('newer'), updatedAt: '2025-01-02T00:00:00.000Z' };
    expect(newestRecentJob([older, newer])?.taskId).toBe('newer');
  });

  it('does not reconnect a completed history entry into the active workspace', () => {
    const completed = makeJob('completed', 'COMPLETED');
    expect(newestActiveRecentJob([completed])).toBeNull();
  });
});
