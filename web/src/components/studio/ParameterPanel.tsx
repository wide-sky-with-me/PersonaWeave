import React, { useState } from 'react';
import {
  Sliders,
  Shield,
  Palette,
  Zap,
  Play,
  Cpu,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import { useStudioStore } from '../../stores/useStudioStore';
import { api } from '../../services/api';
import { buildQuickSwapPayload } from '../../utils/swapPayload';
import { useToast } from '../ui/ToastProvider';

/* ── Collapsible section wrapper ────────────────────────────── */
const Section: React.FC<{
  icon: React.ReactNode;
  title: string;
  accent?: 'primary' | 'accent' | 'warn';
  defaultOpen?: boolean;
  inactiveNote?: string;
  children: React.ReactNode;
}> = ({ icon, title, accent = 'primary', defaultOpen = true, inactiveNote, children }) => {
  const [open, setOpen] = useState(defaultOpen);

  const accentClasses: Record<string, string> = {
    primary: 'text-primary',
    accent:  'text-accent',
    warn:    'text-warn',
  };

  return (
    <div className="rounded-xl border border-border bg-surface-2 overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-3 py-2.5 hover:bg-surface-hover transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className={`${accentClasses[accent]}`}>{icon}</span>
          <span className="text-xs font-semibold text-zinc-200">{title}</span>
          {inactiveNote && (
            <span className="chip chip-warn text-[9px] normal-case tracking-normal font-normal" title={inactiveNote}>
              暂未接入生产
            </span>
          )}
        </div>
        {open ? (
          <ChevronUp className="w-3.5 h-3.5 text-zinc-600" />
        ) : (
          <ChevronDown className="w-3.5 h-3.5 text-zinc-600" />
        )}
      </button>
      {open && <div className="px-3 pb-3 space-y-3 border-t border-border">{children}</div>}
    </div>
  );
};

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

  return (
    <div className="space-y-2 pt-2">
      <div className="flex items-center justify-between">
        <label htmlFor={`parameter-${label}`} className="text-xs text-zinc-400">{label}</label>
        <span className={badgeClass}>{format ? format(value) : value}</span>
      </div>
      <input
        id={`parameter-${label}`}
        aria-label={label}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full"
        style={{
          accentColor: accent === 'warn' ? '#f59e0b' : accent === 'accent' ? '#06b6d4' : '#5b5fef',
        }}
      />
      {hints && (
        <div className="flex justify-between text-2xs text-zinc-600 font-mono">
          <span>{hints[0]}</span>
          <span>{hints[1]}</span>
          <span>{hints[2]}</span>
        </div>
      )}
    </div>
  );
};


