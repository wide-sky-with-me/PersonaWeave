import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/services/api';
import { sessionApi } from '@/features/refinement/sessionApi';
import { useJobActivityStore } from '@/stores/useJobActivityStore';
import type { BatchTaskSummary, TaskDetailData } from '@/types';
import { HistoryWorkspace } from './HistoryWorkspace';

vi.mock('@/services/api', () => ({
  apiClient: { get: vi.fn().mockResolvedValue({ data: { data: [] } }) },
  api: {
    listBatchTaskHistory: vi.fn(),
    deleteBatchTask: vi.fn(),
    getTaskStatus: vi.fn(),
    listLegacyRefinementTasks: vi.fn(),
    getRefinementTask: vi.fn(),
  },
}));

vi.mock('@/features/refinement/sessionApi', () => ({
  sessionApi: {
    list: vi.fn(),
    delete: vi.fn(),
  },
}));

const completedTask: BatchTaskSummary = {
  task_id: 'task_completed',
  status: 'COMPLETED',
  progress: 1,
  total_images: 2,
  processed_images: 2,
  success_count: 2,
  failure_count: 0,
  has_partial_failure: false,
  viewable_results: 2,
  created_at: '2026-09-01T10:00:00Z',
  completed_at: '2026-09-01T10:01:00Z',
};

const activeTask: BatchTaskSummary = {
  task_id: 'task_active',
  status: 'PROCESSING',
  progress: 0.5,
  total_images: 2,
  processed_images: 1,
  success_count: 1,
  failure_count: 0,
  has_partial_failure: false,
  viewable_results: 1,
  created_at: '2026-09-01T11:00:00Z',
};

const taskDetail: TaskDetailData = {
  task_id: completedTask.task_id,
  status: completedTask.status,
  progress: 1,
  total_images: 2,
  processed_images: 2,
  success_count: 2,
  failure_count: 0,
  has_partial_failure: false,
  created_at: completedTask.created_at,
  completed_at: completedTask.completed_at,
  results: [],
};

const renderComponent = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <HistoryWorkspace />
    </QueryClientProvider>,
  );
};

