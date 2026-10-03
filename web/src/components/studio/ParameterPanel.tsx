import React, { useState, useMemo } from 'react';
import {
  Download,
  RotateCw,
  Sliders,
  Shield,
  Palette,
  Zap,
  Play,
  Cpu,
  ChevronDown,
  ChevronUp,
  RotateCcw,
  Users,
  Sparkles,
  SunMedium,
  ShieldCheck,
} from 'lucide-react';
import { useStudioStore } from '@/stores/useStudioStore';
import type { BatchPipeline } from '@/hooks/useBatchPipeline';
import { api } from '@/services/api';
import { buildQuickSwapPayload } from '@/utils/swapPayload';
import { useToast } from '@/components/ui/ToastProvider';
import type { SwapParameters } from '@/types';
import { apiErrorMessage } from '@/utils/apiError';

/* ── Scenario Presets definition ────────────────────────────── */
interface PresetItem {
  id: string;
  name: string;
  desc: string;
  icon: React.ComponentType<{ className?: string }>;
  params: Partial<SwapParameters>;
}

const PRESETS: PresetItem[] = [
  {
    id: 'portrait',
    name: '写真质感',
    desc: '高保真写真质感，强化正脸细节与五官清晰度',
    icon: Sparkles,
    params: {
      swapper_model: 'auto',
      identity_blend_strength: 0.92,
      similarity_threshold: 0.35,
      color_transfer_mode: 'boundary',
      enable_dense_mask: true,
    },
  },
  {
    id: 'fast',
    name: '极速日常',
    desc: '快速出图，泛化能力强，适合多场景抓拍与日常照',
    icon: Zap,
    params: {
      swapper_model: 'inswapper_128',
      identity_blend_strength: 0.9,
      similarity_threshold: 0.35,
      color_transfer_mode: 'boundary',
      enable_dense_mask: true,
    },
  },
  {
    id: 'atmosphere',
    name: '复杂光影',
    desc: '适度保留原片侧影高光，减少侧脸与暗光生硬边界',
    icon: SunMedium,
    params: {
      swapper_model: 'auto',
      identity_blend_strength: 0.85,
      similarity_threshold: 0.3,
      color_transfer_mode: 'boundary',
      enable_dense_mask: true,
    },
  },
  {
    id: 'occlusion',
    name: '遮挡防护',
    desc: '收紧识别截断，预防眼镜、饰品与发丝穿模贴图',
    icon: ShieldCheck,
    params: {
      swapper_model: 'auto',
      identity_blend_strength: 0.88,
      similarity_threshold: 0.42,
      color_transfer_mode: 'boundary',
      enable_dense_mask: true,
    },
  },
];

/* ── Labelled slider with value badge ───────────────────────── */
const ParamSlider: React.FC<{
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  format?: (v: number) => string;
  accent?: 'primary' | 'warn' | 'accent';
  hints?: [string, string, string];
}> = ({ label, value, min, max, step, onChange, format, accent = 'primary', hints }) => {
  const badgeClass =
    accent === 'warn' ? 'val-badge-warn' : accent === 'accent' ? 'val-badge-accent' : 'val-badge-primary';

  const accentColor = accent === 'warn' ? '#f59e0b' : accent === 'accent' ? '#06b6d4' : '#5b5fef';
  const percentage = Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));

  return (
    <div className="space-y-1.5 pt-2">
      <div className="flex items-center justify-between">
        <label htmlFor={`parameter-${label}`} className="text-xs font-medium text-zinc-300">
          {label}
        </label>
        <span className={badgeClass}>{format ? format(value) : value}</span>
      </div>
      <div className="relative flex items-center">
        <input
          id={`parameter-${label}`}
          aria-label={label}
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          className="w-full h-1.5 rounded-full transition-all cursor-pointer"
          style={{
            background: `linear-gradient(to right, ${accentColor} 0%, ${accentColor} ${percentage}%, rgba(255, 255, 255, 0.08) ${percentage}%, rgba(255, 255, 255, 0.08) 100%)`,
            accentColor,
          }}
        />
      </div>
      {hints && (
        <div className="flex justify-between text-2xs text-zinc-400 font-mono">
          <span>{hints[0]}</span>
          <span>{hints[1]}</span>
          <span>{hints[2]}</span>
        </div>
      )}
    </div>
  );
};

/* ── Main component ─────────────────────────────────────────── */
import { snapshotRefinementInput } from '@/features/refinement/sessionRecipe';

