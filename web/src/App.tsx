import React, { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { BrainCircuit, History, Layers } from 'lucide-react';
import { AppHeader } from '@/components/layout/AppHeader';
import { SessionWorkbench } from '@/features/refinement/components/SessionWorkbench';
import { HistoryWorkspace } from '@/features/history/components/HistoryWorkspace';
import { useWorkbenchStore } from '@/features/refinement/workbenchStore';
import { useJobActivityStore } from '@/stores/useJobActivityStore';
import { ResponsiveWorkflowAccess, type WorkflowPanel } from '@/components/layout/ResponsiveWorkflowAccess';
import { SourceGallery } from '@/components/studio/SourceGallery';
import { TargetCanvas } from '@/components/studio/TargetCanvas';
import { ParameterPanel } from '@/components/studio/ParameterPanel';
import { BatchStatusBar } from '@/components/studio/BatchStatusBar';
import { useBatchPipeline } from '@/hooks/useBatchPipeline';
import { LazyModalFallback } from '@/components/ui/ErrorBoundary';
import { useToast } from '@/components/ui/ToastProvider';

const MultiPersonMatrixModal = lazy(() =>
  import('@/components/studio/MultiPersonMatrixModal').then((module) => ({ default: module.MultiPersonMatrixModal })),
);
import { useStudioStore } from '@/stores/useStudioStore';
import { api } from '@/services/api';
import { ProjectControls } from '@/features/projects/ProjectControls';
import { useAcceptedResults } from '@/features/refinement/hooks/useAcceptedResults';

export const App: React.FC = () => {
  const batch = useBatchPipeline();
  useAcceptedResults();
  const view = useWorkbenchStore((state) => state.view);
  const navigate = useWorkbenchStore((state) => state.navigate);
  const [openPanel, setOpenPanel] = useState<WorkflowPanel | null>(null);
  const {
    setServerHealth,
    addSourcePerson,
    addSourceReference,
    clearSourceGallery,
    addTargetImages,
    clearTargetImages,
    setActiveTargetIndex,
    setSelectedTargetPersonId,
    sourcePeople,
    sourceGallery,
    targetImages,
    personMappings,
    isMatrixOpen,
  } = useStudioStore();
  // The mapping dialog must sit above the canvas, not behind a still-open mobile drawer.
  useEffect(() => {
    if (isMatrixOpen) setOpenPanel(null);
  }, [isMatrixOpen]);
  const readiness = useMemo(() => {
    const analyzedTargets = targetImages.filter((target) => Boolean(target.faces?.length)).length;
    return [
      {
        label: `源身份 ${sourcePeople.length}`,
        complete: sourcePeople.length > 0 && sourceGallery.length > 0,
      },
      { label: `目标图 ${targetImages.length}`, complete: targetImages.length > 0 },
      {
        label: `人脸分析 ${analyzedTargets}/${targetImages.length}`,
        complete: targetImages.length > 0 && analyzedTargets === targetImages.length,
      },
      { label: `人物映射 ${personMappings.length}`, complete: personMappings.length > 0 },
    ];
  }, [personMappings.length, sourceGallery.length, sourcePeople.length, targetImages]);
  const toast = useToast();
  // Load server health status on startup
  useEffect(() => {
    const fetchHealth = async () => {
      try {
        const health = await api.getHealth();
        setServerHealth(health);
      } catch (e) {
        setServerHealth(null);
        console.warn('Backend service offline or initializing:', e);
      }
    };
    fetchHealth();
    const interval = setInterval(fetchHealth, 10000);
    return () => clearInterval(interval);
  }, [setServerHealth]);

  // Fast sample data loader
  const handleLoadSampleData = async () => {
    try {
      const res = await axios.get('/api/v1/samples');
      const data = res.data;

      // 1. Load sample sources
      clearSourceGallery();
      const samplePersonId = addSourcePerson('示例人物');
      data.sources.forEach((s: any, idx: number) => {
        addSourceReference({
          id: `sample_src_${idx}`,
          name: s.name,
          kind: 'manual',
          imageData: s.url,
          personId: samplePersonId,
          yaw: s.yaw,
          yawLabel: s.yaw === 0 ? '0° 正脸 (Frontal)' : s.yaw > 0 ? `+${s.yaw}° 右微侧` : `${s.yaw}° 左微侧`,
        });
      });

      // 2. Load sample target images
      clearTargetImages();
      const targetItems = data.targets.map((t: any, idx: number) => ({
        id: `sample_tgt_${idx}`,
        name: t.name,
        url: t.url,
        status: 'IDLE' as const,
      }));
      addTargetImages(targetItems);
      setActiveTargetIndex(0);
      setSelectedTargetPersonId(null);
    } catch (e) {
      console.error('Failed to load sample dataset:', e);
      toast({ title: '载入测试样例失败', description: '请确保后端服务已启动后重试。', kind: 'error' });
    }
  };

  const jobs = useJobActivityStore((state) => state.jobs);
  const activeJobsCount = useMemo(
    () => jobs.filter((job) => job.status === 'QUEUED' || job.status === 'PROCESSING').length,
    [jobs],
  );
  const hasRefinableTargets = useMemo(
    () => targetImages.some((target) => Boolean(target.processedUrl || target.refinementSessionId)),
    [targetImages],
  );

  const workspaceTabs = [
    { id: 'prepare' as const, label: '准备与换脸', icon: Layers, badge: null },
    {
      id: 'refinement' as const,
      label: 'AI 精修',
      icon: BrainCircuit,
      badge: hasRefinableTargets ? (
        <span
          className="h-1.5 w-1.5 rounded-full bg-cyan-400 shadow-[0_0_6px_rgba(6,182,212,0.8)]"
          title="已有可精修图片"
        />
      ) : null,
    },
    {
      id: 'history' as const,
      label: '历史',
      icon: History,
      badge:
        activeJobsCount > 0 ? (
          <span className="chip chip-accent text-[10px] px-1.5 py-0 leading-tight font-mono">{activeJobsCount}</span>
        ) : null,
    },
  ];

  return (
    <div className="flex flex-col h-dvh w-full overflow-hidden bg-background text-zinc-200 font-sans">
      {/* Precision System Bar with Integrated Workspace Navigation */}
      <AppHeader onLoadSampleData={view === 'prepare' ? handleLoadSampleData : undefined}>
        <nav
          aria-label="工作区切换"
          className="flex items-center gap-1 p-0.5 rounded-xl bg-surface-2/90 border border-white/[0.08] shadow-inner"
        >
          {workspaceTabs.map(({ id, label, icon: Icon, badge }) => {
            const isActive = view === id;
            return (
              <button
                key={id}
                aria-label={label}
                aria-current={isActive ? 'page' : undefined}
                onClick={() => navigate(id)}
                className={
                  'min-h-8 rounded-lg px-3 text-xs font-medium inline-flex items-center gap-1.5 transition-all cursor-pointer select-none ' +
                  (isActive
                    ? 'bg-primary text-white shadow-sm font-semibold'
                    : 'text-zinc-400 hover:text-zinc-200 hover:bg-surface-hover/70')
                }
              >
                <Icon className={`h-3.5 w-3.5 ${isActive ? 'text-white' : 'text-zinc-400'}`} />
                <span className="whitespace-nowrap">{label}</span>
                {badge}
              </button>
            );
          })}
        </nav>
      </AppHeader>
      <ProjectControls />
      {view === 'prepare' && (
        <ResponsiveWorkflowAccess
          sourcePanel={<SourceGallery />}
          parameterPanel={<ParameterPanel batch={batch} />}
          openPanel={openPanel}
          onOpenPanel={setOpenPanel}
          readiness={readiness}
        />
      )}
      {/* Preparation controls remain isolated from historical/session state. */}
      <div
        className={
          view === 'prepare'
            ? 'flex-1 grid grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)] xl:grid-cols-[290px_minmax(0,1fr)_320px] overflow-hidden min-h-0'
            : 'hidden'
        }
      >
        {/* Left: Source Identity Gallery — 272px */}
        <aside className="hidden lg:block h-full border-r border-border overflow-hidden">
          <SourceGallery />
        </aside>

        {/* Center: Target Canvas — flexible */}
        <main className="h-full flex flex-col min-w-0 overflow-hidden">
          <TargetCanvas />
        </main>

        {/* Right: Algorithm & Parameter Panel — 288px */}
        <aside className="hidden xl:block h-full border-l border-border overflow-hidden">
          <ParameterPanel batch={batch} />
        </aside>
      </div>

      {view === 'refinement' && <SessionWorkbench />}
      {view === 'history' && <HistoryWorkspace />}
      <div className={view === 'prepare' ? 'contents' : 'hidden'}>
        <BatchStatusBar batch={batch} />
      </div>
      <Suspense fallback={<LazyModalFallback label="人物矩阵" />}>
        <MultiPersonMatrixModal />
      </Suspense>
    </div>
  );
};

export default App;