describe('HistoryWorkspace batch task deletion', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listBatchTaskHistory).mockResolvedValue([completedTask, activeTask]);
    vi.mocked(api.deleteBatchTask).mockResolvedValue(undefined);
    vi.mocked(api.getTaskStatus).mockResolvedValue(taskDetail);
    vi.mocked(sessionApi.list).mockResolvedValue([]);
    useJobActivityStore.setState({
      jobs: [
        {
          taskId: 'task_completed',
          targetIds: ['target-1'],
          targetNames: ['test.jpg'],
          status: 'COMPLETED',
          submittedAt: '2026-09-01T10:00:00Z',
          updatedAt: '2026-09-01T10:01:00Z',
        },
      ],
      isOpen: false,
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('renders terminal and active batch tasks, showing delete button only for terminal tasks', async () => {
    renderComponent();

    expect(await screen.findByText('task_completed')).toBeInTheDocument();
    expect(screen.getByText('task_active')).toBeInTheDocument();

    expect(screen.getByRole('button', { name: '删除任务 task_completed' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '删除任务 task_active' })).not.toBeInTheDocument();
  });

  it('batch deletes idle refinement sessions with individual revisions and retains conflicts', async () => {
    let records = ['s-a', 's-b', 's-running'].map((id, index) => ({
      id,
      target_id: id,
      title: id,
      revision: index + 2,
      status: id === 's-running' ? ('running' as const) : ('idle' as const),
      created_at: '2026-01-01',
      updated_at: '2026-01-01',
    }));
    vi.mocked(sessionApi.list).mockImplementation(async () => records);
    vi.mocked(sessionApi.delete).mockImplementation(async (id) => {
      if (id === 's-b') throw { isAxiosError: true, response: { status: 409 } };
      records = records.filter((record) => record.id !== id);
    });
    renderComponent();
    fireEvent.click(await screen.findByRole('checkbox', { name: '选择精修会话 s-a' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '选择精修会话 s-b' }));
    expect(screen.getByRole('checkbox', { name: '选择精修会话 s-running' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: '删除精修会话 s-running' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '批量删除精修会话 2 项' }));
    await waitFor(() => expect(sessionApi.delete).toHaveBeenCalledTimes(2));
    expect(sessionApi.delete).toHaveBeenCalledWith('s-a', 2);
    expect(sessionApi.delete).toHaveBeenCalledWith('s-b', 3);
    await screen.findByText(/1 条记录未删除/);
    expect(screen.queryByRole('checkbox', { name: '选择精修会话 s-a' })).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: '选择精修会话 s-b' })).toBeChecked();
  });

  it('deletes a terminal task after confirmation and removes from store and list', async () => {
    renderComponent();

    const deleteBtn = await screen.findByRole('button', { name: '删除任务 task_completed' });
    fireEvent.click(deleteBtn);

    expect(window.confirm).toHaveBeenCalledWith(
      '删除这条任务记录及其原图、结果图？此操作无法撤销；已保存的工程副本会保留。',
    );
    await waitFor(() => expect(api.deleteBatchTask).toHaveBeenCalledWith('task_completed'));
    await waitFor(() => expect(screen.queryByText('task_completed')).not.toBeInTheDocument());

    expect(useJobActivityStore.getState().jobs.find((j) => j.taskId === 'task_completed')).toBeUndefined();
  });

  it('does not delete when confirmation is cancelled', async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    renderComponent();

    const deleteBtn = await screen.findByRole('button', { name: '删除任务 task_completed' });
    fireEvent.click(deleteBtn);

    expect(window.confirm).toHaveBeenCalled();
    expect(api.deleteBatchTask).not.toHaveBeenCalled();
    expect(screen.getByText('task_completed')).toBeInTheDocument();
  });

  it('displays error message when task deletion fails', async () => {
    vi.mocked(api.deleteBatchTask).mockRejectedValue(new Error('Network error'));
    renderComponent();

    const deleteBtn = await screen.findByRole('button', { name: '删除任务 task_completed' });
    fireEvent.click(deleteBtn);

    await waitFor(() => expect(api.deleteBatchTask).toHaveBeenCalledWith('task_completed'));
    expect(await screen.findByRole('alert')).toHaveTextContent('任务删除失败；活动任务需等待完成后再删除。');
    expect(screen.getByText('task_completed')).toBeInTheDocument();
  });

  it('allows deleting a record from within the opened detail dialog', async () => {
    renderComponent();

    // Open detail dialog
    const viewButtons = await screen.findAllByRole('button', { name: '查看' });
    fireEvent.click(viewButtons[0]);

    await waitFor(() => expect(api.getTaskStatus).toHaveBeenCalledWith('task_completed'));
    expect(await screen.findByRole('dialog', { name: '任务结果回看' })).toBeInTheDocument();

    // Dialog has a delete button for terminal task
    const dialogDeleteBtn = screen.getByRole('button', { name: '删除任务 task_completed' });
    fireEvent.click(dialogDeleteBtn);

    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(api.deleteBatchTask).toHaveBeenCalledWith('task_completed'));

    // Dialog should close and item should be removed
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '任务结果回看' })).not.toBeInTheDocument());
    expect(screen.queryByText('task_completed')).not.toBeInTheDocument();
  });

  it('paginates tasks when count exceeds PAGE_SIZE (10)', async () => {
    const manyTasks: BatchTaskSummary[] = Array.from({ length: 15 }, (_, i) => ({
      task_id: `task_${i + 1}`,
      status: 'COMPLETED',
      progress: 1,
      total_images: 1,
      processed_images: 1,
      success_count: 1,
      failure_count: 0,
      has_partial_failure: false,
      viewable_results: 1,
      created_at: `2026-09-01T10:${String(i).padStart(2, '0')}:00Z`,
    }));
    vi.mocked(api.listBatchTaskHistory).mockResolvedValue(manyTasks);

    renderComponent();

    // First page shows task_1 through task_10
    expect(await screen.findByText('task_1')).toBeInTheDocument();
    expect(screen.getByText('task_10')).toBeInTheDocument();
    expect(screen.queryByText('task_11')).not.toBeInTheDocument();
    expect(screen.getByText(/共 15 条记录 · 第 1 \/ 2 页/)).toBeInTheDocument();

    // Navigate to next page
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));

    expect(await screen.findByText('task_11')).toBeInTheDocument();
    expect(screen.getByText('task_15')).toBeInTheDocument();
    expect(screen.queryByText('task_1')).not.toBeInTheDocument();
    expect(screen.getByText(/共 15 条记录 · 第 2 \/ 2 页/)).toBeInTheDocument();

    // Navigate back to previous page
    fireEvent.click(screen.getByRole('button', { name: '上一页' }));
    expect(await screen.findByText('task_1')).toBeInTheDocument();
  });

  it('supports selecting all terminal tasks on page and batch deleting them', async () => {
    const tasks: BatchTaskSummary[] = [
      { ...completedTask, task_id: 'task_batch_1' },
      { ...completedTask, task_id: 'task_batch_2' },
      { ...activeTask, task_id: 'task_batch_active' },
    ];
    vi.mocked(api.listBatchTaskHistory).mockResolvedValue(tasks);

    renderComponent();

    expect(await screen.findByText('task_batch_1')).toBeInTheDocument();

    // Check "全选本页"
    const selectAllCheckbox = screen.getByRole('checkbox', { name: '全选本页' });
    fireEvent.click(selectAllCheckbox);

    // Terminal tasks should be selected (2 of them), active task cannot be selected
    expect(screen.getByText('已选 2 项')).toBeInTheDocument();
    const batchDeleteBtn = screen.getByRole('button', { name: '批量删除 2 项' });
    expect(batchDeleteBtn).toBeInTheDocument();

    // Click batch delete
    fireEvent.click(batchDeleteBtn);

    expect(window.confirm).toHaveBeenCalledWith(
      '确定要批量删除选中的 2 条任务记录及其原图、结果图？此操作无法撤销；已保存的工程副本会保留。',
    );
    await waitFor(() => {
      expect(api.deleteBatchTask).toHaveBeenCalledWith('task_batch_1');
      expect(api.deleteBatchTask).toHaveBeenCalledWith('task_batch_2');
    });

    await waitFor(() => {
      expect(screen.queryByText('task_batch_1')).not.toBeInTheDocument();
      expect(screen.queryByText('task_batch_2')).not.toBeInTheDocument();
    });
    // Active task remains
    expect(screen.getByText('task_batch_active')).toBeInTheDocument();
  });

  it('clears selection when "取消选择" is clicked', async () => {
    renderComponent();

    const taskCheckbox = await screen.findByRole('checkbox', { name: '选择任务 task_completed' });
    fireEvent.click(taskCheckbox);

    expect(screen.getByText('已选 1 项')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '取消选择' }));

    expect(screen.queryByText('已选 1 项')).not.toBeInTheDocument();
  });

  it('shows error notice when batch deletion fails partially or completely', async () => {
    const tasks: BatchTaskSummary[] = [
      { ...completedTask, task_id: 'task_err_1' },
      { ...completedTask, task_id: 'task_err_2' },
    ];
    vi.mocked(api.listBatchTaskHistory).mockResolvedValue(tasks);
    vi.mocked(api.deleteBatchTask).mockImplementation(async (id) => {
      if (id === 'task_err_2') throw new Error('409 Conflict');
    });

    renderComponent();

    expect(await screen.findByText('task_err_1')).toBeInTheDocument();

    // Select all
    fireEvent.click(screen.getByRole('checkbox', { name: '全选本页' }));
    fireEvent.click(screen.getByRole('button', { name: '批量删除 2 项' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('1 条任务删除失败');
    });

    // task_err_1 was deleted, task_err_2 remains
    expect(screen.queryByText('task_err_1')).not.toBeInTheDocument();
    expect(screen.getByText('task_err_2')).toBeInTheDocument();
  });
});

it('fetches older refinement pages from the server beyond the former 50-record cutoff', async () => {
  vi.mocked(api.listBatchTaskHistory).mockResolvedValue([]);
  const records = Array.from({ length: 61 }, (_, index) => ({
    id: `older-${index}`,
    target_id: `t-${index}`,
    title: `Saved ${index}`,
    revision: 1,
    status: 'idle' as const,
    created_at: '2026-01-01',
    updated_at: '2026-01-01',
  }));
  vi.mocked(sessionApi.list).mockImplementation(async (limit = 50, offset = 0) =>
    records.slice(offset, offset + limit),
  );
  renderComponent();
  await screen.findByText('Saved 0');
  for (let page = 1; page <= 5; page++) {
    fireEvent.click(screen.getByRole('button', { name: '下一页精修会话' }));
    await screen.findByText(`Saved ${page * 10}`);
  }
  expect(sessionApi.list).toHaveBeenCalledWith(11, 50);
  expect(screen.getByText('Saved 50')).toBeInTheDocument();
});
