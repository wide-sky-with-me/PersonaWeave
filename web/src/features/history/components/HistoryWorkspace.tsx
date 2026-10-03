import { useEffect, useState } from 'react';
import { LegacyQualityHistory } from './LegacyQualityHistory';
import { RecordSelectionToolbar } from './RecordSelectionToolbar';
import { useSessionDeletion } from '@/features/refinement/hooks/useSessionDeletion';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Loader2, Trash2, RefreshCw } from 'lucide-react';
import { taskStatusLabels } from '../utils/taskHistory';
import { api } from '@/services/api';
import { sessionApi } from '@/features/refinement/sessionApi';
import { useWorkbenchStore } from '@/features/refinement/workbenchStore';
import { useJobActivityStore } from '@/stores/useJobActivityStore';
import { sessionsKey } from '@/features/refinement/hooks/useSessionWorkbench';
import type { BatchTaskSummary } from '@/types';
import { TaskResultHistoryDialog } from './TaskResultHistoryDialog';
import { ProjectHistory, RestoreProjectButton } from '@/features/projects/ProjectHistory';

const PAGE_SIZE = 10;

/** History reads server summaries only; opening it never hydrates the preparation store. */
export function HistoryWorkspace() {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [taskPage, setTaskPage] = useState(1);
  const [sessionPage, setSessionPage] = useState(1);
  const [selectedTaskIds, setSelectedTaskIds] = useState<Set<string>>(new Set());
  const [batchDeleteError, setBatchDeleteError] = useState<string | null>(null);

  const queryClient = useQueryClient();
  const removeJob = useJobActivityStore((state) => state.removeJob);

  const history = useQuery({ queryKey: ['batch-task-history'], queryFn: () => api.listBatchTaskHistory() });
  const sessions = useQuery({
    queryKey: [...sessionsKey, 'history', sessionPage],
    queryFn: () => sessionApi.list(PAGE_SIZE + 1, (sessionPage - 1) * PAGE_SIZE),
  });
  const open = useWorkbenchStore((state) => state.openSession);

  const allTasks = history.data ?? [];
  const totalTasks = allTasks.length;
  const totalTaskPages = Math.max(1, Math.ceil(totalTasks / PAGE_SIZE));
  const validTaskPage = Math.min(taskPage, totalTaskPages);

  useEffect(() => {
    if (taskPage > totalTaskPages) {
      setTaskPage(Math.max(1, totalTaskPages));
    }
  }, [taskPage, totalTaskPages]);

  useEffect(() => {
    const eligible = new Set(
      allTasks.filter((task) => task.status !== 'QUEUED' && task.status !== 'PROCESSING').map((task) => task.task_id),
    );
    setSelectedTaskIds((current) => {
      const next = new Set([...current].filter((id) => eligible.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [history.data]);

  const currentTasks = allTasks.slice((validTaskPage - 1) * PAGE_SIZE, validTaskPage * PAGE_SIZE);
  const terminalTasksOnPage = currentTasks.filter((t) => t.status !== 'QUEUED' && t.status !== 'PROCESSING');
  const allPageTerminalSelected =
    terminalTasksOnPage.length > 0 && terminalTasksOnPage.every((t) => selectedTaskIds.has(t.task_id));

  const allSessions = sessions.data ?? [];
  const sessionDeletion = useSessionDeletion(allSessions);
  const totalSessions = allSessions.length;
  const hasNextSessionPage = totalSessions > PAGE_SIZE;
  useEffect(() => {
    if (sessions.isSuccess && !totalSessions && sessionPage > 1) setSessionPage((p) => p - 1);
  }, [sessions.isSuccess, totalSessions, sessionPage]);
  const currentSessions = allSessions.slice(0, PAGE_SIZE);

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteBatchTask(id),
    onSuccess: (_data, deletedTaskId) => {
      removeJob(deletedTaskId);
      queryClient.removeQueries({ queryKey: ['batch-task-history-detail', deletedTaskId] });
      queryClient.setQueryData<BatchTaskSummary[]>(['batch-task-history'], (current) =>
        current?.filter((task) => task.task_id !== deletedTaskId),
      );
      setSelectedTaskIds((prev) => {
        if (!prev.has(deletedTaskId)) return prev;
        const next = new Set(prev);
        next.delete(deletedTaskId);
        return next;
      });
      if (taskId === deletedTaskId) {
        setTaskId(null);
      }
    },
  });

  const batchDeleteMutation = useMutation({
    mutationFn: async (taskIds: string[]) => {
      const results = await Promise.allSettled(taskIds.map((id) => api.deleteBatchTask(id)));
      const succeeded: string[] = [];
      const failed: string[] = [];
      results.forEach((res, idx) => {
        if (res.status === 'fulfilled') {
          succeeded.push(taskIds[idx]);
        } else {
          failed.push(taskIds[idx]);
        }
      });
      return { succeeded, failed };
    },
    onSuccess: ({ succeeded, failed }) => {
      succeeded.forEach((id) => {
        removeJob(id);
        queryClient.removeQueries({ queryKey: ['batch-task-history-detail', id] });
      });
      queryClient.setQueryData<BatchTaskSummary[]>(['batch-task-history'], (current) =>
        current?.filter((task) => !succeeded.includes(task.task_id)),
      );
      setSelectedTaskIds((prev) => {
        const next = new Set(prev);
        succeeded.forEach((id) => next.delete(id));
        return next;
      });
      if (taskId && succeeded.includes(taskId)) {
        setTaskId(null);
      }
      if (failed.length > 0) {
        setBatchDeleteError(`${failed.length} 条任务删除失败（可能活动中或已被清理）`);
      } else {
        setBatchDeleteError(null);
      }
    },
    onError: (err) => {
      setBatchDeleteError('批量删除失败：' + (err instanceof Error ? err.message : String(err)));
    },
  });

  const handleDelete = (id: string) => {
    if (!window.confirm('删除这条任务记录及其原图、结果图？此操作无法撤销；已保存的工程副本会保留。')) return;
    deleteMutation.mutate(id);
  };

  const handleBatchDelete = () => {
    const count = selectedTaskIds.size;
    if (count === 0) return;
    if (
      !window.confirm(
        `确定要批量删除选中的 ${count} 条任务记录及其原图、结果图？此操作无法撤销；已保存的工程副本会保留。`,
      )
    )
      return;
    batchDeleteMutation.mutate(Array.from(selectedTaskIds));
  };

  const handleToggleSelectAll = (checked: boolean) => {
    setSelectedTaskIds((prev) => {
      const next = new Set(prev);
      terminalTasksOnPage.forEach((task) => {
        if (checked) {
          next.add(task.task_id);
        } else {
          next.delete(task.task_id);
        }
      });
      return next;
    });
  };

  const toggleSelect = (id: string, checked: boolean) => {
    setSelectedTaskIds((prev) => {
      const next = new Set(prev);
      if (checked) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  };

  const isMutating = deleteMutation.isPending || batchDeleteMutation.isPending;

  return (
    <section aria-label="服务端任务历史" className="min-h-0 flex-1 overflow-y-auto px-3 py-5 sm:px-6 sm:py-7">
      <header className="mx-auto mb-6 flex max-w-7xl items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold text-zinc-100">历史与定稿</h1>
          <p className="mt-1 text-sm text-zinc-400">
            统一管理精修、换脸和旧版记录。可单条或批量删除，运行中的记录需先等待完成。
          </p>
        </div>
        <button
          disabled={history.isFetching || sessions.isFetching}
          className="btn btn-outline btn-sm shrink-0 text-xs text-zinc-200"
          onClick={() => {
            void history.refetch();
            void sessions.refetch();
            void queryClient.invalidateQueries({ queryKey: ['legacy-refinement-tasks'] });
          }}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${history.isFetching || sessions.isFetching ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </header>
      <ProjectHistory />
      <div className="mx-auto grid max-w-7xl items-start gap-5 lg:grid-cols-2">
        <div className="min-w-0 rounded-2xl border border-white/10 bg-surface-1/60 p-3 sm:p-4">
          <h2 className="mb-3 text-sm font-bold text-zinc-200">精修会话</h2>
          {sessions.isError && (
            <p role="alert" className="text-sm text-amber-300">
              精修会话暂不可读取。
            </p>
          )}
          {sessions.isLoading && (
            <p role="status" className="py-5 text-sm text-zinc-400">
              正在加载精修会话…
            </p>
          )}
          <RecordSelectionToolbar
            label="精修会话"
            ids={currentSessions
              .filter((item) => item.status === 'idle' && !item.active_turn_id)
              .map((item) => item.id)}
            deletion={sessionDeletion}
          />
          <div className="divide-y divide-border rounded-xl border border-border">
            {currentSessions.map((session) => {
              const active = session.status === 'running' || !!session.active_turn_id;
              return (
                <div key={session.id} className="flex items-center gap-3 p-3 hover:bg-surface-2">
                  <input
                    type="checkbox"
                    aria-label={`选择精修会话 ${session.id}`}
                    disabled={active || sessionDeletion.busy}
                    checked={sessionDeletion.selected.has(session.id)}
                    onChange={(event) => sessionDeletion.toggle([session.id], event.target.checked)}
                  />
                  <button className="min-w-0 flex-1 text-left" onClick={() => open(session.id)}>
                    <p className="truncate text-sm font-medium text-zinc-200">{session.title}</p>
                    <p className="mt-1 text-xs text-zinc-400">
                      {new Date(session.updated_at).toLocaleString('zh-CN', { hour12: false })}
                    </p>
                    <span className="mt-1 block text-xs text-cyan-300">
                      {active ? '后台处理中' : session.accepted_version_id ? '已接受 · 打开会话' : '待确认 · 打开会话'}
                    </span>
                  </button>
                  {!active && <RestoreProjectButton id={session.id} kind="session" />}
                  {!active && (
                    <button
                      className="btn btn-ghost btn-sm hover:text-red-300"
                      aria-label={`删除精修会话 ${session.id}`}
                      disabled={sessionDeletion.busy}
                      onClick={() => void sessionDeletion.request([session.id])}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      删除
                    </button>
                  )}
                </div>
              );
            })}
          </div>
          {!sessions.isLoading && !sessions.isError && !totalSessions && (
            <p className="mt-4 text-sm text-zinc-400">暂无精修会话</p>
          )}
          {totalSessions > 0 && (
            <div className="mt-3 flex items-center justify-between text-xs text-zinc-400">
              <span>
                第 {sessionPage} 页 · 本页 {currentSessions.length} 个会话
              </span>
              {(sessionPage > 1 || hasNextSessionPage) && (
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    className="btn btn-outline btn-xs"
                    disabled={sessionPage <= 1 || sessions.isFetching}
                    onClick={() => setSessionPage((p) => Math.max(1, p - 1))}
                    aria-label="上一页精修会话"
                  >
                    上一页
                  </button>
                  <button
                    type="button"
                    className="btn btn-outline btn-xs"
                    disabled={!hasNextSessionPage || sessions.isFetching}
                    onClick={() => setSessionPage((p) => p + 1)}
                    aria-label="下一页精修会话"
                  >
                    下一页
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
        <div className="min-w-0 rounded-2xl border border-white/10 bg-surface-1/60 p-3 sm:p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-zinc-200">本地换脸任务</h2>
            {history.isFetching && <Loader2 className="h-3.5 w-3.5 animate-spin text-zinc-400" />}
          </div>
          {history.isError && (
            <p role="alert" className="mb-3 text-sm text-amber-300">
              批次历史暂不可读取。
            </p>
          )}
          {deleteMutation.isError && (
            <div role="alert" className="mb-3 rounded-lg border border-err/30 bg-err/10 p-3 text-xs text-err">
              <AlertCircle className="mr-1.5 inline h-3.5 w-3.5" />
              任务删除失败；活动任务需等待完成后再删除。
            </div>
          )}
          {batchDeleteError && (
            <div
              role="alert"
              className="mb-3 rounded-lg border border-err/30 bg-err/10 p-3 text-xs text-err flex items-center justify-between"
            >
              <span>
                <AlertCircle className="mr-1.5 inline h-3.5 w-3.5" />
                {batchDeleteError}
              </span>
              <button
                type="button"
                className="btn btn-ghost btn-xs text-zinc-400 hover:text-zinc-200"
                onClick={() => setBatchDeleteError(null)}
              >
                关闭
              </button>
            </div>
          )}
          {history.isLoading && (
            <p role="status" className="py-5 text-sm text-zinc-400">
              正在加载换脸任务…
            </p>
          )}
          {totalTasks > 0 && (
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-surface-1 px-3 py-2 text-xs">
              <label className="flex items-center gap-2 cursor-pointer text-zinc-300 select-none">
                <input
                  type="checkbox"
                  aria-label="全选本页"
                  ref={(input) => {
                    if (input)
                      input.indeterminate =
                        !allPageTerminalSelected &&
                        terminalTasksOnPage.some((task) => selectedTaskIds.has(task.task_id));
                  }}
                  checked={allPageTerminalSelected}
                  disabled={terminalTasksOnPage.length === 0 || isMutating}
                  onChange={(e) => handleToggleSelectAll(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-zinc-600 bg-surface-2 text-primary-soft focus:ring-primary/40 cursor-pointer"
                />
                <span>全选本页 ({terminalTasksOnPage.length})</span>
              </label>
              {selectedTaskIds.size > 0 && (
                <div className="flex items-center gap-2">
                  <span className="text-zinc-400">已选 {selectedTaskIds.size} 项</span>
                  <button
                    type="button"
                    className="btn btn-xs bg-err/15 hover:bg-err/25 text-err border border-err/30 gap-1"
                    disabled={isMutating}
                    onClick={handleBatchDelete}
                    aria-label={`批量删除 ${selectedTaskIds.size} 项`}
                  >
                    {batchDeleteMutation.isPending ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Trash2 className="h-3 w-3" />
                    )}
                    批量删除 ({selectedTaskIds.size})
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-xs text-zinc-400 hover:text-zinc-200"
                    onClick={() => setSelectedTaskIds(new Set())}
                  >
                    取消选择
                  </button>
                </div>
              )}
            </div>
          )}
          <div className="divide-y divide-border rounded-xl border border-border">
            {currentTasks.map((task) => {
              const active = task.status === 'QUEUED' || task.status === 'PROCESSING';
              const isSelected = selectedTaskIds.has(task.task_id);
              return (
                <div
                  key={task.task_id}
                  className={`flex w-full flex-wrap items-center justify-between gap-3 p-3.5 hover:bg-surface-2 transition-colors ${
                    isSelected ? 'bg-primary/5' : ''
                  }`}
                >
                  <div className="flex items-center gap-2.5 min-w-0 flex-1">
                    <input
                      type="checkbox"
                      aria-label={`选择任务 ${task.task_id}`}
                      checked={isSelected}
                      disabled={active || isMutating}
                      title={active ? '活动任务不可删除' : undefined}
                      onChange={(e) => {
                        e.stopPropagation();
                        toggleSelect(task.task_id, e.target.checked);
                      }}
                      className="h-3.5 w-3.5 rounded border-zinc-600 bg-surface-1 text-primary-soft focus:ring-primary/40 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
                    />
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left cursor-pointer"
                      onClick={() => setTaskId(task.task_id)}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className="min-w-0 truncate font-mono text-xs font-semibold text-zinc-200"
                          title={task.task_id}
                        >
                          {task.task_id}
                        </span>
                        <span
                          className={`chip ${
                            task.status === 'COMPLETED'
                              ? 'chip-ok'
                              : task.status === 'PARTIAL'
                                ? 'chip-warn'
                                : task.status === 'FAILED'
                                  ? 'chip-err'
                                  : 'chip-neutral'
                          }`}
                        >
                          {taskStatusLabels[task.status] || task.status}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-zinc-400">
                        {task.total_images ? `图片 ${task.processed_images ?? 0} / ${task.total_images}` : '只读回看'}
                        {task.viewable_results !== undefined ? ` · ${task.viewable_results} 个结果` : ''}
                        {task.created_at
                          ? ` · ${new Date(task.created_at).toLocaleString('zh-CN', { hour12: false })}`
                          : ''}
                      </p>
                    </button>
                  </div>
                  <div className="ml-auto flex items-center gap-1.5 shrink-0">
                    {!active && <RestoreProjectButton id={task.task_id} kind="batch" />}
                    <button
                      type="button"
                      className="btn btn-outline btn-xs text-zinc-300"
                      onClick={() => setTaskId(task.task_id)}
                    >
                      查看
                    </button>
                    {!active && (
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs text-zinc-400 hover:text-err hover:bg-err/10"
                        aria-label={`删除任务 ${task.task_id}`}
                        disabled={isMutating}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(task.task_id);
                        }}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        删除
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          {!history.isLoading && !history.isError && !totalTasks && (
            <p className="mt-4 text-sm text-zinc-400">暂无批次任务</p>
          )}
          {totalTasks > 0 && (
            <div className="mt-3 flex items-center justify-between text-xs text-zinc-400">
              <span>
                共 {totalTasks} 条记录 · 第 {validTaskPage} / {totalTaskPages} 页
              </span>
              {totalTaskPages > 1 && (
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    className="btn btn-outline btn-xs"
                    disabled={validTaskPage <= 1}
                    onClick={() => setTaskPage((p) => Math.max(1, p - 1))}
                    aria-label="上一页"
                  >
                    上一页
                  </button>
                  <button
                    type="button"
                    className="btn btn-outline btn-xs"
                    disabled={validTaskPage >= totalTaskPages}
                    onClick={() => setTaskPage((p) => Math.min(totalTaskPages, p + 1))}
                    aria-label="下一页"
                  >
                    下一页
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      <LegacyQualityHistory />
      <TaskResultHistoryDialog
        taskId={taskId}
        onClose={() => setTaskId(null)}
        onDelete={handleDelete}
        isDeleting={isMutating}
      />
    </section>
  );
}
