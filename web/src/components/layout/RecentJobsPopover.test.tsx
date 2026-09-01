import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../services/api';
import { useJobActivityStore } from '../../stores/useJobActivityStore';
import { RecentJobsPopover } from './RecentJobsPopover';

vi.mock('../../services/api', () => ({
  api: {
    listBatchTaskHistory: vi.fn(),
    getTaskStatus: vi.fn(),
  },
}));

const summary = {
  task_id: 'pw_task_history',
  status: 'COMPLETED' as const,
  progress: 1,
  total_images: 1,
  processed_images: 1,
  success_count: 1,
  failure_count: 0,
  has_partial_failure: false,
  viewable_results: 1,
  created_at: '2026-08-31T00:00:00Z',
  completed_at: '2026-08-31T00:00:01Z',
};

const detail = {
  task_id: summary.task_id,
  status: summary.status,
  progress: 1,
  total_images: 1,
  processed_images: 1,
  success_count: 1,
  failure_count: 0,
  has_partial_failure: false,
  created_at: summary.created_at,
  completed_at: summary.completed_at,
  results: [{
    client_target_id: 'target-1',
    original_url: '/api/v1/files/pw_task_history/original_0001.png',
    processed_url: '/api/v1/files/pw_task_history/res_0001.jpg',
    status: 'SUCCESS',
    faces_detected: 2,
    faces_swapped: 1,
    swapped_person_indices: [1],
    requested_person_indices: [1],
    person_outcomes: [],
    matched_similarities: [0.8],
    model_routing: [],
    cost_ms: 120,
  }],
};

describe('RecentJobsPopover', () => {
  beforeEach(() => {
    vi.mocked(api.listBatchTaskHistory).mockResolvedValue([summary]);
    vi.mocked(api.getTaskStatus).mockResolvedValue(detail);
    useJobActivityStore.setState({ jobs: [], isOpen: true });
  });

  it('opens persisted completed results without requiring browser-local job metadata', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RecentJobsPopover /></QueryClientProvider>);

    fireEvent.click(await screen.findByRole('button', { name: '查看结果' }));

    await waitFor(() => expect(api.getTaskStatus).toHaveBeenCalledWith('pw_task_history'));
    expect(await screen.findByRole('dialog', { name: '任务结果回看' })).toBeInTheDocument();
    expect(screen.getByText('1 / 1')).toBeInTheDocument();
    expect(screen.getByRole('slider', { name: '原图与换脸结果对比位置' })).toHaveAttribute('aria-disabled', 'true');
    fireEvent.error(screen.getByAltText('历史任务 target-1：原图'));
    expect(screen.getByRole('status')).toHaveTextContent('对比图片加载失败：原图');
  });
});
