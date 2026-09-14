import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/services/api';
import { useStudioStore } from '@/stores/useStudioStore';
import { useJobActivityStore } from '@/stores/useJobActivityStore';
import { buildBatchSwapPayload, resolveExecutionMappings } from '@/utils/swapPayload';
import { useState } from 'react';
import { useBatchPipeline } from '@/hooks/useBatchPipeline';
import { ParameterPanel } from './ParameterPanel';
import { BatchStatusBar } from './BatchStatusBar';
import type { PersonMappingItem } from '@/types';
vi.mock('@/services/api', () => ({ api: { createBatchTask: vi.fn(), getTaskStatus: vi.fn() } }));
const toast = vi.hoisted(() => vi.fn());
vi.mock('@/components/ui/ToastProvider', () => ({ useToast: () => toast }));
vi.mock('@/utils/swapPayload', () => ({
  buildBatchSwapPayload: vi.fn(),
  buildQuickSwapPayload: vi.fn(),
  resolveExecutionMappings: vi.fn(),
}));
vi.mock('canvas-confetti', () => ({ default: vi.fn() }));
const mapping: PersonMappingItem = {
  person_index: 2,
  target_reference_url: '/original',
  target_reference_person_index: 1,
  source_asset_ids: ['asset'],
  source_manual_images: [],
};
const clients: QueryClient[] = [];
beforeEach(() => {
  vi.clearAllMocks();
  useJobActivityStore.setState({ jobs: [] });
  useStudioStore.setState({
    activeTaskId: null,
    isProcessing: false,
    batchProgress: 0,
    targetImages: [
      { id: 'second', name: 'second', url: '/second', status: 'IDLE' },
      {
        id: 'target',
        name: 'group',
        url: '/original',
        status: 'IDLE',
        faces: [{ index: 1, global_person_id: 2, bbox: [0, 0, 20, 20] }],
      },
    ],
    sourcePeople: [{ id: 1, name: 'person' }],
    sourceGallery: [{ id: 'ref', name: 'ref', kind: 'managed', assetId: 'asset', personId: 1 }],
    personMappings: [mapping],
  });
  vi.mocked(resolveExecutionMappings).mockReturnValue({ ready: true, autoCreated: false, mappings: [mapping] });
  vi.mocked(buildBatchSwapPayload).mockReturnValue({
    ready: true,
    autoCreated: false,
    mappings: [mapping],
    payload: {
      person_mappings: [mapping],
      batch_images: [{ client_target_id: 'target', image_url: '/original' }],
      parameters: useStudioStore.getState().parameters,
    },
  });
  vi.mocked(api.createBatchTask).mockResolvedValue({ code: 200, data: { task_id: 'batch' } });
  vi.mocked(api.getTaskStatus).mockResolvedValue({
    task_id: 'batch',
    status: 'COMPLETED',
    progress: 1,
    total_images: 1,
    processed_images: 1,
    success_count: 1,
    failure_count: 0,
    has_partial_failure: false,
    created_at: '2026-01-01',
    results: [
      {
        client_target_id: 'target',
        original_url: '/immutable-original',
        processed_url: '/immutable-baseline',
        status: 'SUCCESS',
        faces_detected: 1,
        faces_swapped: 1,
        swapped_person_indices: [2],
        requested_person_indices: [2],
        person_outcomes: [],
        matched_similarities: [],
        model_routing: [],
        cost_ms: 100,
        refinement_session_id: 'server-session',
      },
    ],
  });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});
