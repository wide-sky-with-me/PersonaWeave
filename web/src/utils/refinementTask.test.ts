import { describe, expect, it } from 'vitest';
import type { RefinementTaskData } from '../types';
import { isNewerRefinementTask } from './refinementTask';

const task = (revision: number, status: RefinementTaskData['status'] = 'REFINING'): RefinementTaskData => ({
  task_id: 'task-1', revision, status, progress: 0.5, created_at: '', people: [],
});

describe('isNewerRefinementTask', () => {
  it('rejects an older revision and accepts a newer revision', () => {
    expect(isNewerRefinementTask(task(3), task(2))).toBe(false);
    expect(isNewerRefinementTask(task(3), task(4))).toBe(true);
  });

  it('does not regress a cancellation at the same revision', () => {
    expect(isNewerRefinementTask(task(3, 'CANCEL_REQUESTED'), task(3, 'REFINING'))).toBe(false);
    expect(isNewerRefinementTask(task(3, 'CANCEL_REQUESTED'), task(3, 'CANCELLED'))).toBe(true);
  });
});
