import React, { useEffect, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle, Download, ExternalLink, Loader2, Trash2, ZoomIn, ZoomOut, X } from 'lucide-react';
import { taskStatusLabels as statusLabel } from '../utils/taskHistory';
import { api } from '@/services/api';
import { ComparisonViewer } from '@/components/ui/ComparisonViewer';
import { PersonOutcomeDiagnostics } from './PersonOutcomeDiagnostics';
import { RestoreProjectButton } from '@/features/projects/ProjectHistory';

interface TaskResultHistoryDialogProps {
  taskId: string | null;
  targetNames?: string[];
  onClose: () => void;
  onDelete?: (taskId: string) => void;
  isDeleting?: boolean;
}

const count = (value: number | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? String(value) : '未知';

/** A terminal task can contain both usable results and failed images. Never hydrate Studio. */
export const TaskResultHistoryDialog: React.FC<TaskResultHistoryDialogProps> = ({
  taskId,
  targetNames = [],
  onClose,
  onDelete,
  isDeleting = false,
}) => {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [imageUnavailable, setImageUnavailable] = useState(false);
  const taskQuery = useQuery({
    queryKey: ['batch-task-history-detail', taskId],
    queryFn: () => api.getTaskStatus(taskId!),
    enabled: Boolean(taskId),
    retry: 2,
  });
  const results = taskQuery.data?.results ?? [];
  const selected = results[selectedIndex];
  // Task-level FAILED can be a restart marker; media eligibility belongs to each result.
  const hasOutput = Boolean(
    selected && (selected.status === 'SUCCESS' || selected.status === 'PARTIAL') && selected.processed_url,
  );
  const canCompare = hasOutput && Boolean(selected?.original_url);

  useEffect(() => {
    setSelectedIndex(0);
    setZoom(1);
    setImageUnavailable(false);
  }, [taskId]);
  useEffect(() => {
    setZoom(1);
    setImageUnavailable(false);
  }, [selectedIndex]);

  return (
    <Dialog.Root
      open={Boolean(taskId)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[100] bg-black/85 backdrop-blur-md" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-[101] flex h-[min(94dvh,960px)] w-[calc(100%-1rem)] max-w-7xl -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-surface-2 p-4 shadow-elevated sm:w-[calc(100%-2rem)] sm:p-5">
          <header className="flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-sm font-semibold text-zinc-100">任务结果回看</Dialog.Title>
              <Dialog.Description className="mt-1 text-xs text-zinc-500">
                只读查看图片结果与失败诊断，不会覆盖当前工作台。{taskId}
              </Dialog.Description>
            </div>
            <div className="flex items-center gap-2">
              {taskId && taskQuery.data && !['QUEUED', 'PROCESSING'].includes(taskQuery.data.status) && (
                <RestoreProjectButton id={taskId} kind="batch" />
              )}
              {onDelete &&
                taskQuery.data &&
                taskQuery.data.status !== 'QUEUED' &&
                taskQuery.data.status !== 'PROCESSING' && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm text-zinc-400 hover:text-err hover:bg-err/10"
                    aria-label={`删除任务 ${taskId}`}
                    disabled={isDeleting}
                    onClick={() => {
                      if (taskId) {
                        onDelete(taskId);
                      }
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                    <span className="hidden sm:inline">删除记录</span>
                  </button>
                )}
              <Dialog.Close className="btn btn-ghost btn-icon" aria-label="关闭历史结果">
                <X className="h-4 w-4" />
              </Dialog.Close>
            </div>
          </header>
          {taskQuery.isLoading && (
            <p role="status" className="mt-6 flex items-center gap-2 text-sm text-zinc-400">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载任务详情…
            </p>
          )}
          {taskQuery.isError && (
            <div role="alert" className="mt-6 rounded-lg border border-err/30 bg-err/10 p-4 text-sm text-err">
              <AlertCircle className="mr-2 inline h-4 w-4" />
              任务详情不可用，记录可能已被清理。
              <button className="btn btn-ghost btn-sm ml-2" onClick={() => taskQuery.refetch()}>
                重试读取
              </button>
            </div>
          )}
          {taskQuery.data && (
            <>
              <p className="mt-3 text-xs text-zinc-400">
                状态：{statusLabel[taskQuery.data.status] ?? taskQuery.data.status} · 已处理{' '}
                <span>
                  {count(taskQuery.data.processed_images)} / {count(taskQuery.data.total_images)}
                </span>{' '}
                · 有结果 {count(taskQuery.data.success_count)} · 失败 {count(taskQuery.data.failure_count)}
              </p>
              {taskQuery.data.error && (
                <p role="alert" className="mt-2 text-xs text-err">
                  任务错误：{taskQuery.data.error}
                </p>
              )}
              {!results.length ? (
                <p className="mt-6 text-sm text-zinc-500">暂无逐图记录或诊断，不能据此推断没有检测到人脸。</p>
              ) : (
                <div className="mt-4 flex min-h-0 flex-1 flex-col gap-4 md:flex-row">
                  <nav aria-label="任务图片" className="flex shrink-0 gap-2 overflow-auto md:w-48 md:flex-col">
                    {results.map((result, index) => (
                      <button
                        key={result.client_target_id ?? index}
                        aria-pressed={selectedIndex === index}
                        onClick={() => setSelectedIndex(index)}
                        className={
                          'rounded-lg border p-3 text-left text-xs ' +
                          (selectedIndex === index
                            ? 'border-primary/50 bg-primary/10 text-zinc-100'
                            : 'border-border text-zinc-400')
                        }
                      >
                        {(result.status === 'SUCCESS' || result.status === 'PARTIAL') && result.processed_url && (
                          <img
                            src={result.processed_url}
                            alt=""
                            loading="lazy"
                            className="mb-2 aspect-video w-full rounded bg-black object-cover"
                          />
                        )}
                        <span className="block truncate">
                          图片 {index + 1} · {targetNames[index] || result.client_target_id || '未命名图片'}
                        </span>
                        <span className="mt-1 block">
                          {statusLabel[result.status] ?? result.status ?? '未知状态'} · 检测{' '}
                          {count(result.faces_detected)} / 换入 {count(result.faces_swapped)}
                        </span>
                      </button>
                    ))}
                  </nav>
                  {selected && (
                    <main className="min-h-0 min-w-0 flex-1 space-y-3 overflow-auto">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <h3 className="text-sm text-zinc-200">
                          图片 {selectedIndex + 1} · {statusLabel[selected.status] ?? selected.status ?? '未知状态'}
                        </h3>
                        {hasOutput && (
                          <div className="flex items-center gap-2">
                            {canCompare && (
                              <>
                                <button
                                  aria-label="缩小历史图片"
                                  className="btn btn-ghost btn-icon"
                                  disabled={zoom <= 0.5}
                                  onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}
                                >
                                  <ZoomOut className="h-4 w-4" />
                                </button>
                                <button
                                  aria-label="恢复历史结果百分之百缩放"
                                  className="text-xs text-zinc-400"
                                  onClick={() => setZoom(1)}
                                >
                                  {Math.round(zoom * 100)}%
                                </button>
                                <button
                                  aria-label="放大历史图片"
                                  className="btn btn-ghost btn-icon"
                                  disabled={zoom >= 3}
                                  onClick={() => setZoom((value) => Math.min(3, value + 0.25))}
                                >
                                  <ZoomIn className="h-4 w-4" />
                                </button>
                              </>
                            )}
                            <a
                              href={selected.processed_url!}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="btn btn-outline btn-sm"
                            >
                              <ExternalLink className="h-3 w-3" />
                              打开结果
                            </a>
                            <a href={selected.processed_url!} download className="btn btn-primary btn-sm">
                              <Download className="h-3 w-3" />
                              下载结果
                            </a>
                          </div>
                        )}
                      </div>
                      <p className="text-xs text-zinc-400">
                        检测人脸：{count(selected.faces_detected)} · 已换入：{count(selected.faces_swapped)}
                      </p>
                      {selected.error && (
                        <p role="alert" className="rounded-lg border border-err/30 bg-err/10 p-3 text-xs text-err">
                          图片错误：{selected.error}
                        </p>
                      )}
                      {selected.warning && (
                        <p className="rounded-lg border border-warn/30 bg-warn/10 p-3 text-xs text-warn">
                          图片警告：{selected.warning}
                        </p>
                      )}
                      {canCompare ? (
                        <div className="min-h-64 overflow-auto rounded-lg border border-border">
                          <div style={{ width: String(zoom * 100) + '%' }}>
                            <ComparisonViewer
                              key={taskId + ':' + selectedIndex}
                              beforeSrc={selected.original_url}
                              afterSrc={selected.processed_url!}
                              beforeLabel="原图"
                              afterLabel="换脸结果"
                              imageLabel={
                                '历史任务 ' +
                                (targetNames[selectedIndex] ||
                                  selected.client_target_id ||
                                  '图片 ' + (selectedIndex + 1))
                              }
                              className="w-full"
                            />
                          </div>
                        </div>
                      ) : hasOutput ? (
                        <div className="rounded-lg border border-border p-3">
                          <p className="mb-2 text-xs text-zinc-500">原图不可用，仅显示已保存结果，无法前后对比。</p>
                          {imageUnavailable ? (
                            <p role="alert" className="text-xs text-err">
                              结果图片不可用，文件可能已被清理。
                            </p>
                          ) : (
                            <img
                              src={selected.processed_url!}
                              alt="已保存的历史结果"
                              className="max-h-[55vh] object-contain"
                              onError={() => setImageUnavailable(true)}
                            />
                          )}
                        </div>
                      ) : (
                        <p className="rounded-lg border border-dashed border-border p-5 text-sm text-zinc-500">
                          此图片没有可用的成功结果，无法对比或下载。未生成替代图片。
                        </p>
                      )}
                      <PersonOutcomeDiagnostics outcomes={selected.person_outcomes} />
                    </main>
                  )}
                </div>
              )}
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
};
