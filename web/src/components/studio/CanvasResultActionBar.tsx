import { CheckCircle2, Download, BrainCircuit } from 'lucide-react';
import type { TargetImageItem } from '@/types';

interface Props {
  currentImage: TargetImageItem;
  onOpenRefinement: (targetId: string, sessionId?: string) => void;
  onDownload: () => void;
}

/** Reserve space for result actions so they never obscure the image or overflow on phones. */
export function CanvasResultActionBar({ currentImage, onOpenRefinement, onDownload }: Props) {
  return (
    <div data-testid="target-result-action-bar" className="shrink-0 border-t border-white/10 bg-surface-1 px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs text-zinc-400">
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
          {currentImage.refinedUrl ? '已同步接受的精修结果' : '本地结果已生成'}
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
            <BrainCircuit className="h-3.5 w-3.5 text-cyan-300" />
            AI 精修
          </button>
          <button
            aria-label="下载当前结果"
            onClick={onDownload}
            className="btn btn-sm btn-primary min-h-9 whitespace-nowrap"
          >
            <Download className="h-3.5 w-3.5" />
            下载结果
          </button>
        </div>
      </div>
      {currentImage.swapWarning && (
        <p role="status" className="mt-2 break-words text-xs text-amber-300">
          {currentImage.swapWarning}
        </p>
      )}
    </div>
  );
}
