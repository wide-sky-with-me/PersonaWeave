import { useEffect } from 'react';
import type { ViewMode } from '@/types';

interface UseStudioShortcutsProps {
  enabled?: boolean;
  viewMode: ViewMode;
  setViewMode: (mode: ViewMode) => void;
  brushSize: number;
  setBrushSize: (size: number) => void;
  undoManualMask: () => void;
  redoManualMask: () => void;
  canUndoMask: boolean;
  canRedoMask: boolean;
  onHoldCompareChange?: (isHolding: boolean) => void;
}

export function useStudioShortcuts({
  enabled = true,
  viewMode,
  setViewMode,
  brushSize,
  setBrushSize,
  undoManualMask,
  redoManualMask,
  canUndoMask,
  canRedoMask,
  onHoldCompareChange,
}: UseStudioShortcutsProps) {
  useEffect(() => {
    if (!enabled) {
      onHoldCompareChange?.(false);
      return;
    }
    const handleKeyDown = (e: KeyboardEvent) => {
      if (document.querySelector('[role="dialog"]')) return;
      const activeElement = document.activeElement;
      if (activeElement) {
        const tag = activeElement.tagName.toLowerCase();
        if (
          tag === 'input' ||
          tag === 'textarea' ||
          tag === 'select' ||
          (activeElement as HTMLElement).isContentEditable
        ) {
          return;
        }
      }

      if (viewMode === 'BRUSH') {
        const isUndo = (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !e.shiftKey;
        const isRedo =
          ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && e.shiftKey) ||
          ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y');
        if (isUndo) {
          e.preventDefault();
          if (canUndoMask) undoManualMask();
          return;
        }
        if (isRedo) {
          e.preventDefault();
          if (canRedoMask) redoManualMask();
          return;
        }

        if (e.key === '[') {
          e.preventDefault();
          setBrushSize(Math.max(10, brushSize - 4));
          return;
        }
        if (e.key === ']') {
          e.preventDefault();
          setBrushSize(Math.min(60, brushSize + 4));
          return;
        }
      }

      if (!e.ctrlKey && !e.altKey && !e.metaKey) {
        if (e.key === '1') {
          setViewMode('SINGLE');
          return;
        }
        if (e.key === '2') {
          setViewMode('SPLIT');
          return;
        }
        if (e.key === '3') {
          setViewMode('MAGNIFIER');
          return;
        }
        if (e.key === '4') {
          setViewMode('BRUSH');
          return;
        }

        if (e.key === '\\' && onHoldCompareChange && !e.repeat) {
          e.preventDefault();
          onHoldCompareChange(true);
          return;
        }
      }
    };

    const handleKeyUp = (e: KeyboardEvent) => {
      if (e.key === '\\' && onHoldCompareChange) {
        onHoldCompareChange(false);
      }
    };

    const resetCompare = () => onHoldCompareChange?.(false);
    window.addEventListener('blur', resetCompare);
    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('keyup', handleKeyUp);
    return () => {
      window.removeEventListener('blur', resetCompare);
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('keyup', handleKeyUp);
    };
  }, [
    enabled,
    viewMode,
    setViewMode,
    brushSize,
    setBrushSize,
    undoManualMask,
    redoManualMask,
    canUndoMask,
    canRedoMask,
    onHoldCompareChange,
  ]);
}