/* ── Main component ─────────────────────────────────────────── */
export const ParameterPanel: React.FC = () => {
  const toast = useToast();
  const [isSingleRunning, setIsSingleRunning] = useState(false);
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
  } = useStudioStore();

  const currentTarget = targetImages[activeTargetIndex] || null;

  /* Run quick single preview on current canvas image */
  const handleQuickSingleSwap = async () => {
    if (!currentTarget || sourceGallery.length === 0 || isProcessing) return;

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

    setIsSingleRunning(true);
    updateTargetImageById(submittedTargetId, {
      status: 'PROCESSING',
      refinedUrl: undefined,
      refinementWorkspace: undefined,
    });

    try {
      const res = await api.swapSync(built.payload);

      const swapData = res.data;
      updateTargetImageById(submittedTargetId, {
        processedUrl:        swapData.processed_base64 || swapData.processed_url,
        refinedUrl:          undefined,
        refinementWorkspace: undefined,
        status:              'SUCCESS',
        costMs:              swapData.cost_ms,
        facesDetected:       swapData.faces_detected,
        facesSwapped:        swapData.faces_swapped,
        swappedPersonIds:    swapData.swapped_person_indices,
        personOutcomes:      swapData.person_outcomes,
        swapWarning:         swapData.warning,
        matchedSimilarities: swapData.matched_similarities,
        modelRouting:        swapData.model_routing,
      });
      if (swapData.warning) {
        toast({ title: '部分人物未完成换脸', description: swapData.warning, kind: 'warning' });
      }
    } catch (err: any) {
      console.error('Quick swap failed:', err);
      updateTargetImageById(submittedTargetId, {
        status: 'FAILED',
        error:  err.response?.data?.message || err.message || '换脸处理失败',
      });
    } finally {
      setIsSingleRunning(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-surface select-none overflow-hidden">
      {/* ── Panel header ── */}
      <div className="flex items-center justify-between px-4 h-10 border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          <div className="icon-badge-primary">
            <Sliders className="w-3.5 h-3.5" />
          </div>
          <span className="text-xs font-semibold text-zinc-200">算法调优</span>
        </div>
      </div>

      {/* ── Scrollable param body ── */}
      <div className="flex-1 overflow-y-auto min-h-0 p-3 space-y-2">

        {/* ── Model selection ── */}
        <Section icon={<Cpu className="w-3.5 h-3.5" />} title="换脸模型" accent="accent">
          <div className="pt-2 space-y-2.5">
            <select
              value={parameters.swapper_model}
              onChange={(e) => {
                const model = e.target.value as typeof parameters.swapper_model;
                updateParameters({
                  swapper_model: model,
                  allow_research_models: model === 'hyperswap_1c_256' ? true : parameters.allow_research_models,
                });
              }}
              className="w-full"
            >
              <option value="auto">自动 · 稳定基线</option>
              <option value="inswapper_128">InSwapper 128 · 稳定基线</option>
              <option value="hyperswap_1c_256">HyperSwap 256 · 质量优先 / ResearchRAIL</option>
              <option value="ghost_1_256">Ghost 256 · 实验性，不推荐</option>
            </select>

            <label className="flex items-start gap-2 text-2xs leading-relaxed text-zinc-500 cursor-pointer">
              <input
                type="checkbox"
                checked={parameters.allow_research_models}
                onChange={(e) => updateParameters({ allow_research_models: e.target.checked })}
                className="mt-0.5"
              />
              <span>允许研究模型（HyperSwap / ResearchRAIL 评估场景）</span>
            </label>

            <p className="text-2xs leading-relaxed text-zinc-600">
              自动模式默认使用稳定的 InSwapper；明确允许研究模型后，大尺寸高置信人脸可路由到 HyperSwap。Ghost 仅保留手动实验。
            </p>
          </div>
        </Section>

        {/* ── Identity blend strength ── */}
        <Section icon={<Zap className="w-3.5 h-3.5" />} title="身份融合强度" accent="warn">
          <ParamSlider
            label="换入特征强度"
            value={parameters.identity_blend_strength}
            min={0.45}
            max={0.90}
            step={0.01}
            onChange={(v) => updateParameters({ identity_blend_strength: v })}
            format={(v) => `${Math.round(v * 100)}%`}
            accent="warn"
            hints={['45% 保守', '72% 均衡', '90% 强烈']}
          />
          <p className="text-2xs leading-relaxed text-zinc-600">
            控制 InSwapper 换入身份的可见程度；小脸和大侧脸仍会自动降低实际强度以减少破面。
          </p>
        </Section>


        {/* ── Similarity threshold ── */}
        <Section icon={<Shield className="w-3.5 h-3.5" />} title="余弦匹配阈值 (τ)" accent="primary">
          <ParamSlider
            label="相似度截断"
            value={parameters.similarity_threshold}
            min={0.20}
            max={0.80}
            step={0.01}
            onChange={(v) => updateParameters({ similarity_threshold: v })}
            format={(v) => v.toFixed(2)}
            hints={['0.20 宽松', '0.38 推荐', '0.80 严格']}
          />
        </Section>

        {/* ── Color harmonization ── */}
        <Section icon={<Palette className="w-3.5 h-3.5" />} title="光影色温适配" accent="accent">
          <div className="pt-2 space-y-2">
            <select
              value={parameters.color_transfer_mode}
              onChange={(e) => {
                const mode = e.target.value as 'off' | 'boundary';
                updateParameters({ color_transfer_mode: mode, enable_color_transfer: mode !== 'off' });
              }}
              className="w-full"
            >
              <option value="off">关闭</option>
              <option value="boundary">仅边界（生产默认）</option>
            </select>
            <p className="text-2xs leading-relaxed text-zinc-600">
              生产换脸路径只修正换脸边缘的窄过渡环，不改变脸部本体色调与整体饱和度。
            </p>
          </div>
        </Section>
      </div>

      {/* ── Action footer ── */}
      <div className="border-t border-border p-3 space-y-2 shrink-0">
        {/* Multi-person matrix */}

        {/* Quick single swap */}
        <button
          onClick={handleQuickSingleSwap}
          disabled={!currentTarget || sourceGallery.length === 0 || isSingleRunning || isProcessing}
          className="w-full btn btn-lg btn-primary justify-center"
        >
          {isSingleRunning ? (
            <>
              <Zap className="w-3.5 h-3.5 animate-spin" />
              <span>运算中…</span>
            </>
          ) : (
            <>
              <Play className="w-3.5 h-3.5 fill-white" />
              <span>当前图快速试换</span>
            </>
          )}
        </button>
      </div>
    </div>
  );
};
