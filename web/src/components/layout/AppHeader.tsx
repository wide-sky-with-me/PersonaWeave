import React from 'react';
import { Cpu, BookOpen, RefreshCw, Activity, History } from 'lucide-react';
import { useStudioStore } from '../../stores/useStudioStore';
import { useJobActivityStore } from '../../stores/useJobActivityStore';
import { RecentJobsPopover } from './RecentJobsPopover';

interface AppHeaderProps {
  onLoadSampleData?: () => void;
}

export const AppHeader: React.FC<AppHeaderProps> = ({ onLoadSampleData }) => {
  const { serverHealth, isProcessing } = useStudioStore();
  const { jobs, isOpen, setOpen } = useJobActivityStore();
  const activeJobs = jobs.filter((job) => job.status === 'QUEUED' || job.status === 'PROCESSING').length;

  const isGpu   = serverHealth?.gpu_available  ?? true;
  const isReady = serverHealth?.pipeline_ready  ?? true;

  return (
    <header className="relative h-11 border-b border-border glass shrink-0 px-4 flex items-center justify-between z-50">
      {/* ── Brand ── */}
      <div className="flex items-center gap-2.5 min-w-0">
        {/* Logo mark: two overlapping squares, no gradient */}
        <div
          className="w-6 h-6 rounded-md bg-primary/90 flex items-center justify-center shrink-0 shadow-primary"
          aria-hidden
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <rect x="1" y="1" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.9" />
            <rect x="6" y="6" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.45" />
          </svg>
        </div>
        <span className="font-semibold text-sm text-zinc-100 tracking-tight">
          PersonaWeave
        </span>
        <span className="hidden sm:inline-block text-zinc-600 text-xs font-mono">
          Studio
        </span>
      </div>

      {/* ── Center: processing indicator ── */}
      {isProcessing && (
        <div role="status" aria-live="polite" className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-warn-muted border border-warn-border text-warn text-xs font-medium animate-in fade-in">
          <Activity className="w-3 h-3 animate-pulse" />
          <span>处理中…</span>
        </div>
      )}

      {/* ── Right: Actions & Status ── */}
      <div className="flex items-center gap-2">
        {/* Sample loader */}
        {onLoadSampleData && (
          <button
            onClick={onLoadSampleData}
            disabled={isProcessing}
            className="btn btn-outline btn-sm gap-1.5"
            title="一键载入内置合影测试集与人物素材"
          >
            <RefreshCw className="w-3 h-3 text-primary" />
            <span className="hidden sm:inline">载入样例</span>
          </button>
        )}

        <button className="btn btn-outline btn-sm gap-1.5" aria-haspopup="dialog" aria-expanded={isOpen} onClick={() => setOpen(!isOpen)} title="查看最近任务">
          <History className="w-3 h-3 text-primary" />
          <span className="hidden sm:inline">任务</span>
          {activeJobs > 0 && <span className="chip chip-accent">{activeJobs}</span>}
        </button>
        {/* Hardware status chip */}
        <div
          className={`chip ${isGpu ? 'chip-accent' : 'chip-neutral'} gap-1.5`}
          title={isGpu ? 'GPU 硬件加速已启用' : 'CPU 模式'}
        >
          <Cpu className="w-3 h-3" />
          <span>{isGpu ? 'GPU' : 'CPU'}</span>
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              isReady ? 'bg-ok animate-pulse-dot' : 'bg-warn'
            }`}
            aria-hidden="true"
          />
          <span className="sr-only">{isReady ? '就绪' : '初始化中'}</span>
        </div>

        {/* API docs */}
        <a
          href="/docs"
          target="_blank"
          rel="noreferrer"
          className="btn btn-ghost btn-sm"
          title="查看 API 文档"
        >
          <BookOpen className="w-3 h-3" />
          <span className="hidden md:inline">API</span>
        </a>
      </div>
      <RecentJobsPopover />
    </header>
  );
};