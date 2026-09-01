export interface RecentRefinementTask {
  taskId: string;
  targetId: string;
  updatedAt: string;
}

const STORAGE_KEY = 'personaweave-recent-refinement-tasks';
const MAX_ENTRIES = 10;

export function readRecentRefinementTasks(): RecentRefinementTask[] {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]');
    return Array.isArray(value)
      ? value.filter((item): item is RecentRefinementTask =>
          typeof item?.taskId === 'string' && typeof item?.targetId === 'string' && typeof item?.updatedAt === 'string')
      : [];
  } catch {
    return [];
  }
}

export function linkRecentRefinementTask(taskId: string, targetId: string): void {
  const entry = { taskId, targetId, updatedAt: new Date().toISOString() };
  const next = [entry, ...readRecentRefinementTasks().filter((item) => item.taskId !== taskId && item.targetId !== targetId)]
    .slice(0, MAX_ENTRIES);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
}

export function unlinkRecentRefinementTask(taskId: string): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(readRecentRefinementTasks().filter((item) => item.taskId !== taskId)));
}

export function refinementTaskForTarget(targetId: string): string | null {
  return readRecentRefinementTasks().find((item) => item.targetId === targetId)?.taskId ?? null;
}
