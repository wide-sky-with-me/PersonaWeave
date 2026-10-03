import React from 'react';
import { Cpu, BookOpen, RefreshCw, Activity, History } from 'lucide-react';
import { useStudioStore } from '@/stores/useStudioStore';
import { useJobActivityStore } from '@/stores/useJobActivityStore';
import { RecentJobsPopover } from '@/features/history/components/RecentJobsPopover';

interface AppHeaderProps {
  onLoadSampleData?: () => void;
  children?: React.ReactNode;
}

export const AppHeader: React.FC<AppHeaderProps> = ({ onLoadSampleData, children }) => {
  const { serverHealth, isProcessing } = useStudioStore();
  const { jobs, isOpen, setOpen } = useJobActivityStore();
  const activeJobs = jobs.filter((job) => job.status === 'QUEUED' || job.status === 'PROCESSING').length;

  const isReady = serverHealth?.pipeline_ready === true;
  const modelProviders = Object.values(serverHealth?.model_providers || {});
  const cudaModels = modelProviders.filter((providers) => providers.includes('CUDAExecutionProvider')).length;
  const executionLabel = !modelProviders.length
    ? '本地就绪'
    : cudaModels === modelProviders.length
      ? 'CUDA 已加载'
      : cudaModels
        ? '部分模型 CPU'
        : 'CPU 模式';
  const executionTitle = !modelProviders.length
    ? '本地流水线就绪状态；不代表 GPU 实际执行能力'
    : Object.entries(serverHealth?.model_providers || {})
        .map(([name, providers]) => `${name}: ${providers.join(', ')}`)
        .join('\n') + '\n模型实际加载的后端；不等同于每个算子都在 GPU 执行。';

  return (
    <header className="relative grid shrink-0 grid-cols-[1fr_auto] items-center gap-x-2 gap-y-2 border-b border-white/10 bg-surface-1 px-3 py-2 sm:grid-cols-[auto_1fr_auto] sm:px-4 sm:py-1.5 z-50">
      {/* ── Brand ── */}
      <div className="flex items-center gap-2.5 min-w-0">
        {/* Logo mark: precision AI Weaver mark with subtle gradient and glow */}
        <div
          className="w-7 h-7 rounded-xl bg-gradient-to-br from-primary via-primary-hover to-accent flex items-center justify-center shrink-0 shadow-glow-primary border border-border-strong relative"
          aria-hidden
        >
          <svg width="15" height="15" viewBox="0 0 14 14" fill="none">
            <rect x="1" y="1" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.95" />
            <rect x="6" y="6" width="7" height="7" rx="1.5" fill="white" fillOpacity="0.4" />
          </svg>
        </div>
        <div className="flex items-baseline gap-1.5">
          <span className="font-semibold text-sm text-zinc-100 tracking-tight">PersonaWeave</span>
          <span className="hidden sm:inline-block text-cyan-400/90 text-[10px] font-mono uppercase tracking-wider font-semibold">
            Studio
          </span>
        </div>
      </div>

      {/* ── Center: workspace switcher & processing indicator ── */}
      <div className="order-3 col-span-2 flex items-center justify-center gap-2 sm:order-2 sm:col-span-1">
        {children}
        {isProcessing && (
          <div
            role="status"
            aria-live="polite"
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-warn-muted border border-warn-border text-warn text-xs font-medium animate-in fade-in"
          >
            <Activity className="w-3 h-3 animate-pulse" />
            <span className="hidden sm:inline">处理中…</span>
          </div>
        )}
      </div>

      {/* ── Right: Actions & Status ── */}
      <div className="order-2 flex items-center justify-end gap-1.5 sm:order-3">
        {/* Sample loader */}
        {onLoadSampleData && (
          <button
            aria-label="载入样例"
            onClick={onLoadSampleData}
            disabled={isProcessing}
            className="btn btn-outline btn-sm gap-1.5"
            title="一键载入内置合影测试集与人物素材"
          >
            <RefreshCw className="w-3 h-3 text-primary-soft" />
            <span className="hidden sm:inline">载入样例</span>
          </button>
        )}

        {activeJobs > 0 && (
          <button
            aria-label="查看任务动态"
            className="btn btn-outline btn-sm gap-1.5"
            aria-haspopup="dialog"
            aria-expanded={isOpen}
            onClick={() => setOpen(!isOpen)}
            title="查看最近任务"
          >
            <History className="w-3 h-3 text-primary-soft" />
            <span className="hidden sm:inline">任务</span>
            {activeJobs > 0 && <span className="chip chip-accent">{activeJobs}</span>}
          </button>
        )}
        {/* Available providers do not prove which execution backend a session actually uses. */}
        <div className={'chip gap-1.5 ' + (isReady ? 'chip-accent' : 'chip-neutral')} title={executionTitle}>
          <Cpu className="w-3 h-3" />
          <span className="hidden sm:inline whitespace-nowrap">
            {!serverHealth ? '服务未知' : isReady ? executionLabel : '初始化中'}
          </span>
          <span className={'w-1.5 h-1.5 rounded-full ' + (isReady ? 'bg-ok' : 'bg-warn')} aria-hidden="true" />
        </div>

        {/* API docs */}
        <a
          href="/docs"
          target="_blank"
          rel="noreferrer"
          className="btn btn-ghost btn-sm hidden sm:inline-flex"
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
