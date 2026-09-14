import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/services/api';
import { useStudioStore } from '@/stores/useStudioStore';
import type { ImageSwapResult, TaskDetailData } from '@/types';
import { TaskResultHistoryDialog } from './TaskResultHistoryDialog';

vi.mock('@/services/api', () => ({ api: { getTaskStatus: vi.fn() } }));

const result = (patch: Partial<ImageSwapResult> = {}): ImageSwapResult => ({
  client_target_id: 'image-1',
  original_url: '/api/v1/files/task/original.png',
  status: 'FAILED',
  faces_detected: 2,
  faces_swapped: 0,
  person_outcomes: [],
  requested_person_indices: [1],
  swapped_person_indices: [],
  matched_similarities: [],
  model_routing: [],
  cost_ms: 24,
  ...patch,
});
const task = (results: ImageSwapResult[], patch: Partial<TaskDetailData> = {}): TaskDetailData => ({
  task_id: 'task',
  status: 'FAILED',
  progress: 1,
  total_images: results.length,
  processed_images: results.length,
  success_count: 0,
  failure_count: results.length,
  has_partial_failure: true,
  created_at: '2026-09-07T00:00:00Z',
  results,
  ...patch,
});
const open = (detail: TaskDetailData) => {
  vi.mocked(api.getTaskStatus).mockResolvedValue(detail);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <TaskResultHistoryDialog taskId="task" onClose={vi.fn()} />
    </QueryClientProvider>,
  );
};

beforeEach(() => vi.clearAllMocks());

