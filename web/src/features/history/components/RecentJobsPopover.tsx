import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, CheckCircle2, Clock, Eye, Loader2, RefreshCw, RotateCw, Trash2, X } from 'lucide-react';
import { api } from '@/services/api';
import type { BatchTaskSummary } from '@/types';
import { useJobActivityStore } from '@/stores/useJobActivityStore';
import { useStudioStore } from '@/stores/useStudioStore';
import { canReviewTaskResults, mergeTaskHistory } from '@/features/history/utils/taskHistory';
import { TaskResultHistoryDialog } from './TaskResultHistoryDialog';

const HISTORY_PAGE_SIZE = 50;
const HISTORY_STALE_MS = 5_000;
const ACTIVE_HISTORY_REFRESH_MS = 2_000;

export const RecentJobsPopover: React.FC = () => {
  const { jobs, isOpen, setOpen, removeJob } = useJobActivityStore();
  const { setActiveTaskId, setIsProcessing } = useStudioStore();
  const [selectedTask, setSelectedTask] = useState<{ taskId: string; targetNames: string[] } | null>(null);
  const queryClient = useQueryClient();
  const historyQuery = useQuery({
    queryKey: ['batch-task-history'],
    queryFn: () => api.listBatchTaskHistory(HISTORY_PAGE_SIZE),
    enabled: isOpen,
    staleTime: HISTORY_STALE_MS,
    refetchInterval: (query) =>
      query.state.data?.some((task) => task.status === 'QUEUED' || task.status === 'PROCESSING')
        ? ACTIVE_HISTORY_REFRESH_MS
        : false,
  });
  const deleteMutation = useMutation({
    mutationFn: (taskId: string) => api.deleteBatchTask(taskId),
    onSuccess: (_data, taskId) => {
      removeJob(taskId);
      queryClient.removeQueries({ queryKey: ['batch-task-history-detail', taskId] });
      queryClient.setQueryData<BatchTaskSummary[]>(['batch-task-history'], (current) =>
        current?.filter((task) => task.task_id !== taskId),
      );
      setSelectedTask((current) => (current?.taskId === taskId ? null : current));
    },
  });
  const requestDelete = (taskId: string) => {
    if (!window.confirm('删除这条任务记录及其原图、结果图？此操作无法撤销；已保存的工程副本会保留。')) return;
    deleteMutation.mutate(taskId);
  };
  const history = useMemo(() => mergeTaskHistory(historyQuery.data ?? [], jobs), [historyQuery.data, jobs]);

  if (!isOpen && !selectedTask) return null;
  const reopen = (taskId: string) => {
    const browserJob = jobs.find((job) => job.taskId === taskId);
    if (!browserJob || !browserJob.targetIds.length) return;
    setActiveTaskId(taskId);
    setIsProcessing(true);
    setOpen(false);
  };

  return (
    <>
      {isOpen && (
        <section
          role="dialog"
          aria-label="最近任务"
          className="absolute right-4 top-10 z-[80] mt-2 w-[min(30rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface-2 p-3 shadow-2xl"
        >
          <div className="flex items-start justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold">任务历史</h2>
              <p className="mt-0.5 text-2xs text-zinc-500">服务端持久化记录；打开时再读取完整图片结果</p>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="btn btn-ghost btn-sm p-1.5"
                onClick={() => void historyQuery.refetch()}
                disabled={historyQuery.isFetching}
                aria-label="刷新任务历史"
              >
                {historyQuery.isFetching ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
              </button>
              <button className="btn btn-ghost btn-sm p-1.5" aria-label="关闭最近任务" onClick={() => setOpen(false)}>
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
          {historyQuery.isError && (
            <div role="alert" className="mt-3 rounded-lg border border-err/30 bg-err/10 px-3 py-2 text-xs text-err">
              服务端任务历史暂时不可用，下面仅显示本机保存的任务关联。
            </div>
          )}
          {deleteMutation.isError && (
            <div role="alert" className="mt-3 rounded-lg border border-err/30 bg-err/10 px-3 py-2 text-xs text-err">
              任务删除失败；活动任务需等待完成后再删除。
            </div>
          )}
          <div className="mt-3 max-h-[28rem] space-y-2 overflow-y-auto">
            {history.length === 0 && !historyQuery.isLoading && (
              <p className="rounded-lg border border-dashed border-border p-5 text-center text-xs text-zinc-500">
                暂无任务记录
              </p>
            )}
            {historyQuery.isLoading && !history.length && (
              <p
                role="status"
                className="flex items-center justify-center gap-2 rounded-lg border border-dashed border-border p-5 text-xs text-zinc-500"
              >
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                正在读取任务历史…
              </p>
            )}
            {history.map((entry) => {
              const active = entry.status === 'QUEUED' || entry.status === 'PROCESSING';
              const completed = entry.status === 'COMPLETED';
              const partial = entry.status === 'PARTIAL';
              const Icon = active ? Clock : completed || partial ? CheckCircle2 : AlertCircle;
              const targetLabel = entry.targetNames.join(', ') || `${entry.totalImages} 张图片`;
              return (
                <article key={entry.taskId} className="rounded-lg border border-border bg-surface-1 p-3">
                  <div className="flex items-start gap-2">
                    <Icon
                      className={`mt-0.5 h-4 w-4 shrink-0 ${active ? 'text-warn' : completed ? 'text-ok' : partial ? 'text-warn' : 'text-err'}`}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex justify-between gap-2">
                        <span className="truncate font-mono text-xs text-zinc-300" title={entry.taskId}>
                          {entry.taskId}
                        </span>
                        <span className={`chip ${completed ? 'chip-ok' : partial ? 'chip-warn' : 'chip-neutral'}`}>
                          {entry.status}
                        </span>
                      </div>
                      <p className="mt-1 truncate text-xs text-zinc-400" title={targetLabel}>
                        {targetLabel}
                      </p>
                      <p className="mt-1 text-2xs text-zinc-600">
                        {new Date(entry.updatedAt).toLocaleString()}
                        {entry.viewableResults !== undefined ? ` · ${entry.viewableResults} 个可查看结果` : ''}
                      </p>
                      {entry.error && <p className="mt-2 text-xs text-err">{entry.error}</p>}
                    </div>
                  </div>
                  <div className="mt-2 flex justify-end gap-1">
                    {!active && (
                      <button
                        className="btn btn-ghost btn-sm text-zinc-500 hover:text-err"
                        disabled={deleteMutation.isPending}
                        onClick={() => requestDelete(entry.taskId)}
                        aria-label={`删除任务 ${entry.taskId}`}
                      >
                        <Trash2 className="h-3 w-3" />
                        删除
                      </button>
                    )}
                    {active && entry.browserLinked && (
                      <button className="btn btn-outline btn-sm" onClick={() => reopen(entry.taskId)}>
                        <RotateCw className="h-3 w-3" />
                        重新连接
                      </button>
                    )}
                    {canReviewTaskResults(entry) && (
                      <button
                        className="btn btn-primary btn-sm"
                        onClick={() => {
                          setSelectedTask({ taskId: entry.taskId, targetNames: entry.targetNames });
                          setOpen(false);
                        }}
                      >
                        <Eye className="h-3 w-3" />
                        查看结果
                      </button>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      )}
      <TaskResultHistoryDialog
        taskId={selectedTask?.taskId ?? null}
        targetNames={selectedTask?.targetNames}
        onClose={() => setSelectedTask(null)}
        onDelete={requestDelete}
        isDeleting={deleteMutation.isPending}
      />
    </>
  );
};
