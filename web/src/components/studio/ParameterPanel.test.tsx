import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useStudioStore } from '@/stores/useStudioStore';
import type { BatchPipeline } from '@/hooks/useBatchPipeline';
import { ParameterPanel } from './ParameterPanel';

vi.mock('@/services/api', () => ({ api: { swapSync: vi.fn() } }));
const toast = vi.hoisted(() => vi.fn());
vi.mock('@/components/ui/ToastProvider', () => ({ useToast: () => toast }));

const batch = {
  aiRefinement: false,
  setAiRefinement: vi.fn(),
  waitingReason: null,
  queryError: null,
  canRetry: false,
  retryConnection: vi.fn(),
  startBatch: vi.fn(),
  completedCount: 0,
} as unknown as BatchPipeline;

beforeEach(() => useStudioStore.setState(useStudioStore.getInitialState()));
afterEach(cleanup);

describe('ParameterPanel presets', () => {
  it('recognizes the recommended defaults instead of calling them a custom tuning', () => {
    render(<ParameterPanel batch={batch} />);
    expect(screen.getByText('推荐默认')).toBeInTheDocument();
    expect(screen.queryByText('自定义微调')).not.toBeInTheDocument();
    expect(screen.queryByText('已微调')).not.toBeInTheDocument();
  });

  it('reports a custom tuning once a parameter leaves every preset and the defaults', () => {
    useStudioStore.getState().updateParameters({ identity_blend_strength: 0.7 });
    render(<ParameterPanel batch={batch} />);
    expect(screen.getByText('自定义微调')).toBeInTheDocument();
    expect(screen.getByText('已微调')).toBeInTheDocument();
  });

  it('applies a preset without touching the research-model opt-in', () => {
    render(<ParameterPanel batch={batch} />);
    fireEvent.click(screen.getByRole('button', { name: /严格匹配/ }));
    const parameters = useStudioStore.getState().parameters;
    expect(parameters.similarity_threshold).toBe(0.42);
    expect(parameters.allow_research_models).toBe(false);
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: '已套用「严格匹配」预设' }));
  });

  it('does not describe the matching threshold as occlusion protection', () => {
    render(<ParameterPanel batch={batch} />);
    const strict = screen.getByRole('button', { name: /严格匹配/ });
    expect(strict).toHaveTextContent('识别阈值 0.42');
    expect(strict).toHaveTextContent('保护在所有预设下始终开启');
    expect(screen.queryByText(/遮挡防护/)).not.toBeInTheDocument();
  });

  it('resets to the recommended defaults', () => {
    useStudioStore.getState().updateParameters({ identity_blend_strength: 0.7, similarity_threshold: 0.5 });
    render(<ParameterPanel batch={batch} />);
    fireEvent.click(screen.getByTitle('恢复推荐默认算法参数'));
    const parameters = useStudioStore.getState().parameters;
    expect([parameters.identity_blend_strength, parameters.similarity_threshold]).toEqual([0.9, 0.35]);
    expect(screen.getByText('推荐默认')).toBeInTheDocument();
  });
  it('disables AI continuation while service readiness is unknown or unavailable', () => {
    render(<ParameterPanel batch={batch} />);
    expect(screen.getByRole('checkbox', { name: /换脸后接续 AI 首轮精修/ })).toBeDisabled();
    expect(screen.getByText('AI 服务未配置或未就绪；本地换脸仍可直接使用')).toBeInTheDocument();
  });

  it('enables AI continuation only after the server confirms readiness', () => {
    useStudioStore.setState({
      serverHealth: {
        status: 'ok',
        app_name: 'PersonaWeave',
        version: 'test',
        pipeline_ready: true,
        providers: ['CPUExecutionProvider'],
        gpu_available: false,
        ai_refinement_ready: true,
      },
    });
    render(<ParameterPanel batch={batch} />);
    expect(screen.getByRole('checkbox', { name: /换脸后接续 AI 首轮精修/ })).toBeEnabled();
  });
});