describe('read-only task history details', () => {
  it('keeps failed images selectable and exposes per-image and per-person evidence without mutating Studio', async () => {
    const studioBefore = useStudioStore.getState();
    open(
      task([
        result({
          error: 'No visible swap',
          warning: 'Protection degraded',
          person_outcomes: [
            {
              person_index: 3,
              status: 'SKIPPED',
              reason: 'no_visible_change',
              diagnostics: {
                matching: { best_similarity: 0.8, assigned_similarity: 0.7, threshold: 0.35, face_index: 2 },
                source_selection: {
                  mode: 'best-match',
                  reference_count: 2,
                  selected_reference_index: 1,
                  gallery_weight: 0.8,
                  best_reference_weight: 0.2,
                  gallery_reference_weights: [0.4, 0.6],
                },
                mask: {
                  requested_strength: 0.95,
                  intersection_mean: 0.3,
                  bbox_alpha_mean: 0.2,
                  components: [{ name: 'glasses', status: 'failed', mean: null, failure_code: 'inference_failed' }],
                },
              },
            },
          ],
        }),
        result({
          client_target_id: 'image-2',
          error: 'Decode failed',
          faces_detected: undefined,
          faces_swapped: undefined,
        }),
      ]),
    );
    expect(await screen.findByText('图片错误：No visible swap')).toBeInTheDocument();
    expect(screen.getByText('图片警告：Protection degraded')).toBeInTheDocument();
    fireEvent.click(screen.getByText(/人物 #3 · SKIPPED/));
    expect(screen.getByText(/当前图人脸 #2/)).toBeInTheDocument();
    expect(screen.getByText(/最佳参考 #1/)).toBeInTheDocument();
    expect(screen.getByText(/聚合权重 0.800 · 最佳单张系数 0.200/)).toBeInTheDocument();
    expect(screen.getByText(/glasses · failed · 均值 未知 · 失败代码 inference_failed/)).toBeInTheDocument();
    expect(screen.getByText(/不是自然度评分或身份验证/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '下载结果' })).not.toBeInTheDocument();
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /图片 2/ }));
    expect(screen.getByText('图片错误：Decode failed')).toBeInTheDocument();
    expect(screen.queryByText('图片错误：No visible swap')).not.toBeInTheDocument();
    expect(screen.getByText('检测人脸：未知 · 已换入：未知')).toBeInTheDocument();
    expect(screen.getByText(/暂无逐人诊断/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /图片 1/ }));
    expect(useStudioStore.getState()).toBe(studioBefore);
  });

  it('shows persisted successful media even when the task failed on restart and isolates failed image selection', async () => {
    const studioBefore = useStudioStore.getState();
    open(
      task(
        [
          result({ status: 'SUCCESS', processed_url: '/api/v1/files/task/result.png', faces_swapped: 1 }),
          result({
            client_target_id: 'failed',
            status: 'FAILED',
            processed_url: '/api/v1/files/task/not-a-success.png',
            error: 'Failed later',
          }),
        ],
        { error: 'Interrupted by restart' },
      ),
    );
    expect(await screen.findByText('任务错误：Interrupted by restart')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '下载结果' })).toHaveAttribute('href', '/api/v1/files/task/result.png');
    expect(screen.getByRole('slider')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '放大历史图片' }));
    expect(screen.getByText('125%')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /图片 2/ }));
    expect(screen.queryByRole('link', { name: '下载结果' })).not.toBeInTheDocument();
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    expect(screen.getByText(/未生成替代图片/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /图片 1/ }));
    expect(screen.getByText('100%')).toBeInTheDocument();
    fireEvent.error(screen.getByAltText('历史任务 image-1：原图'));
    expect(screen.getByRole('status')).toHaveTextContent('对比图片加载失败');
    expect(useStudioStore.getState()).toBe(studioBefore);
  });

  it('renders absent legacy records and evidence as unknown rather than fabricated zero counts', async () => {
    open(task([], { error: 'Old interruption', results: undefined as unknown as ImageSwapResult[] }));
    expect(await screen.findByText(/暂无逐图记录或诊断/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '下载结果' })).not.toBeInTheDocument();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('shows legacy person status and reason without invented detailed evidence', async () => {
    open(task([result({ person_outcomes: [{ person_index: 1, status: 'SKIPPED', reason: 'future_reason' }] })]));
    expect(await screen.findByText(/人物 #1 · SKIPPED · future_reason/)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/人物 #1 · SKIPPED/));
    expect(screen.getByText('暂无诊断（旧记录未保存详细证据）。')).toBeInTheDocument();
  });

  it('never fabricates a comparison or download when a successful record has no output URL', async () => {
    open(task([result({ status: 'SUCCESS' })]));
    expect(await screen.findByText(/此图片没有可用的成功结果/)).toBeInTheDocument();
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('shows only actual output when the legacy original is unavailable', async () => {
    open(task([result({ status: 'PARTIAL', original_url: '', processed_url: '/api/v1/files/task/output.png' })]));
    expect(await screen.findByText(/原图不可用，仅显示已保存结果/)).toBeInTheDocument();
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    const image = screen.getByAltText('已保存的历史结果');
    expect(image).toHaveAttribute('src', '/api/v1/files/task/output.png');
    fireEvent.error(image);
    expect(screen.getByText('结果图片不可用，文件可能已被清理。')).toBeInTheDocument();
  });
  it('does not invent source weights for external gallery embeddings', async () => {
    open(
      task([
        result({
          person_outcomes: [
            {
              person_index: 1,
              status: 'SKIPPED',
              reason: 'assignment_conflict',
              diagnostics: {
                matching: { best_similarity: 0.8, assigned_similarity: null, threshold: 0.35, face_index: null },
                source_selection: {
                  mode: 'gallery',
                  gallery_weights_known: false,
                  gallery_reference_weights: [0.5, 0.5],
                  selected_reference_index: null,
                },
              },
            },
          ],
        }),
      ]),
    );
    const person = await screen.findByText(/人物 #1 · SKIPPED · 一对一人物分配冲突/);
    fireEvent.click(person);
    expect(screen.getByText(/分配相似度 未知/)).toBeInTheDocument();
    expect(screen.getByText('聚合内参考权重：未知（未记录或外部聚合输入）')).toBeInTheDocument();
    expect(screen.queryByText(/#1 0.500/)).not.toBeInTheDocument();
  });
});
