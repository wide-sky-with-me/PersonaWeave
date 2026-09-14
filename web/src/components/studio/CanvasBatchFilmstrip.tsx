import React from 'react';
import { Upload, CheckCircle, Clock, AlertCircle, X } from 'lucide-react';
import { motion } from 'framer-motion';
import type { TargetImageItem } from '@/types';

interface CanvasBatchFilmstripProps {
  targetImages: TargetImageItem[];
  activeTargetIndex: number;
  onSelectTargetIndex: (idx: number) => void;
  onRemoveTargetImage: (idx: number) => void;
  onUploadClick: () => void;
  isProcessing: boolean;
}

export const CanvasBatchFilmstrip: React.FC<CanvasBatchFilmstripProps> = ({
  targetImages,
  activeTargetIndex,
  onSelectTargetIndex,
  onRemoveTargetImage,
  onUploadClick,
  isProcessing,
}) => {
  return (
    <div className="h-[72px] border-t border-white/10 bg-[#121215]/90 backdrop-blur-xl px-3 flex items-center gap-3 overflow-x-auto shrink-0 select-none">
      <button
        onClick={onUploadClick}
        disabled={isProcessing}
        className="w-14 h-14 rounded-xl border border-dashed border-white/25 hover:border-primary bg-surface-2 hover:bg-primary-muted/30 flex flex-col items-center justify-center text-zinc-300 hover:text-primary transition-all shrink-0 active:scale-95 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
        title="添加更多待换脸图片"
      >
        <Upload className="w-4 h-4" />
        <span className="text-xs mt-0.5 font-semibold">加图</span>
      </button>

      <div className="flex items-center gap-2.5 overflow-x-auto py-1">
        {targetImages.map((item, idx) => {
          const isActive = idx === activeTargetIndex;
          return (
            <motion.div
              key={item.id}
              whileHover={{ scale: 1.03 }}
              transition={{ type: 'spring', stiffness: 300, damping: 25 }}
              className={`group relative w-14 h-14 rounded-xl border-2 transition-all shrink-0 bg-black/50 ${
                isActive
                  ? 'border-primary shadow-lg shadow-primary/30 ring-1 ring-primary/40'
                  : 'border-white/15 hover:border-white/40 opacity-80 hover:opacity-100'
              }`}
            >
              <button
                type="button"
                onClick={() => onSelectTargetIndex(idx)}
                aria-label={`选择图片 ${idx + 1}：${item.name}`}
                aria-pressed={isActive}
                className="absolute inset-0 overflow-hidden rounded-[10px] cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300"
              >
                <img
                  src={item.refinedUrl || item.processedUrl || item.url}
                  alt={item.name}
                  className="w-full h-full object-cover"
                />
              </button>
              <div className="absolute top-0.5 left-0.5 px-1.5 py-0.5 rounded bg-black/80 text-xs font-mono text-zinc-100 font-bold shadow">
                #{idx + 1}
              </div>

              {/* Delete / Remove image button on hover */}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onRemoveTargetImage(idx);
                }}
                className="absolute -top-1.5 -right-1.5 w-6 h-6 rounded-full bg-black/80 hover:bg-red-500 text-zinc-200 hover:text-white flex items-center justify-center opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 transition-all z-10"
                disabled={isProcessing || item.status === 'PROCESSING'}
                title="移除此图片"
                aria-label={`移除图片 ${idx + 1}：${item.name}`}
              >
                <X className="w-3.5 h-3.5" />
              </button>

              <div className="absolute bottom-0.5 right-0.5">
                {item.status === 'SUCCESS' && <CheckCircle className="w-3.5 h-3.5 text-emerald-400 drop-shadow" />}
                {item.status === 'PROCESSING' && (
                  <Clock className="w-3.5 h-3.5 text-amber-400 animate-spin drop-shadow" />
                )}
                {item.status === 'FAILED' && <AlertCircle className="w-3.5 h-3.5 text-red-400 drop-shadow" />}
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
};
