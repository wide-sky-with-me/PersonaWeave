import React from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { motion } from 'framer-motion';
import { Columns, Maximize2, ZoomIn, Sparkles, Eye, EyeOff, Paintbrush, Keyboard, X } from 'lucide-react';
import type { ViewMode } from '@/types';

interface CanvasTopToolbarProps {
  currentImageName?: string | null;
  hasCurrentImage: boolean;
  selectedTargetPersonId: number | null;
  onSelectTargetPersonId: (id: number | null) => void;
  distinctGlobalPersons: number[];
  canVlmArbitrate: boolean;
  isArbitrating: boolean;
  onVlmArbitrate: () => void;
  showFaceMarkers: boolean;
  onToggleShowFaceMarkers: () => void;
  viewMode: ViewMode;
  onChangeViewMode: (mode: ViewMode) => void;
}

const modes = [
  { id: 'SINGLE', label: '单图', icon: Maximize2 },
  { id: 'SPLIT', label: '对比', icon: Columns },
  { id: 'MAGNIFIER', label: '放大镜', icon: ZoomIn },
  { id: 'BRUSH', label: '保护笔', icon: Paintbrush },
] as const;

/** Keep image tools together; result actions have one persistent entry below the canvas. */
export const CanvasTopToolbar: React.FC<CanvasTopToolbarProps> = (props) => (
  <div className="shrink-0 space-y-2 border-b border-white/10 bg-surface-1/90 px-3 py-2.5">
    <div className="flex min-w-0 items-center justify-between gap-2">
      <span title={props.currentImageName || undefined} className="min-w-0 truncate text-xs font-medium text-zinc-200">
        {props.currentImageName || '2. 目标画布'}
      </span>
      {props.hasCurrentImage && (
        <select
          aria-label="定位画面人物"
          title="用于定位与身份检查；实际换脸对象以右侧角色映射为准"
          value={props.selectedTargetPersonId ?? 'all'}
          onChange={(e) => props.onSelectTargetPersonId(e.target.value === 'all' ? null : Number(e.target.value))}
          className="max-w-[48%] shrink-0"
        >
          <option value="all">查看全部人物</option>
          {props.distinctGlobalPersons.map((id) => (
            <option key={id} value={id}>
              定位人物 #{id}
            </option>
          ))}
        </select>
      )}
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <div
        role="group"
        aria-label="画布工具"
        className="flex shrink-0 items-center gap-0.5 rounded-xl border border-white/10 bg-surface-2 p-1"
      >
        {modes.map(({ id, label, icon: Icon }, index) => (
          <button
            key={id}
            type="button"
            aria-pressed={props.viewMode === id}
            disabled={!props.hasCurrentImage}
            onClick={() => props.onChangeViewMode(id)}
            title={`${label} · 快捷键 ${index + 1}`}
            className="relative flex min-h-8 items-center gap-1.5 whitespace-nowrap rounded-lg px-2 text-xs text-zinc-300 disabled:opacity-40 sm:px-2.5"
          >
            {props.viewMode === id && (
              <motion.span
                layoutId="canvas-tool"
                transition={{ type: 'spring', stiffness: 300, damping: 25 }}
                className="absolute inset-0 rounded-lg border border-indigo-400/25 bg-indigo-500/15"
              />
            )}
            <Icon className="relative h-3.5 w-3.5 shrink-0" />
            <span className="relative">{label}</span>
          </button>
        ))}
      </div>
      <div className="flex items-center gap-1">
        {props.hasCurrentImage && (
          <button
            type="button"
            onClick={props.onToggleShowFaceMarkers}
            aria-pressed={props.showFaceMarkers}
            aria-label="显示人脸定位框"
            title="显示或隐藏人脸定位框"
            className={`btn btn-icon ${props.showFaceMarkers ? 'bg-cyan-500/10 text-cyan-300' : 'btn-ghost'}`}
          >
            {props.showFaceMarkers ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
          </button>
        )}
        {props.hasCurrentImage && props.canVlmArbitrate && (
          <button
            type="button"
            onClick={props.onVlmArbitrate}
            disabled={props.isArbitrating}
            className="btn btn-sm btn-ghost whitespace-nowrap"
            title="用视觉模型辅助检查人物编号，可能产生模型费用"
          >
            <Sparkles className={`h-3.5 w-3.5 ${props.isArbitrating ? 'animate-spin' : ''}`} />
            {props.isArbitrating ? '检查中…' : 'AI 身份检查'}
          </button>
        )}
        <Dialog.Root>
          <Dialog.Trigger asChild>
            <button className="btn btn-icon btn-ghost" aria-label="快捷键指南">
              <Keyboard className="h-4 w-4" />
            </button>
          </Dialog.Trigger>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 z-[90] bg-black/60 backdrop-blur-sm" />
            <Dialog.Content
              aria-describedby="shortcuts-description"
              className="fixed left-1/2 top-1/2 z-[91] w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-white/10 bg-surface-2 p-5 shadow-elevated"
            >
              <div className="flex items-center justify-between gap-2">
                <Dialog.Title className="text-sm font-semibold">专业快捷键指南</Dialog.Title>
                <Dialog.Close className="btn btn-icon btn-ghost" aria-label="关闭快捷键指南">
                  <X className="h-4 w-4" />
                </Dialog.Close>
              </div>
              <Dialog.Description id="shortcuts-description" className="mt-1 text-xs text-zinc-400">
                在目标画布中使用，输入文字时不会触发。
              </Dialog.Description>
              <dl className="mt-4 space-y-2 text-xs">
                {[
                  ['单图 / 对比 / 放大镜 / 保护笔', '1 / 2 / 3 / 4'],
                  ['按住查看原图，松开还原', '\\'],
                  ['缩小 / 放大画笔', '[ / ]'],
                  ['撤销画笔笔触', 'Ctrl / ⌘ + Z'],
                  ['重做画笔笔触', 'Ctrl / ⌘ + Shift + Z'],
                ].map(([label, key]) => (
                  <div key={label} className="flex items-center justify-between gap-3 rounded-lg bg-white/[0.03] p-2.5">
                    <dt className="text-zinc-300">{label}</dt>
                    <dd className="shrink-0 font-mono text-indigo-200">{key}</dd>
                  </div>
                ))}
              </dl>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      </div>
    </div>
  </div>
);
