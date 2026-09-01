import type { RecentJob, RecentJobStatus } from '../stores/useJobActivityStore';
import type { BatchTaskSummary } from '../types';

export interface TaskHistoryEntry {
  taskId: string;
  status: RecentJobStatus;
  targetIds: string[];
  targetNames: string[];
  totalImages: number;
  successCount: number;
  failureCount: number;
  viewableResults?: number;
  createdAt: string;
  updatedAt: string;
  error?: string;
  browserLinked: boolean;
}

/**
 * Server persistence owns task truth; browser metadata only enriches names and
 * enables reconnecting a task to the exact workspace that submitted it.
 */
export const mergeTaskHistory = (
  serverTasks: BatchTaskSummary[],
  browserJobs: RecentJob[],
): TaskHistoryEntry[] => {
  const browserById = new Map(browserJobs.map((job) => [job.taskId, job]));
  const entries: TaskHistoryEntry[] = serverTasks.map((task) => {
    const browser = browserById.get(task.task_id);
    return {
      taskId: task.task_id,
      status: task.status,
      targetIds: browser?.targetIds ?? [],
      targetNames: browser?.targetNames ?? [],
      totalImages: task.total_images,
      successCount: task.success_count,
      failureCount: task.failure_count,
      viewableResults: task.viewable_results,
      createdAt: task.created_at,
      updatedAt: task.completed_at ?? task.created_at,
      error: task.error,
      browserLinked: Boolean(browser),
    } satisfies TaskHistoryEntry;
  });
  const serverIds = new Set(serverTasks.map((task) => task.task_id));
  browserJobs.forEach((job) => {
    if (serverIds.has(job.taskId)) return;
    entries.push({
      taskId: job.taskId,
      status: job.status,
      targetIds: job.targetIds,
      targetNames: job.targetNames,
      totalImages: job.targetIds.length,
      successCount: 0,
      failureCount: 0,
      createdAt: job.submittedAt,
      updatedAt: job.updatedAt,
      error: job.error,
      browserLinked: true,
    });
  });
  return entries.sort((left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt));
};

export const canReviewTaskResults = (entry: TaskHistoryEntry): boolean =>
  entry.status === 'COMPLETED' || entry.status === 'PARTIAL';
