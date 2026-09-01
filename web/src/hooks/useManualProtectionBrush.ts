import { useEffect, useRef } from 'react';
import type { PointerEvent as ReactPointerEvent, RefObject } from 'react';
import type { ViewMode } from '../types';
import { createManualMaskPixels } from '../utils/manualMask';

interface ManualProtectionBrushOptions {
  activeTargetIndex: number;
  brushSize: number;
  imageContainerRef: RefObject<HTMLDivElement | null>;
  imageViewportHeight?: number;
  imageViewportWidth?: number;
  isEraser: boolean;
  maskDataUrl: string | null;
  setMaskDataUrl: (dataUrl: string | null) => void;
  viewMode: ViewMode;
}

/** Owns the canvas-only state and serialization rules for the protection brush. */
export function useManualProtectionBrush({
  activeTargetIndex,
  brushSize,
  imageContainerRef,
  imageViewportHeight,
  imageViewportWidth,
  isEraser,
  maskDataUrl,
  setMaskDataUrl,
  viewMode,
}: ManualProtectionBrushOptions) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const isPaintingRef = useRef(false);
  const lastPointRef = useRef<{ x: number; y: number } | null>(null);

  // Brush pixels use the rendered image viewport coordinate system. The
  // persisted grayscale mask is reprojected whenever that viewport changes.
  useEffect(() => {
    const canvas = canvasRef.current;
    const container = imageContainerRef.current;
    if (!canvas || !container) return;

    const rect = container.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width));
    const height = Math.max(1, Math.round(rect.height));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }

    const context = canvas.getContext('2d');
    context?.clearRect(0, 0, canvas.width, canvas.height);
    if (!context || !maskDataUrl) return;

    let cancelled = false;
    const image = new Image();
    image.onload = () => {
      if (cancelled) return;
      const buffer = document.createElement('canvas');
      buffer.width = canvas.width;
      buffer.height = canvas.height;
      const bufferContext = buffer.getContext('2d');
      if (!bufferContext) return;
      bufferContext.drawImage(image, 0, 0, buffer.width, buffer.height);
      const overlay = bufferContext.getImageData(0, 0, buffer.width, buffer.height);
      for (let index = 0; index < overlay.data.length; index += 4) {
        const painted = overlay.data[index] > 127;
        overlay.data[index] = 245;
        overlay.data[index + 1] = 158;
        overlay.data[index + 2] = 11;
        overlay.data[index + 3] = painted ? 115 : 0;
      }
      context.putImageData(overlay, 0, 0);
    };
    image.src = maskDataUrl;
    return () => {
      cancelled = true;
    };
  }, [activeTargetIndex, imageContainerRef, imageViewportHeight, imageViewportWidth, maskDataUrl, viewMode]);

  const handlePointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    isPaintingRef.current = true;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    lastPointRef.current = { x, y };

    const context = canvas.getContext('2d');
    if (!context) return;
    context.beginPath();
    context.arc(x, y, brushSize / 2, 0, Math.PI * 2);
    context.fillStyle = isEraser ? 'rgba(0,0,0,1)' : 'rgba(245, 158, 11, 0.45)';
    context.globalCompositeOperation = isEraser ? 'destination-out' : 'source-over';
    context.fill();
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!isPaintingRef.current || !canvas || !lastPointRef.current) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;

    const context = canvas.getContext('2d');
    if (!context) return;
    context.strokeStyle = isEraser ? 'rgba(0,0,0,1)' : 'rgba(245, 158, 11, 0.45)';
    context.lineWidth = brushSize;
    context.lineCap = 'round';
    context.lineJoin = 'round';
    context.globalCompositeOperation = isEraser ? 'destination-out' : 'source-over';
    context.beginPath();
    context.moveTo(lastPointRef.current.x, lastPointRef.current.y);
    context.lineTo(x, y);
    context.stroke();
    lastPointRef.current = { x, y };
  };

  const handlePointerEnd = () => {
    if (!isPaintingRef.current) return;
    isPaintingRef.current = false;
    lastPointRef.current = null;

    const canvas = canvasRef.current;
    if (!canvas) return;
    const source = canvas.getContext('2d')?.getImageData(0, 0, canvas.width, canvas.height);
    if (!source) return;
    const { pixels, hasPainted } = createManualMaskPixels(source.data);
    if (!hasPainted) {
      setMaskDataUrl(null);
      return;
    }

    const exportCanvas = document.createElement('canvas');
    exportCanvas.width = canvas.width;
    exportCanvas.height = canvas.height;
    const exportContext = exportCanvas.getContext('2d');
    if (!exportContext) return;
    exportContext.putImageData(new ImageData(pixels, exportCanvas.width, exportCanvas.height), 0, 0);
    setMaskDataUrl(exportCanvas.toDataURL('image/png'));
  };

  return {
    canvasRef,
    clear: () => {
      if (maskDataUrl) setMaskDataUrl(null);
    },
    handlePointerDown,
    handlePointerEnd,
    handlePointerMove,
  };
}
