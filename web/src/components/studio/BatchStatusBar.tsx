import { AlertCircle, CheckCircle2, Clock } from 'lucide-react';
import type { BatchPipeline } from '@/hooks/useBatchPipeline';
import { useStudioStore } from '@/stores/useStudioStore';
import { localInferenceStatus } from '@/utils/localInferenceStatus';

/** Display task state only; task actions live in the right sidebar. */
export function BatchStatusBar({ batch }: { batch: BatchPipeline }) {
  const singleRunning = useStudioStore((state) => state.targetImages.some((target) => target.status === 'PROCESSING'));
  const engineStatus = localInferenceStatus(useStudioStore((state) => state.serverHealth));
  const {
    queryError,
    isProcessing,
    batchProgress,
    completedCount,
    failedCount,
    totalCount,
    mappingStatus,
    taskSummary,
  } = batch;
  const indeterminate = singleRunning && !isProcessing && !queryError;
  const running = isProcessing || singleRunning;
  const status = queryError
    ? queryError
    : isProcessing
      ? batch.waitingReason ||
        engineStatus ||
        (batch.taskStatus === 'QUEUED' ? '已排队，等待前一个批任务完成…' : '逐张换脸中…')
      : singleRunning
        ? engineStatus || '单图试换中…'
        : batch.taskStatus === 'FAILED'
          ? '批量任务失败，请在历史中查看详情'
          : completedCount > 0
            ? `已完成 ${completedCount} / ${totalCount}`
            : mappingStatus.ready
              ? '映射已确认，可启动'
              : mappingStatus.message;

  return (
    <footer
      aria-label="任务状态栏"
      className="shrink-0 flex min-h-12 items-center gap-3 border-t border-white/10 bg-surface-1/95 px-3 py-2 sm:px-5"
    >
      <div role="status" className="min-w-0 flex-1 flex items-center gap-2 text-xs">
        {queryError || batch.taskStatus === 'FAILED' ? (
          <AlertCircle className="h-3.5 w-3.5 shrink-0 text-err" />
        ) : running ? (
          <Clock className="h-3.5 w-3.5 shrink-0 text-accent animate-pulse" />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-zinc-400" />
        )}
        <span title={status} className={`truncate ${queryError ? 'text-err' : 'text-zinc-300'}`}>
          {status}
        </span>
        {failedCount > 0 && <span className="shrink-0 text-err">失败 {failedCount}</span>}
      </div>
      <div className="flex w-24 shrink-0 items-center gap-2 sm:w-48">
        <div
          role="progressbar"
          aria-label={indeterminate ? '单图处理进度' : '批量任务进度'}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={indeterminate ? undefined : batchProgress}
          className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3"
        >
          <div
            className={`h-full rounded-full bg-primary transition-width ${indeterminate ? 'animate-pulse' : ''}`}
            style={{ width: indeterminate ? '35%' : `${batchProgress}%` }}
          />
        </div>
        <span className="font-mono text-xs text-zinc-400">{indeterminate ? '处理中' : `${batchProgress}%`}</span>
      </div>
      {taskSummary && !running && !queryError && (
        <span className="hidden shrink-0 text-xs text-zinc-400 lg:inline">
          {taskSummary.total} 张 · {taskSummary.costSeconds}s · {taskSummary.avgSpeed}s/图
        </span>
      )}
    </footer>
  );
}
