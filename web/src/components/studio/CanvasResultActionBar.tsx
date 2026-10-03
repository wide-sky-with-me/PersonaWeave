import { CheckCircle2, Download, BrainCircuit } from 'lucide-react';
import type { TargetImageItem } from '@/types';
import { unavailableRefinementMessage } from '@/features/refinement/acceptedResults';

interface Props {
  currentImage: TargetImageItem;
  onOpenRefinement: (targetId: string, sessionId?: string) => void;
  onDownload: () => void;
  isDownloading?: boolean;
}

/** Reserve space for result actions so they never obscure the image or overflow on phones. */
export function CanvasResultActionBar({ currentImage, onOpenRefinement, onDownload, isDownloading }: Props) {
  return (
    <div data-testid="target-result-action-bar" className="shrink-0 border-t border-border bg-surface-1/95 px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs text-zinc-400">
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
          {currentImage.refinementResultUnavailable
            ? '当前显示本地初稿'
            : currentImage.refinedUrl
              ? '已同步接受的精修结果'
              : '本地结果已生成'}
          {currentImage.costMs != null && (
            <span className="font-mono">· {(currentImage.costMs / 1000).toFixed(1)}s</span>
          )}
        </span>
        <div className="flex items-center gap-2">
          <button
            aria-label="AI 精修当前图"
            onClick={() => onOpenRefinement(currentImage.id, currentImage.refinementSessionId)}
            className="btn btn-sm btn-outline min-h-9 whitespace-nowrap"
          >
            <BrainCircuit className="h-3.5 w-3.5 text-primary-soft" />
            AI 精修
          </button>
          <button
            aria-label="下载当前结果"
            onClick={onDownload}
            disabled={isDownloading || currentImage.refinementResultUnavailable}
            className="btn btn-sm btn-primary min-h-9 whitespace-nowrap"
          >
            <Download className="h-3.5 w-3.5" />
            {isDownloading ? '正在下载…' : '下载结果'}
          </button>
        </div>
      </div>
      {currentImage.refinementResultUnavailable && (
        <p role="alert" className="mt-2 break-words text-xs text-amber-300">
          {unavailableRefinementMessage}
        </p>
      )}
      {currentImage.swapWarning && (
        <p role="status" className="mt-2 break-words text-xs text-amber-300">
          {currentImage.swapWarning}
        </p>
      )}
    </div>
  );
}