function Workspace() {
  const batch = useBatchPipeline();
  const [showPanel, setShowPanel] = useState(true);
  return (
    <>
      <button onClick={() => setShowPanel((visible) => !visible)}>切换侧栏</button>
      {showPanel && <ParameterPanel batch={batch} />}
      <BatchStatusBar batch={batch} />
    </>
  );
}
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <Workspace />
    </QueryClientProvider>,
  );
}
describe('explicit batch AI authorization', () => {
  it.each(['batch', 'unrelated-task'])('restores frozen pending input only for its own task: %s', async (taskId) => {
    const now = new Date().toISOString();
    useJobActivityStore.getState().upsertJob({
      taskId: 'batch',
      targetIds: ['target'],
      targetNames: ['group'],
      status: 'PROCESSING',
      submittedAt: now,
      updatedAt: now,
    });
    useStudioStore.getState().updateTargetImageById('target', {
      pendingRefinement: {
        taskId,
        input: {
          original_image_url: '/submitted-original',
          baseline_image_url: '',
          people: [
            {
              person_id: 2,
              face_index: 1,
              person_locator: 'Executed assignment',
              source_manual_images: ['/frozen-reference'],
            },
          ],
        },
      },
    });
    // A remounted hook has no in-memory execution snapshots, as after a browser refresh.
    mount();
    await waitFor(() => expect(useStudioStore.getState().targetImages[1].status).toBe('SUCCESS'));
    const target = useStudioStore.getState().targetImages[1];
    if (taskId === 'batch')
      expect(target.refinementInput).toMatchObject({
        original_image_url: '/immutable-original',
        baseline_image_url: '/immutable-baseline',
        people: [{ person_id: 2, face_index: 1, source_manual_images: ['/frozen-reference'] }],
      });
    else expect(target.refinementInput).toBeUndefined();
    expect(target.pendingRefinement).toBeUndefined();
    expect(api.createBatchTask).not.toHaveBeenCalled();
  });

  it('defaults to local-only and only links the server supplied refinement session', async () => {
    mount();
    expect(screen.getByRole('checkbox', { name: /换脸后接续 AI 首轮精修/ })).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: /批量换脸/ }));
    await waitFor(() =>
      expect(api.createBatchTask).toHaveBeenCalledWith(expect.objectContaining({ ai_refinement: false })),
    );
    await waitFor(() => expect(useStudioStore.getState().targetImages[1].refinementSessionId).toBe('server-session'));
    expect(useStudioStore.getState().targetImages[1].refinementInput).toMatchObject({
      original_image_url: '/immutable-original',
      baseline_image_url: '/immutable-baseline',
      people: [{ person_id: 2, source_asset_ids: ['asset'] }],
    });
  });
  it('passes opt-in without starting frontend-generated AI turns', async () => {
    mount();
    fireEvent.click(screen.getByRole('checkbox', { name: /换脸后接续 AI 首轮精修/ }));
    fireEvent.click(screen.getByRole('button', { name: /批量换脸/ }));
    await waitFor(() =>
      expect(api.createBatchTask).toHaveBeenCalledWith(expect.objectContaining({ ai_refinement: true })),
    );
    expect(api.createBatchTask).toHaveBeenCalledTimes(1);
  });
});

describe('sidebar task controls', () => {
  it('keeps all actions out of the status bar and exposes one mapping and batch entry', () => {
    mount();
    const footer = screen.getByRole('contentinfo', { name: '任务状态栏' });
    expect(within(footer).queryByRole('button')).not.toBeInTheDocument();
    expect(within(footer).queryByRole('checkbox')).not.toBeInTheDocument();
    expect(within(footer).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getAllByRole('button', { name: /角色映射/ })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: /启动批量换脸/ })).toHaveLength(1);
  });
  it('keeps AI opt-in and submitted tasks when the sidebar closes', async () => {
    let finish!: (value: Awaited<ReturnType<typeof api.createBatchTask>>) => void;
    vi.mocked(api.createBatchTask).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    mount();
    fireEvent.click(screen.getByRole('checkbox', { name: /换脸后接续 AI 首轮精修/ }));
    fireEvent.click(screen.getByRole('button', { name: /启动批量换脸/ }));
    await waitFor(() => expect(api.createBatchTask).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '切换侧栏' }));
    finish({ code: 200, data: { task_id: 'batch' } });
    await waitFor(() => expect(useStudioStore.getState().targetImages[1].status).toBe('SUCCESS'));
    fireEvent.click(screen.getByRole('button', { name: '切换侧栏' }));
    expect(screen.getByRole('checkbox', { name: /换脸后接续 AI 首轮精修/ })).toBeChecked();
    expect(screen.getByRole('button', { name: '导出批量结果 ZIP' })).toBeEnabled();
    expect(api.createBatchTask).toHaveBeenCalledTimes(1);
  });
  it('preserves explicitly authorized AI processing for a single target', async () => {
    useStudioStore.setState({ targetImages: [useStudioStore.getState().targetImages[1]], activeTargetIndex: 0 });
    mount();
    expect(screen.getByRole('button', { name: '开始单图换脸' })).toBeEnabled();
    fireEvent.click(screen.getByRole('checkbox', { name: /换脸后接续 AI 首轮精修/ }));
    fireEvent.click(screen.getByRole('button', { name: '开始换脸并精修' }));
    await waitFor(() =>
      expect(api.createBatchTask).toHaveBeenCalledWith(expect.objectContaining({ ai_refinement: true })),
    );
  });
});
