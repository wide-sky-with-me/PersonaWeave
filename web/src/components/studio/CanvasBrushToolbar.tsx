import React from 'react';
import { Paintbrush, Eraser, Undo2, Redo2, Trash2 } from 'lucide-react';

interface CanvasBrushToolbarProps {
  isEraser: boolean;
  onToggleEraser: (eraser: boolean) => void;
  brushSize: number;
  onChangeBrushSize: (size: number) => void;
  canUndo: boolean;
  onUndo: () => void;
  canRedo: boolean;
  onRedo: () => void;
  hasMaskData: boolean;
  onClear: () => void;
}

export const CanvasBrushToolbar: React.FC<CanvasBrushToolbarProps> = ({
  isEraser,
  onToggleEraser,
  brushSize,
  onChangeBrushSize,
  canUndo,
  onUndo,
  canRedo,
  onRedo,
  hasMaskData,
  onClear,
}) => {
  return (
    <div className="absolute top-16 left-2 right-2 sm:left-1/2 sm:right-auto sm:-translate-x-1/2 px-3 sm:px-4 py-2 rounded-2xl bg-zinc-900/90 backdrop-blur-xl border border-amber-500/40 shadow-2xl flex items-center gap-3.5 overflow-x-auto z-40 animate-in fade-in slide-in-from-top-2">
      <div
        id="brush-canvas-help"
        className="flex items-center gap-1.5 text-xs text-amber-300 font-medium whitespace-nowrap"
        title="键盘可操作工具栏，绘制需使用触控笔、触摸或鼠标"
      >
        <Paintbrush className="w-4 h-4 shrink-0" />
        <span>保护笔：涂抹不希望被替换的区域</span>
      </div>
      <div className="h-4 w-px bg-white/10" />
      <button
        type="button"
        onClick={() => onToggleEraser(false)}
        className={`min-h-11 px-3 py-2 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-all ${
          !isEraser
            ? 'bg-amber-500/20 text-amber-300 border border-amber-400/50 shadow-sm'
            : 'text-zinc-400 hover:text-zinc-200'
        }`}
      >
        <Paintbrush className="w-3.5 h-3.5" />
        <span>画笔</span>
      </button>
      <button
        type="button"
        onClick={() => onToggleEraser(true)}
        className={`min-h-11 px-3 py-2 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-all ${
          isEraser
            ? 'bg-amber-500/20 text-amber-300 border border-amber-400/50 shadow-sm'
            : 'text-zinc-400 hover:text-zinc-200'
        }`}
      >
        <Eraser className="w-3.5 h-3.5" />
        <span>橡皮</span>
      </button>
      <div className="flex items-center gap-2 text-xs text-zinc-300">
        <span className="text-xs text-zinc-400">粗细:</span>
        <input
          aria-label="保护画笔粗细"
          title="画笔粗细调节快捷键：[ 键减小，] 键增大"
          type="range"
          min="10"
          max="60"
          value={brushSize}
          onChange={(e) => onChangeBrushSize(Number(e.target.value))}
          className="w-20 h-11 accent-amber-500 cursor-pointer"
        />
        <span className="font-mono text-xs text-amber-300">{brushSize}px</span>
      </div>
      <button
        type="button"
        onClick={onUndo}
        disabled={!canUndo}
        className="min-w-11 min-h-11 p-2 rounded-lg text-zinc-400 hover:text-amber-300 disabled:opacity-30 disabled:pointer-events-none transition-colors"
        title="撤销遮罩编辑 (Ctrl+Z)"
        aria-label="撤销遮罩编辑"
      >
        <Undo2 className="w-3.5 h-3.5" />
      </button>
      <button
        type="button"
        onClick={onRedo}
        disabled={!canRedo}
        className="min-w-11 min-h-11 p-2 rounded-lg text-zinc-400 hover:text-amber-300 disabled:opacity-30 disabled:pointer-events-none transition-colors"
        title="重做遮罩编辑 (Ctrl+Y 或 Ctrl+Shift+Z)"
        aria-label="重做遮罩编辑"
      >
        <Redo2 className="w-3.5 h-3.5" />
      </button>
      <button
        type="button"
        onClick={onClear}
        disabled={!hasMaskData}
        className="min-h-11 px-3 py-2 rounded-lg text-xs text-zinc-400 hover:text-rose-400 hover:bg-rose-500/10 disabled:opacity-30 disabled:pointer-events-none transition-colors flex items-center gap-1"
        title="清空当前涂抹遮罩"
      >
        <Trash2 className="w-3.5 h-3.5" />
        <span>清空</span>
      </button>
    </div>
  );
};
