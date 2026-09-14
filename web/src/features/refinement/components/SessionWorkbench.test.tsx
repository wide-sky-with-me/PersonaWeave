import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/services/api';
import { useStudioStore } from '@/stores/useStudioStore';
import { sessionApi } from '../sessionApi';
import { useWorkbenchStore } from '../workbenchStore';
import type { SessionDetail, SessionTurn } from '../sessionTypes';
import { SessionWorkbench } from './SessionWorkbench';
import { HistoryWorkspace } from '@/features/history/components/HistoryWorkspace';

vi.mock('@/services/api', () => ({
  api: { getAIModels: vi.fn(), getRefinementTask: vi.fn(), listBatchTaskHistory: vi.fn() },
}));
vi.mock('../sessionApi', () => ({
  sessionApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    turn: vi.fn(),
    cancel: vi.fn(),
    accept: vi.fn(),
    delete: vi.fn(),
  },
}));
const analysis = (base = 'v0'): SessionTurn => ({
  id: 'analysis-' + base,
  request_id: 'analysis-request-' + base,
  base_version_id: base,
  mode: 'discuss',
  message: '检查画面',
  status: 'completed',
  diagnosis: {
    assistant_message: '下颌边缘存在融合问题',
    needs_edit: true,
    mapping_uncertain: false,
    issues: [
      {
        category: 'blending',
        person_id: 2,
        region: '右下颌边缘',
        description: '边缘出现灰白直线',
        evidence: '当前边缘比原图更亮',
        suggestion: '修复明暗过渡',
        severity: 'medium',
        confidence: 'high',
        uncertainty: '',
      },
    ],
    people: [],
    edit_plan: { changes: ['修复下颌边缘'], preserve: ['保留肤色'], instructions: '修复过渡' },
    uncertainties: [],
  },
  created_at: 'now',
  updated_at: 'now',
});
const fixture = (): SessionDetail => ({
  id: 's1',
  target_id: 'target',
  title: '合影精修',
  revision: 5,
  status: 'idle',
  original_url: '/original',
  baseline_version_id: 'v0',
  accepted_version_id: 'v1',
  accepted_image_url: '/accepted',
  active_turn_id: null,
  people: [
    {
      person_id: 2,
      face_index: 1,
      person_locator: '左侧人物',
      reference_urls: ['/board'],
      reference_status: 'available',
    },
  ],
  versions: [
    {
      id: 'v0',
      parent_version_id: null,
      digest: 'd0',
      image_url: '/baseline',
      kind: 'baseline',
      created_at: '2026-01-01',
    },
    { id: 'v1', parent_version_id: 'v0', digest: 'd1', image_url: '/one', kind: 'candidate', created_at: '2026-01-01' },
    { id: 'v2', parent_version_id: 'v0', digest: 'd2', image_url: '/two', kind: 'candidate', created_at: '2026-01-01' },
  ],
  turns: [analysis()],
  created_at: '2026-01-01',
  updated_at: '2026-01-01',
});
const clients: QueryClient[] = [];
const mount = (element = <SessionWorkbench />) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(client);
  return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
};
beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  useWorkbenchStore.setState({ view: 'refinement', sessionId: 's1', targetId: null, selections: {}, pendingTurns: {} });
  useStudioStore.setState({ targetImages: [] });
  vi.mocked(sessionApi.list).mockResolvedValue([fixture()]);
  vi.mocked(sessionApi.get).mockResolvedValue(fixture());
  vi.mocked(sessionApi.turn).mockResolvedValue(fixture());
  vi.mocked(api.getAIModels).mockResolvedValue({
    default_alias: 'editor',
    models: [
      {
        alias: 'editor',
        display_name: '默认图像编辑模型',
        provider: 'litellm',
        model: 'openai/image',
        description: '',
        is_default: true,
      },
    ],
  });
  vi.mocked(api.listBatchTaskHistory).mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe('server-backed conversation workbench', () => {
  it('distinguishes native generated resolution from an enlarged output canvas', async () => {
    const generated: SessionTurn = {
      ...analysis(),
      id: 'generated',
      mode: 'generate',
      status: 'review_required',
      candidate_version_id: 'v1',
      generation_metadata: {
        provider_output_size: [1024, 1536],
        target_canvas_size: [3656, 5484],
      },
    };
    vi.mocked(sessionApi.get).mockResolvedValue({ ...fixture(), turns: [analysis(), generated] });
    mount();
    expect(await screen.findByText(/模型原生输出 1024 × 1536/)).toHaveTextContent('放大不等于恢复细节');
    expect(sessionApi.turn).not.toHaveBeenCalled();
  });

  it('requires a fresh human confirmation when the analysis changes, without calling models on confirmation', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: '确认分析，填写意见' }));
    expect(screen.getByRole('textbox')).toBeEnabled();
    expect(sessionApi.turn).not.toHaveBeenCalled();
    const next = { ...analysis(), id: 'new-analysis' };
    act(() =>
      clients.at(-1)!.setQueryData(['refinement-session', 's1'], {
        ...fixture(),
        revision: 6,
        turns: [analysis(), next],
      }),
    );
    expect(await screen.findByRole('button', { name: '确认分析，填写意见' })).toBeEnabled();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '结合分析与意见生成' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认分析，填写意见' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '保留肤色，只修复边缘' } });
    fireEvent.click(screen.getByRole('button', { name: '结合分析与意见生成' }));
    await waitFor(() =>
      expect(sessionApi.turn).toHaveBeenCalledWith(
        's1',
        expect.objectContaining({
          diagnosis_turn_id: 'new-analysis',
          base_version_id: 'v0',
          message: '保留肤色，只修复边缘',
        }),
      ),
    );
  });

  it('forgets an externally deleted session instead of retaining stale cached controls', async () => {
    vi.mocked(sessionApi.get).mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    mount();
    await waitFor(() => expect(useWorkbenchStore.getState().sessionId).toBeNull());
    expect(sessionApi.get).toHaveBeenCalledTimes(1);
    expect(sessionApi.turn).not.toHaveBeenCalled();
  });
  it('deletes the current idle session only after confirmation and clears the open workspace', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.mocked(sessionApi.delete).mockResolvedValue(undefined);
    mount();
    fireEvent.click(await screen.findByRole('button', { name: '删除当前精修会话' }));
    await waitFor(() => expect(sessionApi.delete).toHaveBeenCalledWith('s1', 5));
    await waitFor(() => expect(useWorkbenchStore.getState().sessionId).toBeNull());
    expect(sessionApi.turn).not.toHaveBeenCalled();
  });
  it('requires analysis of the selected base and never borrows a different version diagnosis', async () => {
    mount();
    await screen.findByRole('button', { name: '确认分析，填写意见' });
    fireEvent.click(screen.getByRole('button', { name: '查看V2' }));
    fireEvent.click(screen.getByRole('button', { name: '以此版继续' }));
    expect(screen.getByRole('button', { name: '开始检查问题' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: '结合分析与意见生成' })).not.toBeInTheDocument();
    expect(sessionApi.turn).not.toHaveBeenCalled();
  });
  it('allows feedback on a clean image without fabricating a defect or generating automatically', async () => {
    const checked = analysis();
    checked.diagnosis = {
      ...checked.diagnosis!,
      needs_edit: false,
      issues: [],
      edit_plan: { changes: [], preserve: [], instructions: '' },
    };
    vi.mocked(sessionApi.get).mockResolvedValue({ ...fixture(), turns: [checked] });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: '确认分析，填写意见' }));
    expect(screen.getByRole('button', { name: '结合分析与意见生成' })).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '把整体光影调暖，保留肤色' } });
    fireEvent.click(screen.getByRole('button', { name: '结合分析与意见生成' }));
    await waitFor(() =>
      expect(sessionApi.turn).toHaveBeenCalledWith(
        's1',
        expect.objectContaining({ mode: 'generate', message: '把整体光影调暖，保留肤色' }),
      ),
    );
  });
  it('keeps uncertain person mapping in discussion until clarified', async () => {
    const checked = analysis();
    checked.diagnosis!.mapping_uncertain = true;
    vi.mocked(sessionApi.get).mockResolvedValue({ ...fixture(), turns: [checked] });
    mount();
    expect(await screen.findByRole('button', { name: '澄清并重新检查' })).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'P2 是左边戴帽子的人' } });
    fireEvent.click(screen.getByRole('button', { name: '澄清并重新检查' }));
    await waitFor(() =>
      expect(sessionApi.turn).toHaveBeenCalledWith('s1', expect.objectContaining({ mode: 'discuss' })),
    );
  });
  it('shows a known local result and its session only once in the image list', async () => {
    useStudioStore.setState({
      targetImages: [
        {
          id: 'target',
          name: '本地重复入口',
          url: '/original',
          processedUrl: '/baseline',
          refinementSessionId: 's1',
          status: 'SUCCESS',
        },
      ],
    });
    mount();
    await screen.findByText('合影精修', { selector: 'h1' });
    expect(screen.queryByRole('button', { name: '本地重复入口' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /合影精修/ })).toHaveLength(1);
  });
  it('keeps viewed, base and accepted versions separate; only explicit acceptance publishes', async () => {
    vi.mocked(sessionApi.accept).mockResolvedValue({ ...fixture(), revision: 6, accepted_version_id: 'v2' });
    mount();
    await screen.findByText('合影精修', { selector: 'h1' });
    for (const button of screen
      .getAllByRole('button')
      .filter((button) => /接受当前|结合分析与意见生成|只讨论/.test(button.textContent ?? '')))
      expect(button).toHaveStyle({ minHeight: '44px' });
    expect(screen.getByRole('combobox', { name: '画布对比方式' })).toHaveValue('none');
    expect(screen.getByTestId('refinement-composer')).toHaveClass('overflow-y-auto', 'shrink-0');
    expect(screen.getByTestId('refinement-conversation-scroll')).toHaveClass('overflow-y-auto', 'min-h-0');
    fireEvent.click(screen.getByRole('button', { name: '查看V2' }));
    expect(screen.getByTestId('view-version')).toHaveTextContent('V2');
    expect(screen.getByTestId('base-version')).toHaveTextContent('本地初稿');
    expect(screen.getByTestId('accepted-version')).toHaveTextContent('V1');
    expect(sessionApi.accept).not.toHaveBeenCalled();
    expect(sessionApi.turn).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '以此版继续' }));
    expect(screen.getByTestId('base-version')).toHaveTextContent('V2');
    fireEvent.click(screen.getByRole('button', { name: '接受当前 V2' }));
    await waitFor(() => expect(sessionApi.accept).toHaveBeenCalledWith('s1', 5, 'v2', 'd2'));
    expect(useStudioStore.getState().targetImages).toEqual([]);
  });
  it('syncs an accepted candidate to its linked workspace result without changing the local baseline', async () => {
    useStudioStore.setState({
      targetImages: [
        {
          id: 'target',
          name: 'Image',
          url: '/original',
          processedUrl: '/baseline',
          status: 'SUCCESS',
          refinementSessionId: 's1',
        },
      ],
    });
    vi.mocked(sessionApi.accept).mockResolvedValue({
      ...fixture(),
      revision: 6,
      accepted_version_id: 'v2',
      accepted_image_url: '/accepted-v2',
    });
    mount();
    await screen.findByText('合影精修', { selector: 'h1' });
    fireEvent.click(screen.getByRole('button', { name: '查看V2' }));
    expect(useStudioStore.getState().targetImages[0].refinedUrl).toBe('/accepted');
    fireEvent.click(screen.getByRole('button', { name: '接受当前 V2' }));
    await waitFor(() => expect(useStudioStore.getState().targetImages[0].refinedUrl).toBe('/accepted-v2'));
    expect(useStudioStore.getState().targetImages[0].processedUrl).toBe('/baseline');
    expect(screen.getByTestId('base-version')).toHaveTextContent('本地初稿');
  });
  it('sends discuss and generate as explicit commands with UUID, revision and selected base', async () => {
    vi.mocked(sessionApi.turn).mockResolvedValue({ ...fixture(), turns: [analysis('v1')] });
    mount();
    await screen.findByText('合影精修', { selector: 'h1' });
    fireEvent.click(screen.getByRole('button', { name: '查看V1' }));
    fireEvent.click(screen.getByRole('button', { name: '以此版继续' }));
    fireEvent.click(screen.getByRole('button', { name: '开始检查问题' }));
    await waitFor(() => expect(sessionApi.turn).toHaveBeenCalledTimes(1));
    expect(vi.mocked(sessionApi.turn).mock.calls[0][1]).toMatchObject({
      mode: 'discuss',
      expected_revision: 5,
      base_version_id: 'v1',
      message: expect.stringContaining('只检查'),
      request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    });
    await screen.findByRole('button', { name: '确认分析，填写意见' });
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: '确认分析，填写意见' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '修复遮挡' } });
    fireEvent.click(screen.getByRole('button', { name: '结合分析与意见生成' }));
    await waitFor(() => expect(sessionApi.turn).toHaveBeenCalledTimes(2));
    expect(vi.mocked(sessionApi.turn).mock.calls[1][1]).toMatchObject({
      mode: 'generate',
      base_version_id: 'v1',
      model_alias: 'editor',
      diagnosis_turn_id: 'analysis-v1',
    });
  });
  it('requires an explicit inspection first, including with an empty draft; it never auto generates', async () => {
    vi.mocked(sessionApi.get).mockResolvedValue({ ...fixture(), turns: [] });
    mount();
    await screen.findByText('合影精修', { selector: 'h1' });
    expect(sessionApi.turn).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '结合分析与意见生成' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '开始检查问题' }));
    await waitFor(() =>
      expect(sessionApi.turn).toHaveBeenCalledWith(
        's1',
        expect.objectContaining({ mode: 'discuss', message: expect.stringContaining('只检查') }),
      ),
    );
  });
  it('retries uncertain network submissions with the exact original request, not a new paid round', async () => {
    vi.mocked(sessionApi.turn).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(fixture());
    mount();
    await screen.findByText('合影精修', { selector: 'h1' });
    fireEvent.click(await screen.findByRole('button', { name: '确认分析，填写意见' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '修复眼睛' } });
    fireEvent.click(screen.getByRole('button', { name: '结合分析与意见生成' }));
    fireEvent.click(await screen.findByRole('button', { name: '重试同一请求' }));
    await waitFor(() => expect(sessionApi.turn).toHaveBeenCalledTimes(2));
    expect(vi.mocked(sessionApi.turn).mock.calls[1]).toEqual(vi.mocked(sessionApi.turn).mock.calls[0]);
  });
  it('refreshes on 409, preserves draft and requires a new explicit decision', async () => {
    vi.mocked(sessionApi.turn).mockRejectedValue({ isAxiosError: true, response: { status: 409 } });
    mount();
    await screen.findByText('合影精修', { selector: 'h1' });
    fireEvent.click(await screen.findByRole('button', { name: '确认分析，填写意见' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '修复眼睛' } });
    fireEvent.click(screen.getByRole('button', { name: '结合分析与意见生成' }));
    await screen.findByText(/会话版本已变化/);
    expect(sessionApi.get).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('textbox')).toHaveValue('修复眼睛');
    expect(sessionApi.turn).toHaveBeenCalledTimes(1);
  });
  it('does not cancel when switching images or unmounting; cancellation is explicit', async () => {
    const running = {
      ...fixture(),
      status: 'running' as const,
      active_turn_id: 't1',
      turns: [
        {
          id: 't1',
          request_id: 'req',
          base_version_id: 'v0',
          mode: 'generate',
          message: '修复',
          status: 'generating',
          created_at: 'now',
          updated_at: 'now',
        } as SessionTurn,
      ],
    };
    vi.mocked(sessionApi.get).mockResolvedValue(running);
    vi.mocked(sessionApi.cancel).mockResolvedValue(fixture());
    const view = mount();
    await screen.findByText('合影精修', { selector: 'h1' });
    expect(screen.getByRole('button', { name: '接受当前 本地初稿' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '删除当前精修会话' })).toBeDisabled();
    expect(screen.queryByText(/d+%.*生成/)).not.toBeInTheDocument();
    act(() => useWorkbenchStore.getState().openSession('other'));
    await waitFor(() => expect(sessionApi.get).toHaveBeenCalledWith('other'));
    expect(sessionApi.cancel).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole('button', { name: /取消本轮/ }));
    await waitFor(() => expect(sessionApi.cancel).toHaveBeenCalledTimes(1));
    view.unmount();
    expect(sessionApi.cancel).toHaveBeenCalledTimes(1);
  });
  it('keeps candidates accept-able when review failed or model configuration is unavailable', async () => {
    vi.mocked(api.getAIModels).mockRejectedValue(new Error('missing config'));
    vi.mocked(sessionApi.get).mockResolvedValue({
      ...fixture(),
      turns: [
        {
          id: 't1',
          request_id: 'req',
          base_version_id: 'v0',
          mode: 'generate',
          message: '修复',
          status: 'review_required',
          candidate_version_id: 'v2',
          review_error: '模型超时',
          created_at: 'now',
          updated_at: 'now',
        },
      ],
    });
    mount();
    await screen.findByText(/复评未完成，候选仍保留/);
    fireEvent.click(screen.getByRole('button', { name: '查看V2' }));
    expect(screen.getByRole('button', { name: '接受当前 V2' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '开始检查问题' })).toBeDisabled();
    expect(screen.getByText(/AI 服务未配置或不可用/)).toBeInTheDocument();
  });
  it('exposes working narrow-screen pane controls and reference uncertainty', async () => {
    vi.mocked(sessionApi.get).mockResolvedValue({
      ...fixture(),
      people: [
        { person_id: 2, face_index: 1, person_locator: '左侧人物', reference_urls: [], reference_status: 'missing' },
      ],
    });
    mount();
    await screen.findByText('合影精修', { selector: 'h1' });
    fireEvent.click(screen.getByRole('button', { name: '图片与参考' }));
    expect(screen.getByRole('button', { name: '图片与参考' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(/未验证身份一致性/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '问题与意见' }));
    expect(screen.getByRole('button', { name: '问题与意见' })).toHaveAttribute('aria-pressed', 'true');
  });
  it('opens history from server summaries without a local workspace association', async () => {
    useWorkbenchStore.setState({ sessionId: null });
    mount(<HistoryWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: /合影精修/ }));
    expect(useWorkbenchStore.getState().sessionId).toBe('s1');
    expect(useStudioStore.getState().targetImages).toEqual([]);
    expect(sessionApi.create).not.toHaveBeenCalled();
  });
  it('does not reopen an older target session after a fresh local swap; creation freezes the actual recipe', async () => {
    useWorkbenchStore.setState({ sessionId: null, targetId: 'target' });
    const snapshot = {
      original_image_url: '/fresh-original',
      baseline_image_url: '/fresh-local',
      people: [{ person_id: 2, face_index: 1, person_locator: '左侧人物', source_asset_ids: ['executed-asset'] }],
    };
    useStudioStore.setState({
      targetImages: [
        {
          id: 'target',
          name: 'fresh',
          url: '/other-original',
          processedUrl: '/fresh-local',
          status: 'SUCCESS',
          refinementInput: snapshot,
        },
      ],
    });
    const created = { ...fixture(), id: 'new-session' };
    vi.mocked(sessionApi.create).mockResolvedValue(created);
    vi.mocked(sessionApi.get).mockResolvedValue(created);
    mount();
    const create = await screen.findByRole('button', { name: '创建精修会话' });
    await waitFor(() => expect(create).toBeEnabled());
    expect(sessionApi.get).not.toHaveBeenCalled();
    fireEvent.click(create);
    await waitFor(() => expect(sessionApi.create).toHaveBeenCalledWith(expect.objectContaining(snapshot)));
    expect(sessionApi.turn).not.toHaveBeenCalled();
    expect(useStudioStore.getState().targetImages[0].refinementSessionId).toBe('new-session');
  });
  it('treats 422 as a rejected command and never presents it as an uncertain paid submission', async () => {
    vi.mocked(sessionApi.turn).mockRejectedValue({ isAxiosError: true, response: { status: 422 } });
    mount();
    await screen.findByText('合影精修', { selector: 'h1' });
    fireEvent.click(await screen.findByRole('button', { name: '确认分析，填写意见' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '修复眼睛' } });
    fireEvent.click(screen.getByRole('button', { name: '结合分析与意见生成' }));
    await screen.findByText(/服务器已拒绝本次操作/);
    expect(screen.queryByRole('button', { name: '重试同一请求' })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox')).toHaveValue('修复眼睛');
    expect(screen.getByRole('textbox')).toHaveAttribute('maxlength', '4000');
  });
  it('accepts the local baseline without any model call even when AI is not configured', async () => {
    vi.mocked(api.getAIModels).mockRejectedValue(new Error('unconfigured'));
    vi.mocked(sessionApi.accept).mockResolvedValue({ ...fixture(), accepted_version_id: 'v0', revision: 6 });
    mount();
    await screen.findByText('合影精修', { selector: 'h1' });
    fireEvent.click(screen.getByRole('button', { name: '接受当前 本地初稿' }));
    await waitFor(() => expect(sessionApi.accept).toHaveBeenCalledWith('s1', 5, 'v0', 'd0'));
    expect(sessionApi.turn).not.toHaveBeenCalled();
  });
  it('restores the exact pending base and prompt without relying on a persisted view selection', async () => {
    const request = {
      request_id: 'retained-uuid',
      expected_revision: 4,
      base_version_id: 'v2',
      mode: 'generate' as const,
      message: '修复眼睛',
      model_alias: 'editor',
    };
    useWorkbenchStore.setState({ selections: {}, pendingTurns: { s1: request } });
    mount();
    await screen.findByText('合影精修', { selector: 'h1' });
    expect(screen.getByTestId('base-version')).toHaveTextContent('V2');
    expect(screen.getByTestId('view-version')).toHaveTextContent('本地初稿');
    expect(screen.getByRole('textbox')).toHaveValue('修复眼睛');
    fireEvent.click(screen.getByRole('button', { name: '重试同一请求' }));
    await waitFor(() => expect(sessionApi.turn).toHaveBeenCalledWith('s1', request));
  });
});