export const ParameterPanel: React.FC<{ batch: BatchPipeline }> = ({ batch }) => {
  const toast = useToast();
  const [singlePending, setIsSingleRunning] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const {
    parameters,
    updateParameters,
    sourceGallery,
    sourcePeople,
    personMappings,
    setPersonMappings,
    setIsMatrixOpen,
    targetImages,
    activeTargetIndex,
    updateTargetImageById,
    isProcessing,
    batchProgress,
  } = useStudioStore();

  const isSingleRunning =
    singlePending || (!isProcessing && targetImages.some((target) => target.status === 'PROCESSING'));
  const currentTarget = targetImages[activeTargetIndex] || null;

  const activePresetId = useMemo(() => {
    for (const preset of PRESETS) {
      const p = preset.params;
      const match =
        (p.swapper_model === undefined || p.swapper_model === parameters.swapper_model) &&
        (p.identity_blend_strength === undefined ||
          Math.abs(p.identity_blend_strength - parameters.identity_blend_strength) < 0.005) &&
        (p.similarity_threshold === undefined ||
          Math.abs(p.similarity_threshold - parameters.similarity_threshold) < 0.005) &&
        (p.color_transfer_mode === undefined || p.color_transfer_mode === parameters.color_transfer_mode);
      if (match) return preset.id;
    }
    return 'custom';
  }, [parameters]);

  const handleApplyPreset = (preset: PresetItem) => {
    updateParameters({
      ...preset.params,
      allow_research_models:
        preset.params.swapper_model === 'hyperswap_1c_256' ? true : parameters.allow_research_models,
    });
    toast({
      title: `已套用「${preset.name}」预设`,
      description: preset.desc,
      kind: 'info',
    });
  };

  /* Run quick single preview on current canvas image */
  const handleQuickSingleSwap = async () => {
    if (
      !currentTarget ||
      !sourceGallery.length ||
      useStudioStore.getState().isProcessing ||
      useStudioStore.getState().targetImages.some((target) => target.status === 'PROCESSING')
    )
      return;

    const built = buildQuickSwapPayload(
      { sourcePeople, sourceGallery, targetImages, personMappings },
      currentTarget,
      parameters,
      currentTarget.manualMask?.dataUrl || undefined,
    );
    if (!built.ready) {
      setIsMatrixOpen(true);
      return;
    }
    if (built.autoCreated) setPersonMappings(built.mappings);
    const submittedTargetId = currentTarget.id;
    const refinementSnapshot = snapshotRefinementInput(currentTarget, built.payload.person_mappings ?? [], '');

    setIsSingleRunning(true);
    updateTargetImageById(submittedTargetId, {
      status: 'PROCESSING',
      error: undefined,
      refinedUrl: undefined,
      acceptedRefinement: undefined,
      refinementResultUnavailable: undefined,
      refinementSessionId: undefined,
      refinementInput: undefined,
      pendingRefinement: undefined,
      refinementError: undefined,
    });

    try {
      const res = await api.swapSync(built.payload);

      const swapData = res.data;
      updateTargetImageById(submittedTargetId, {
        processedUrl: swapData.processed_base64 || swapData.processed_url,
        refinedUrl: undefined,
        refinementInput: {
          ...refinementSnapshot,
          baseline_image_url: swapData.processed_base64 || swapData.processed_url,
        },
        status: 'SUCCESS',
        costMs: swapData.cost_ms,
        facesDetected: swapData.faces_detected,
        facesSwapped: swapData.faces_swapped,
        swappedPersonIds: swapData.swapped_person_indices,
        personOutcomes: swapData.person_outcomes,
        swapWarning: swapData.warning,
        matchedSimilarities: swapData.matched_similarities,
        modelRouting: swapData.model_routing,
      });
      if (swapData.warning) {
        toast({ title: '部分人物未完成换脸', description: swapData.warning, kind: 'warning', modal: true });
      }
    } catch (err: unknown) {
      console.error('Quick swap failed:', err);
      const message = apiErrorMessage(err, '换脸处理失败');
      updateTargetImageById(submittedTargetId, {
        status: 'FAILED',
        error: message,
      });
      toast({ title: '单图换脸失败', description: `${currentTarget.name}\n${message}`, kind: 'error' });
    } finally {
      setIsSingleRunning(false);
    }
  };

  const handleResetDefaults = () => {
    updateParameters({
      similarity_threshold: 0.35,
      identity_blend_strength: 0.9,
      enable_color_transfer: true,
      color_transfer_mode: 'boundary',
      enable_dense_mask: true,
      source_mode: 'best-match',
      swapper_model: 'auto',
      allow_research_models: false,
    });
    toast({ title: '已恢复算法推荐默认参数', kind: 'info' });
  };

  return (
    <div className="flex flex-col h-full bg-surface select-none overflow-hidden">
      {/* ── Panel header ── */}
      <div className="flex items-center justify-between px-4 h-11 border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          <div className="icon-badge-primary">
            <Sliders className="w-4 h-4" />
          </div>
          <span className="text-xs font-bold text-zinc-100">3. 换脸控制</span>
        </div>
        <button
          type="button"
          onClick={handleResetDefaults}
          disabled={isProcessing || isSingleRunning}
          className="btn btn-ghost btn-sm text-zinc-300 hover:text-white gap-1 text-xs px-2.5 py-1 h-7 rounded-lg"
          title="恢复推荐默认算法参数"
        >
          <RotateCcw className="w-3.5 h-3.5 text-primary-soft" />
          <span>重置默认</span>
        </button>
      </div>

      {/* ── Scrollable param body ── */}
      <fieldset
        disabled={isProcessing || isSingleRunning}
        className="flex-1 overflow-y-auto min-h-0 min-w-0 p-3 space-y-2.5"
      >
        {/* Multi-person matrix trigger */}
        <button
          type="button"
          onClick={() => setIsMatrixOpen(true)}
          disabled={!targetImages.length || isProcessing || isSingleRunning}
          className="w-full flex items-center justify-between p-2.5 rounded-xl border border-border bg-surface-2 hover:border-primary-border hover:bg-surface-hover transition-all text-left text-xs group cursor-pointer"
          title="打开合影人物映射矩阵，配置源人物与目标人脸对应关系"
        >
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="p-1.5 rounded-lg bg-accent-muted text-accent shrink-0">
              <Users className="w-4 h-4 group-hover:scale-110 transition-transform" />
            </div>
            <div>
              <div className="font-bold text-zinc-100">角色映射</div>
              <div className="text-2xs text-zinc-400">
                {personMappings.length > 0 ? `已配置 ${personMappings.length} 组人物绑定` : '配置源人物与目标人脸对应'}
              </div>
            </div>
          </div>
          <span
            className={`chip ${personMappings.length > 0 ? 'chip-accent' : 'chip-neutral'} text-xs font-mono font-medium shrink-0`}
          >
            {personMappings.length > 0 ? `${personMappings.length} 人已映射` : '点击配置'}
          </span>
        </button>

        {/* ── Scenario Presets selector ── */}
        <div className="rounded-xl border border-border bg-surface-2 p-3 space-y-2.5">
          <div className="flex items-center justify-between text-xs">
            <span className="font-bold text-zinc-200 flex items-center gap-1.5">
              <Sparkles className="w-4 h-4 text-primary-soft" />
              场景推荐预设
            </span>
            {activePresetId === 'custom' && <span className="chip chip-neutral text-xs font-medium">自定义微调</span>}
          </div>
          <div className="grid grid-cols-2 gap-2">
            {PRESETS.map((preset) => {
              const isActive = activePresetId === preset.id;
              const Icon = preset.icon;
              return (
                <button
                  key={preset.id}
                  type="button"
                  aria-pressed={isActive}
                  onClick={() => handleApplyPreset(preset)}
                  disabled={isProcessing || isSingleRunning}
                  className={`relative flex items-start gap-2 p-2.5 rounded-xl text-left transition-all border cursor-pointer ${
                    isActive
                      ? 'border-primary/60 bg-primary/10 text-zinc-100 ring-1 ring-primary/30 shadow-sm font-medium'
                      : 'border-border bg-surface-3/40 hover:bg-surface-hover hover:border-border-md text-zinc-300'
                  }`}
                  title={preset.desc}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 whitespace-nowrap text-xs font-semibold text-zinc-100">
                      <Icon className={`h-3.5 w-3.5 shrink-0 ${isActive ? 'text-primary-soft' : 'text-zinc-400'}`} />
                      {preset.name}
                    </div>
                    <p className="mt-1 line-clamp-2 text-2xs leading-snug text-zinc-400">{preset.desc}</p>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* ── Identity blend strength (Primary visual parameter) ── */}
        <div className="rounded-xl border border-border bg-surface-2 p-3 space-y-2">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-warn" />
            <span className="text-xs font-semibold text-zinc-200">特征融合强度</span>
          </div>
          <ParamSlider
            label="换入特征强度"
            value={parameters.identity_blend_strength}
            min={0.45}
            max={0.95}
            step={0.01}
            onChange={(v) => updateParameters({ identity_blend_strength: v })}
            format={(v) => `${Math.round(v * 100)}%`}
            accent="warn"
            hints={['45% 保守自然', '90% 默认推荐', '95% 强烈换入']}
          />
        </div>

        {/* ── Advanced Parameters (Progressive disclosure) ── */}
        <div className="rounded-xl border border-border bg-surface-2 overflow-hidden">
          <button
            type="button"
            aria-expanded={showAdvanced}
            onClick={() => setShowAdvanced((v) => !v)}
            className="w-full flex items-center justify-between px-3 py-2.5 hover:bg-surface-hover transition-colors text-left"
          >
            <div className="flex items-center gap-2">
              <Sliders className="w-4 h-4 text-primary-soft" />
              <span className="text-xs font-semibold text-zinc-200">高级算法调校</span>
              {activePresetId === 'custom' && <span className="chip chip-primary text-[9px] font-normal">已微调</span>}
            </div>
            <div className="flex items-center gap-1.5 text-zinc-400">
              <span className="text-2xs font-mono text-zinc-500">
                {parameters.swapper_model === 'auto' ? '自动' : parameters.swapper_model} · τ=
                {parameters.similarity_threshold.toFixed(2)}
              </span>
              {showAdvanced ? (
                <ChevronUp className="w-3.5 h-3.5 text-zinc-400" />
              ) : (
                <ChevronDown className="w-3.5 h-3.5 text-zinc-400" />
              )}
            </div>
          </button>
          {showAdvanced && (
            <div className="p-3 space-y-3.5 border-t border-border bg-surface-1/40">
              {/* 换脸模型 */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <label
                    htmlFor="swapper-model-select"
                    className="text-xs font-medium text-zinc-300 flex items-center gap-1.5"
                  >
                    <Cpu className="w-3.5 h-3.5 text-primary-soft" />
                    换脸推理模型
                  </label>
                </div>
                <select
                  id="swapper-model-select"
                  value={parameters.swapper_model}
                  onChange={(e) => {
                    const model = e.target.value as typeof parameters.swapper_model;
                    updateParameters({
                      swapper_model: model,
                      allow_research_models: model === 'hyperswap_1c_256' ? true : parameters.allow_research_models,
                    });
                  }}
                  className="w-full text-xs font-medium"
                >
                  <option value="auto">自动 · 稳定基线 (InSwapper)</option>
                  <option value="inswapper_128">InSwapper 128 · 稳定基线</option>
                  <option value="hyperswap_1c_256">HyperSwap 256 · 质量优先 (高清特写)</option>
                  <option value="ghost_1_256">Ghost 256 · 实验性模型</option>
                </select>

                <label className="flex items-center gap-2 text-xs leading-normal text-zinc-300 cursor-pointer pt-0.5">
                  <input
                    type="checkbox"
                    checked={parameters.allow_research_models}
                    onChange={(e) => updateParameters({ allow_research_models: e.target.checked })}
                    className="mt-0"
                  />
                  <span>允许研究模型（HyperSwap 高清特写评估）</span>
                </label>
              </div>

              {/* 余弦匹配阈值 */}
              <div className="pt-2 border-t border-border/50 space-y-1">
                <div className="flex items-center gap-1.5 text-xs font-medium text-zinc-300">
                  <Shield className="w-3.5 h-3.5 text-primary-soft" />
                  <span>特征匹配过滤</span>
                </div>
                <ParamSlider
                  label="余弦匹配阈值 (τ)"
                  value={parameters.similarity_threshold}
                  min={0.2}
                  max={0.8}
                  step={0.01}
                  onChange={(v) => updateParameters({ similarity_threshold: v })}
                  format={(v) => v.toFixed(2)}
                  hints={['0.20 宽松适配', '0.35 推荐基准', '0.80 严格匹配']}
                />
              </div>

              {/* 光影色温适配 */}
              <div className="pt-2 border-t border-border/50 space-y-1.5">
                <label
                  htmlFor="color-transfer-select"
                  className="text-xs font-medium text-zinc-300 flex items-center gap-1.5"
                >
                  <Palette className="w-3.5 h-3.5 text-accent" />
                  光影色温适配
                </label>
                <select
                  id="color-transfer-select"
                  value={parameters.color_transfer_mode}
                  onChange={(e) => {
                    const mode = e.target.value as 'off' | 'boundary';
                    updateParameters({ color_transfer_mode: mode, enable_color_transfer: mode !== 'off' });
                  }}
                  className="w-full text-xs font-medium"
                >
                  <option value="boundary">边界平滑环（推荐：仅平滑边缘过渡）</option>
                  <option value="off">关闭色彩平滑</option>
                </select>
              </div>
            </div>
          )}
        </div>
      </fieldset>

      {/* ── Action footer ── */}
      <div className="border-t border-border p-3 space-y-2.5 shrink-0 max-h-[55%] overflow-y-auto bg-surface-1/80">
        <label
          className={`flex items-start gap-2.5 rounded-xl border p-2.5 text-xs cursor-pointer ${batch.aiRefinement ? 'border-primary/40 bg-primary/10' : 'border-border bg-surface-2'}`}
        >
          <input
            type="checkbox"
            checked={batch.aiRefinement}
            disabled={isProcessing || isSingleRunning}
            onChange={(event) => batch.setAiRefinement(event.target.checked)}
            className="mt-0.5 accent-primary"
          />
          <span>
            <span className="font-semibold text-zinc-100">换脸后接续 AI 首轮精修</span>
            <span className="mt-0.5 block text-2xs leading-normal text-zinc-400">
              由视觉模型自动诊断瑕疵并单次整图优化（按需调用，可能产生费用）
            </span>
          </span>
        </label>
        {batch.waitingReason && (
          <p
            role="status"
            className="rounded-lg border border-amber-400/20 bg-amber-400/5 p-2.5 text-xs leading-5 text-amber-200"
          >
            {batch.waitingReason} 排队任务会自动继续，无需重复点击。
          </p>
        )}
        {batch.queryError && (
          <div role="alert" className="rounded-lg border border-err/30 bg-err/10 p-2.5 text-xs text-err">
            <p className="break-words">{batch.queryError}</p>
            {batch.canRetry && (
              <button onClick={batch.retryConnection} className="btn btn-sm btn-outline mt-2 w-full justify-center">
                <RotateCw className="h-3 w-3" />
                重试连接
              </button>
            )}
          </div>
        )}
        {/* Dynamic Action: Batch or Single */}
        {targetImages.length > 1 || batch.aiRefinement ? (
          <div className="space-y-2 pt-0.5">
            <button
              type="button"
              onClick={batch.startBatch}
              disabled={!targetImages.length || !sourceGallery.length || isProcessing || isSingleRunning}
              className="w-full btn btn-lg btn-primary justify-center gap-2 font-bold text-sm shadow-md shadow-indigo-600/20"
              title={`启动全部 ${targetImages.length} 张图片的批量换脸流水线`}
            >
              {isProcessing ? (
                <>
                  <Zap className="w-4 h-4 animate-spin" />
                  <span>批量运行中 ({batchProgress}%)</span>
                </>
              ) : (
                <>
                  <Play className="w-4 h-4 fill-white" />
                  <span>{targetImages.length > 1 ? `启动批量换脸 (${targetImages.length} 张)` : '开始换脸并精修'}</span>
                </>
              )}
            </button>
            <button
              type="button"
              onClick={handleQuickSingleSwap}
              disabled={!currentTarget || sourceGallery.length === 0 || isSingleRunning || isProcessing}
              className="w-full btn btn-sm btn-outline justify-center text-xs text-zinc-200 hover:text-white font-medium py-2 border-border hover:border-primary-border/60 hover:bg-surface-hover/80 transition-all"
              title="仅在当前单图上快速试换，预览参数效果"
            >
              {isSingleRunning ? (
                <>
                  <Zap className="w-3.5 h-3.5 animate-spin text-primary-soft" />
                  <span>单图试换中…</span>
                </>
              ) : (
                <span>仅试换当前图 (#{activeTargetIndex + 1})</span>
              )}
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={handleQuickSingleSwap}
            disabled={!currentTarget || sourceGallery.length === 0 || isSingleRunning || isProcessing}
            className="w-full btn btn-lg btn-primary justify-center gap-2 font-bold text-sm shadow-md shadow-indigo-600/20"
          >
            {isSingleRunning ? (
              <>
                <Zap className="w-4 h-4 animate-spin" />
                <span>换脸运算中…</span>
              </>
            ) : (
              <>
                <Play className="w-4 h-4 fill-white" />
                <span>开始单图换脸</span>
              </>
            )}
          </button>
        )}
        {batch.completedCount > 0 && (
          <button
            onClick={batch.downloadZip}
            disabled={batch.isExporting || isProcessing}
            className="btn btn-sm btn-outline w-full justify-center gap-1.5 text-xs"
          >
            <Download className="h-3.5 w-3.5 text-accent" />
            {batch.isExporting ? '正在打包…' : '导出批量结果 ZIP'}
          </button>
        )}
      </div>
    </div>
  );
};
