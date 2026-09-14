import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Trash2 } from 'lucide-react';
import { useRecordDeletion } from '@/hooks/useRecordDeletion';
import { RecordSelectionToolbar } from './RecordSelectionToolbar';
import { api } from '@/services/api';
import { RefinementQualityPanel } from '@/features/refinement/components/RefinementQualityPanel';

/** Legacy summaries are a separate namespace; never reinterpret session turn IDs. */
export function LegacyQualityHistory() {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const [taskId, setTaskId] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ['legacy-refinement-tasks', offset],
    queryFn: () => api.listLegacyRefinementTasks(30, offset),
    enabled: open,
  });
  const task = useQuery({
    queryKey: ['legacy-refinement-quality-task', taskId],
    queryFn: () => api.getRefinementTask(taskId!),
    enabled: !!taskId && open,
    retry: false,
  });
  const removable = (list.data ?? []).filter((item) =>
    ['REVIEW_REQUIRED', 'ACCEPTED', 'FAILED', 'CANCELLED'].includes(item.status),
  );
  const deletion = useRecordDeletion({
    records: removable.map((item) => ({ id: item.task_id, revision: item.revision })),
    description: '旧版精修记录及其候选、已接受文件和质量评价证据',
    remove: ({ id, revision }) => api.deleteLegacyRefinementTask(id, revision),
    onDeleted: async (id) => {
      setTaskId((current) => (current === id ? null : current));
      await client.cancelQueries({ queryKey: ['legacy-refinement-tasks'] });
      for (const key of [
        ['legacy-refinement-quality-task', id],
        ['refinement-quality', id],
      ]) {
        await client.cancelQueries({ queryKey: key });
        client.removeQueries({ queryKey: key });
      }
      client.setQueriesData<Awaited<ReturnType<typeof api.listLegacyRefinementTasks>>>(
        { queryKey: ['legacy-refinement-tasks'] },
        (current) => current?.filter((item) => item.task_id !== id),
      );
    },
    refresh: () => {
      void client.invalidateQueries({ queryKey: ['legacy-refinement-tasks'] });
      void client.invalidateQueries({ queryKey: ['refinement-quality-summary'] });
    },
  });
  useEffect(() => {
    if (offset > 0 && list.data?.length === 0 && !list.isFetching) setOffset((current) => Math.max(0, current - 30));
  }, [offset, list.data, list.isFetching]);
  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className="mt-8 rounded-xl border border-border p-4"
    >
      <summary className="cursor-pointer text-sm font-medium">旧版精修质量记录</summary>
      <p className="mt-3 text-xs text-zinc-500">
        回看旧版精修证据与质量评价，也可删除不再需要的档案。删除会清理其候选、已接受文件与评价，不影响新精修会话。
      </p>
      {list.isError && (
        <p role="alert" className="mt-3 text-xs text-amber-200">
          旧版记录暂不可读取。
          <button className="ml-2 underline" onClick={() => void list.refetch()}>
            重新读取
          </button>
        </p>
      )}
      <div className="mt-3">
        <RecordSelectionToolbar label="旧版记录" ids={removable.map((item) => item.task_id)} deletion={deletion} />
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {list.data?.map((item) => {
          const eligible = removable.some((record) => record.task_id === item.task_id);
          return (
            <div key={item.task_id} className="flex items-center gap-2 rounded-xl border border-border p-3">
              <input
                type="checkbox"
                aria-label={`选择旧版记录 ${item.task_id}`}
                checked={deletion.selected.has(item.task_id)}
                disabled={!eligible || deletion.busy}
                onChange={(event) => deletion.toggle([item.task_id], event.target.checked)}
              />
              <button
                aria-pressed={taskId === item.task_id}
                className="min-w-0 flex-1 text-left"
                onClick={() => setTaskId(item.task_id)}
              >
                <span className="block truncate font-mono text-xs">{item.task_id}</span>
                <span className="mt-1 block text-2xs text-zinc-500">
                  {item.status} · {item.has_accepted ? '有已接受结果' : item.has_candidate ? '有候选' : '无候选'}
                </span>
              </button>
              {eligible && (
                <button
                  className="btn btn-ghost btn-sm hover:text-red-300"
                  aria-label={`删除旧版记录 ${item.task_id}`}
                  disabled={deletion.busy}
                  onClick={() => void deletion.request([item.task_id])}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          );
        })}
      </div>
      {!list.isLoading && !list.data?.length && <p className="mt-3 text-xs text-zinc-500">暂无旧版记录</p>}
      <div className="mt-3 flex items-center gap-2">
        <button
          className="btn btn-ghost btn-sm"
          disabled={!offset || list.isFetching}
          onClick={() => setOffset((value) => Math.max(0, value - 30))}
        >
          上一页旧记录
        </button>
        <button
          className="btn btn-ghost btn-sm"
          disabled={list.data?.length !== 30 || list.isFetching}
          onClick={() => setOffset((value) => value + 30)}
        >
          下一页旧记录
        </button>
      </div>
      {task.isFetching && (
        <p role="status" className="mt-3 text-xs text-zinc-400">
          正在读取历史证据…
        </p>
      )}
      {task.isError && (
        <p role="alert" className="mt-3 text-xs text-amber-200">
          旧版任务或证据不可用；不会使用新会话或其他版本替代。
        </p>
      )}
      {task.data && <RefinementQualityPanel key={taskId} task={task.data} />}
    </details>
  );
}
