import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { BatchTaskStatus } from '@/types';

export type RecentJobStatus = BatchTaskStatus | 'UNKNOWN';

export interface RecentJob {
  taskId: string;
  targetIds: string[];
  targetNames: string[];
  status: RecentJobStatus;
  submittedAt: string;
  updatedAt: string;
  error?: string;
}

interface JobActivityState {
  jobs: RecentJob[];
  isOpen: boolean;
  setOpen: (open: boolean) => void;
  upsertJob: (job: RecentJob) => void;
  updateJob: (taskId: string, patch: Partial<RecentJob>) => void;
  removeJob: (taskId: string) => void;
  clearFinished: () => void;
}

const MAX_RECENT_JOBS = 10;

export const useJobActivityStore = create<JobActivityState>()(
  persist(
    (set) => ({
      jobs: [],
      isOpen: false,
      setOpen: (isOpen) => set({ isOpen }),
      upsertJob: (job) =>
        set((state) => ({
          jobs: [job, ...state.jobs.filter((item) => item.taskId !== job.taskId)].slice(0, MAX_RECENT_JOBS),
        })),
      updateJob: (taskId, patch) =>
        set((state) => ({
          jobs: state.jobs.map((job) =>
            job.taskId === taskId ? { ...job, ...patch, updatedAt: new Date().toISOString() } : job,
          ),
        })),
      removeJob: (taskId) => set((state) => ({ jobs: state.jobs.filter((job) => job.taskId !== taskId) })),
      clearFinished: () =>
        set((state) => ({ jobs: state.jobs.filter((job) => job.status === 'QUEUED' || job.status === 'PROCESSING') })),
    }),
    {
      name: 'personaweave-recent-jobs',
      partialize: (state) => ({ jobs: state.jobs }),
    },
  ),
);

export const isActiveJob = (job: RecentJob) => job.status === 'QUEUED' || job.status === 'PROCESSING';

/** Returns only an explicitly persisted browser job; server history is not workspace state. */
export const newestRecentJob = (jobs: RecentJob[]): RecentJob | null =>
  [...jobs].sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))[0] ?? null;

/** Only interrupted active work should reconnect to the mutable Studio workspace. */
export const newestActiveRecentJob = (jobs: RecentJob[]): RecentJob | null => newestRecentJob(jobs.filter(isActiveJob));
