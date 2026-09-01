import React, { useEffect, useMemo, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle, Download, ExternalLink, Loader2, ZoomIn, ZoomOut, X } from 'lucide-react';
import { api } from '../../services/api';
import { ComparisonViewer } from '../ui/ComparisonViewer';

interface TaskResultHistoryDialogProps {
  taskId: string | null;
  targetNames?: string[];
  onClose: () => void;
}

const statusLabel: Record<string, string> = {
  COMPLETED: '已完成',
  PARTIAL: '部分完成',
  FAILED: '失败',
  SUCCESS: '成功',
};
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.25;

/** Read-only history viewer. It never mutates the active Studio workspace. */
export const TaskResultHistoryDialog: React.FC<TaskResultHistoryDialogProps> = ({
  taskId,
  targetNames = [],
  onClose,
}) => {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [zoom, setZoom] = useState(1);
  const taskQuery = useQuery({
    queryKey: ['batch-task-history-detail', taskId],
    queryFn: () => api.getTaskStatus(taskId!),
    enabled: Boolean(taskId),
    retry: 2,
  });
  const viewableResults = useMemo(
    () => (taskQuery.data?.results ?? [])
      .map((result, resultIndex) => ({ result, resultIndex }))
      .filter(({ result }) =>
        (result.status === 'SUCCESS' || result.status === 'PARTIAL') && Boolean(result.processed_url)),
    [taskQuery.data],
  );
  const selected = viewableResults[Math.min(selectedIndex, Math.max(viewableResults.length - 1, 0))];

  useEffect(() => {
    setSelectedIndex(0);
    setZoom(1);
  }, [taskId]);

  if (!taskId) return null;
  const detail = taskQuery.data;
  const selectedName = selected
    ? targetNames[selected.resultIndex] || selected.result.client_target_id || `结果 ${selected.resultIndex + 1}`
    : '';

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[100] bg-black/85 backdrop-blur-md" />
        <Dialog.Content
          aria-describedby="task-history-description"
          className="fixed left-1/2 top-1/2 z-[101] flex h-[min(94dvh,960px)] w-[calc(100%-1rem)] max-w-7xl -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-surface-2 shadow-elevated sm:w-[calc(100%-2rem)]"
        >
          <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-5">
            <div className="min-w-0">
              <Dialog.Title className="text-sm font-semibold text-zinc-100">任务结果回看</Dialog.Title>
              <Dialog.Description id="task-history-description" className="mt-0.5 truncate font-mono text-2xs text-zinc-500">
                {taskId} · 只读查看，不会覆盖当前工作台
              </Dialog.Description>
            </div>
            <div className="flex items-center gap-2">
              {detail && <span className={`chip ${detail.status === 'PARTIAL' ? 'chip-warn' : detail.status === 'COMPLETED' ? 'chip-ok' : 'chip-neutral'}`}>{statusLabel[detail.status] ?? detail.status}</span>}
              <Dialog.Close className="btn btn-sm btn-ghost p-1.5" aria-label="关闭任务结果回看"><X className="h-4 w-4" /></Dialog.Close>
            </div>
          </header>

          {taskQuery.isLoading && <div role="status" className="flex flex-1 items-center justify-center gap-2 text-sm text-zinc-400"><Loader2 className="h-4 w-4 animate-spin" />正在读取持久化任务结果…</div>}
          {taskQuery.isError && <div role="alert" className="m-auto flex max-w-lg items-start gap-2 rounded-xl border border-err/30 bg-err/10 p-4 text-sm text-err"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /><div><div className="font-medium">无法读取该任务</div><div className="mt-1 text-xs opacity-80">任务记录或结果文件可能已被清理，请刷新历史列表后重试。</div></div></div>}

          {detail && !taskQuery.isError && (
            <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[220px_minmax(0,1fr)]">
              <aside className="min-h-0 overflow-y-auto border-b border-border p-3 lg:border-b-0 lg:border-r">
                <div className="mb-2 flex items-center justify-between text-2xs text-zinc-500"><span>可查看结果</span><span>{viewableResults.length} / {detail.total_images}</span></div>
                <div className="grid grid-cols-3 gap-2 lg:grid-cols-1">
                  {viewableResults.map(({ result, resultIndex }, listIndex) => {
                    const name = targetNames[resultIndex] || result.client_target_id || `结果 ${resultIndex + 1}`;
                    return <button key={result.client_target_id} type="button" onClick={() => { setSelectedIndex(listIndex); setZoom(1); }} className={`min-w-0 overflow-hidden rounded-lg border text-left transition-colors ${listIndex === selectedIndex ? 'border-primary bg-primary-muted' : 'border-border bg-surface-1 hover:border-border-md'}`} aria-pressed={listIndex === selectedIndex}>
                      <img src={result.processed_url} alt="" className="aspect-video w-full bg-black object-cover" loading="lazy" />
                      <div className="truncate px-2 py-1.5 text-2xs text-zinc-300" title={name}>{name}</div>
                    </button>;
                  })}
                </div>
                {!viewableResults.length && <div className="rounded-lg border border-dashed border-border p-4 text-center text-xs leading-relaxed text-zinc-500">该任务没有可查看的成功结果。失败详情仍保留在任务记录中。</div>}
              </aside>

              <main className="flex min-h-0 min-w-0 flex-col bg-black/30">
                {selected ? <>
                  <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
                    <div className="min-w-0"><div className="truncate text-xs font-medium text-zinc-200" title={selectedName}>{selectedName}</div><div className="mt-0.5 text-[10px] text-zinc-500">换脸 {selected.result.faces_swapped} / 检测 {selected.result.faces_detected} · {Math.round(selected.result.cost_ms)} ms</div></div>
                    <div className="flex items-center gap-1 rounded-lg border border-border bg-black/40 p-1">
                      <button type="button" onClick={() => setZoom((value) => Math.max(MIN_ZOOM, value - ZOOM_STEP))} disabled={zoom <= MIN_ZOOM} className="btn btn-sm btn-ghost p-1.5" aria-label="缩小历史结果"><ZoomOut className="h-4 w-4" /></button>
                      <button type="button" onClick={() => setZoom(1)} className="min-w-14 rounded-md px-2 py-1 font-mono text-xs text-zinc-200 hover:bg-white/10" aria-label="恢复历史结果百分之百缩放">{Math.round(zoom * 100)}%</button>
                      <button type="button" onClick={() => setZoom((value) => Math.min(MAX_ZOOM, value + ZOOM_STEP))} disabled={zoom >= MAX_ZOOM} className="btn btn-sm btn-ghost p-1.5" aria-label="放大历史结果"><ZoomIn className="h-4 w-4" /></button>
                      <a href={selected.result.processed_url} target="_blank" rel="noreferrer" className="btn btn-sm btn-ghost p-1.5" aria-label="新窗口打开处理结果"><ExternalLink className="h-4 w-4" /></a>
                      <a href={selected.result.processed_url} download className="btn btn-sm btn-ghost p-1.5" aria-label="下载处理结果"><Download className="h-4 w-4" /></a>
                    </div>
                  </div>
                  {selected.result.warning && <div role="status" className="shrink-0 border-b border-warn-border bg-warn-muted px-3 py-2 text-xs text-warn">{selected.result.warning}</div>}
                  <div className="min-h-0 flex-1 overflow-auto p-3">
                    <div className="flex min-h-full items-center justify-center">
                      <div className="shrink-0 transition-[width] duration-200" style={{ width: `${zoom * 100}%` }}>
                        <ComparisonViewer beforeSrc={selected.result.original_url} afterSrc={selected.result.processed_url!} beforeLabel="原图" afterLabel="换脸结果" imageLabel={`历史任务 ${selectedName}`} className="w-full" />
                      </div>
                    </div>
                  </div>
                </> : <div className="flex flex-1 items-center justify-center p-6 text-sm text-zinc-500">请选择一个可查看结果</div>}
              </main>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
};
