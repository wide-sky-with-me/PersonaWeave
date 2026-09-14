import React, { useEffect, useState } from 'react';
import { fitImageWithinViewport, type ViewportSize } from '@/utils/imageViewport';

interface ComparisonViewerProps {
  beforeSrc: string;
  afterSrc: string;
  beforeLabel?: string;
  afterLabel?: string;
  imageLabel?: string;
  className?: string;
  viewportSize?: ViewportSize;
  zoom?: number;
}

interface ImageState {
  src: string;
  width: number;
  height: number;
  status: 'loaded' | 'error';
}

const clampPosition = (value: number) => Math.min(100, Math.max(0, value));
const STEP = 2;

/**
 * Before/after wipe with one coordinate system for clipping, divider, pointer,
 * touch and keyboard input. Refinement output must match the baseline canvas;
 * a size mismatch degrades to side-by-side display instead of a false overlay.
 */
export const ComparisonViewer: React.FC<ComparisonViewerProps> = ({
  beforeSrc,
  afterSrc,
  beforeLabel = '处理前',
  afterLabel = '处理后',
  imageLabel = '图片处理结果',
  className = '',
  viewportSize,
  zoom = 1,
}) => {
  const [position, setPosition] = useState(50);
  const [beforeState, setBeforeState] = useState<ImageState | null>(null);
  const [afterState, setAfterState] = useState<ImageState | null>(null);

  useEffect(() => setPosition(50), [beforeSrc, afterSrc]);

  const beforeReady = beforeState?.src === beforeSrc && beforeState.status === 'loaded';
  const afterReady = afterState?.src === afterSrc && afterState.status === 'loaded';
  const beforeError = beforeState?.src === beforeSrc && beforeState.status === 'error';
  const afterError = afterState?.src === afterSrc && afterState.status === 'error';
  const dimensionsMatch = Boolean(
    beforeReady && afterReady && beforeState.width === afterState.width && beforeState.height === afterState.height,
  );
  const comparisonReady = beforeReady && afterReady && dimensionsMatch;
  const viewportRatio = beforeReady ? beforeState.width + ' / ' + beforeState.height : '16 / 9';
  const boundedSize = viewportSize
    ? fitImageWithinViewport(
        beforeReady ? beforeState.width : 16,
        beforeReady ? beforeState.height : 9,
        Math.max(1, viewportSize.width - 2),
        Math.max(1, viewportSize.height - 2),
      )
    : null;

  const updateFromPointer = (element: HTMLElement, clientX: number) => {
    const bounds = element.getBoundingClientRect();
    if (!bounds.width) return;
    setPosition(clampPosition(((clientX - bounds.left) / bounds.width) * 100));
  };

  const loadState = (src: string, image: HTMLImageElement): ImageState => ({
    src,
    width: image.naturalWidth,
    height: image.naturalHeight,
    status: 'loaded',
  });

  if (beforeReady && afterReady && !dimensionsMatch) {
    return (
      <figure
        className={'space-y-2 ' + className + (viewportSize ? ' flex flex-col' : '')}
        style={
          viewportSize
            ? { width: viewportSize.width * zoom, height: viewportSize.height * zoom, flexShrink: 0 }
            : undefined
        }
      >
        <div
          className={viewportSize ? 'grid min-h-0 flex-1 grid-cols-2 gap-2' : 'grid grid-cols-1 gap-2 sm:grid-cols-2'}
        >
          <div
            className={
              'overflow-hidden rounded-xl border border-border bg-black' +
              (viewportSize ? ' flex min-h-0 flex-col' : '')
            }
          >
            <div className="border-b border-border bg-surface-3 px-3 py-2 text-xs text-zinc-300">{beforeLabel}</div>
            <img
              src={beforeSrc}
              alt={`${imageLabel}：${beforeLabel}`}
              className={viewportSize ? 'min-h-0 w-full flex-1 object-contain' : 'h-auto w-full object-contain'}
              draggable={false}
            />
          </div>
          <div
            className={
              'overflow-hidden rounded-xl border border-border bg-black' +
              (viewportSize ? ' flex min-h-0 flex-col' : '')
            }
          >
            <div className="border-b border-border bg-surface-3 px-3 py-2 text-xs text-zinc-300">{afterLabel}</div>
            <img
              src={afterSrc}
              alt={`${imageLabel}：${afterLabel}`}
              className={viewportSize ? 'min-h-0 w-full flex-1 object-contain' : 'h-auto w-full object-contain'}
              draggable={false}
            />
          </div>
        </div>
        <figcaption
          role="alert"
          className="rounded-lg border border-warn-border bg-warn-muted px-3 py-2 text-xs text-warn"
        >
          两张图片尺寸不一致（{beforeState.width}×{beforeState.height} / {afterState.width}×{afterState.height}
          ），无法可靠叠加，已切换为并排检查。
        </figcaption>
      </figure>
    );
  }

  return (
    <figure
      className={`space-y-2 ${className}`}
      style={boundedSize ? { width: boundedSize.width * zoom + 2, flexShrink: 0 } : undefined}
    >
      <div className="overflow-hidden rounded-xl border border-border bg-black shadow-card">
        <div
          data-testid="comparison-viewport"
          className={`group relative w-full touch-none overflow-hidden bg-black select-none outline-none ${comparisonReady ? 'cursor-col-resize focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary' : 'cursor-wait'}`}
          style={{ aspectRatio: viewportRatio }}
          role="slider"
          tabIndex={comparisonReady ? 0 : -1}
          aria-disabled={!comparisonReady}
          aria-label={`${beforeLabel}与${afterLabel}对比位置`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(position)}
          aria-valuetext={`${afterLabel}从左侧显示至图片宽度的 ${Math.round(position)}%`}
          onKeyDown={(event) => {
            if (!comparisonReady) return;
            let next: number | null = null;
            if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') next = position - STEP;
            if (event.key === 'ArrowRight' || event.key === 'ArrowUp') next = position + STEP;
            if (event.key === 'PageDown') next = position - 10;
            if (event.key === 'PageUp') next = position + 10;
            if (event.key === 'Home') next = 0;
            if (event.key === 'End') next = 100;
            if (next === null) return;
            event.preventDefault();
            setPosition(clampPosition(next));
          }}
          onPointerDown={(event) => {
            if (!comparisonReady || (event.pointerType === 'mouse' && event.button !== 0)) return;
            event.currentTarget.setPointerCapture(event.pointerId);
            updateFromPointer(event.currentTarget, event.clientX);
          }}
          onPointerMove={(event) => {
            if (!comparisonReady || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
            updateFromPointer(event.currentTarget, event.clientX);
          }}
          onPointerUp={(event) => {
            if (event.currentTarget.hasPointerCapture(event.pointerId))
              event.currentTarget.releasePointerCapture(event.pointerId);
          }}
          onPointerCancel={(event) => {
            if (event.currentTarget.hasPointerCapture(event.pointerId))
              event.currentTarget.releasePointerCapture(event.pointerId);
          }}
        >
          <img
            key={beforeSrc}
            src={beforeSrc}
            data-testid="comparison-before-layer"
            alt={`${imageLabel}：${beforeLabel}`}
            className="pointer-events-none absolute inset-0 h-full w-full select-none object-fill object-left-top"
            draggable={false}
            onLoad={(event) => setBeforeState(loadState(beforeSrc, event.currentTarget))}
            onError={() => setBeforeState({ src: beforeSrc, width: 0, height: 0, status: 'error' })}
          />
          <img
            key={afterSrc}
            src={afterSrc}
            alt=""
            className="pointer-events-none absolute inset-0 h-full w-full select-none object-fill object-left-top"
            style={{ clipPath: `inset(0 ${100 - position}% 0 0)` }}
            data-testid="comparison-after-layer"
            draggable={false}
            onLoad={(event) => setAfterState(loadState(afterSrc, event.currentTarget))}
            onError={() => setAfterState({ src: afterSrc, width: 0, height: 0, status: 'error' })}
            aria-hidden="true"
          />

          {(!beforeReady || !afterReady) && (
            <div
              className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-black/60 px-6 text-center text-xs text-zinc-300"
              role="status"
            >
              {beforeError || afterError
                ? `对比图片加载失败：${beforeError ? beforeLabel : afterLabel}`
                : '正在加载前后对比图片…'}
            </div>
          )}
          <span className="pointer-events-none absolute left-3 top-3 z-10 rounded-md border border-border-md bg-black/75 px-2 py-1 text-xs font-medium text-zinc-200 backdrop-blur-sm">
            {afterLabel}
          </span>
          <span className="pointer-events-none absolute right-3 top-3 z-10 rounded-md border border-border-md bg-black/75 px-2 py-1 text-xs font-medium text-zinc-200 backdrop-blur-sm">
            {beforeLabel}
          </span>
          <div
            data-testid="comparison-divider"
            className="pointer-events-none absolute inset-y-0 z-10 w-px -translate-x-1/2 bg-primary shadow-primary"
            style={{ left: `${position}%` }}
            aria-hidden="true"
          />
          <span
            data-testid="comparison-handle"
            className="pointer-events-none absolute top-1/2 z-10 flex h-10 w-10 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-primary-border bg-surface-2 text-sm text-primary shadow-elevated"
            style={{ left: `clamp(20px, ${position}%, calc(100% - 20px))` }}
            aria-hidden="true"
          >
            ↔
          </span>
          <span className="pointer-events-none absolute bottom-2 left-1/2 z-10 -translate-x-1/2 whitespace-nowrap rounded bg-black/65 px-2 py-1 text-[10px] text-zinc-300 opacity-70 transition-opacity group-hover:opacity-100 group-focus:opacity-100">
            在图片上左右拖动
          </span>
        </div>
      </div>
    </figure>
  );
};
