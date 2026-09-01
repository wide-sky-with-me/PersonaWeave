import { beforeEach, describe, expect, it, vi } from 'vitest';
import { linkRecentRefinementTask, readRecentRefinementTasks, refinementTaskForTarget, unlinkRecentRefinementTask } from './refinementRecovery';

describe('refinement recovery metadata', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-01-01T00:00:00Z'));
  });

  it('persists only lightweight task-to-target linkage', () => {
    linkRecentRefinementTask('refine-1', 'target-1');
    expect(refinementTaskForTarget('target-1')).toBe('refine-1');
    expect(localStorage.getItem('personaweave-recent-refinement-tasks')).toBe(
      '[{"taskId":"refine-1","targetId":"target-1","updatedAt":"2025-01-01T00:00:00.000Z"}]',
    );
  });

  it('replaces stale target linkage and clears 404 entries', () => {
    linkRecentRefinementTask('old', 'target-1');
    linkRecentRefinementTask('new', 'target-1');
    expect(readRecentRefinementTasks().map((item) => item.taskId)).toEqual(['new']);
    unlinkRecentRefinementTask('new');
    expect(refinementTaskForTarget('target-1')).toBeNull();
  });
});
