import React, { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { AppHeader } from './components/layout/AppHeader';
import { ResponsiveWorkflowAccess, type WorkflowPanel } from './components/layout/ResponsiveWorkflowAccess';
import { SourceGallery } from './components/studio/SourceGallery';
import { TargetCanvas } from './components/studio/TargetCanvas';
import { ParameterPanel } from './components/studio/ParameterPanel';
import { BatchPipelineDrawer } from './components/studio/BatchPipelineDrawer';
import { LazyModalFallback } from './components/ui/ErrorBoundary';
import { useToast } from './components/ui/ToastProvider';

const MultiPersonMatrixModal = lazy(() => import('./components/studio/MultiPersonMatrixModal').then((module) => ({ default: module.MultiPersonMatrixModal })));
import { useStudioStore } from './stores/useStudioStore';
import { api } from './services/api';

export const App: React.FC = () => {
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
    setIsMatrixOpen,
  } = useStudioStore();
  const readiness = useMemo(() => {
    const analyzedTargets = targetImages.filter((target) => Boolean(target.faces?.length)).length;
    return [
      { label: `源身份 ${sourcePeople.length}`, complete: sourcePeople.length > 0 && sourceGallery.length > 0, action: 'sources' as const },
      { label: `目标图 ${targetImages.length}`, complete: targetImages.length > 0 },
      { label: `人脸分析 ${analyzedTargets}/${targetImages.length}`, complete: targetImages.length > 0 && analyzedTargets === targetImages.length },
      { label: `人物映射 ${personMappings.length}`, complete: personMappings.length > 0, action: 'mapping' as const },
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

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-background text-zinc-200 font-sans">
      {/* Compact System Bar */}
      <AppHeader onLoadSampleData={handleLoadSampleData} />
      <ResponsiveWorkflowAccess
        sourcePanel={<SourceGallery />}
        parameterPanel={<ParameterPanel />}
        openPanel={openPanel}
        onOpenPanel={setOpenPanel}
        onOpenMapping={() => setIsMatrixOpen(true)}
        readiness={readiness}
      />
      {/* Three-Column Studio Workbench */}
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-[240px_minmax(0,1fr)] xl:grid-cols-[272px_minmax(0,1fr)_288px] overflow-hidden min-h-0">
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
          <ParameterPanel />
        </aside>
      </div>

      {/* Compact Status Bar / Batch Pipeline */}
      <BatchPipelineDrawer />
      <Suspense fallback={<LazyModalFallback label="人物矩阵" />}><MultiPersonMatrixModal /></Suspense>
    </div>
  );
};

export default App;
