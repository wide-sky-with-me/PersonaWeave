import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '@/services/api';
import { LegacyQualityHistory } from './LegacyQualityHistory';
vi.mock('@/services/api', () => ({
  api: { listLegacyRefinementTasks: vi.fn(), getRefinementTask: vi.fn(), deleteLegacyRefinementTask: vi.fn() },
}));
vi.mock('@/features/refinement/components/RefinementQualityPanel', () => ({
  RefinementQualityPanel: ({ task }: { task: { task_id: string } }) => <p>旧版质量：{task.task_id}</p>,
}));
const clients: QueryClient[] = [];
beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});
it('loads lightweight legacy summaries first and only fetches the selected old task', async () => {
  vi.mocked(api.listLegacyRefinementTasks).mockResolvedValue([
    {
      task_id: 'old-task',
      status: 'REVIEW_REQUIRED',
      created_at: '2026-01-01',
      revision: 2,
      has_candidate: true,
      has_accepted: false,
    },
  ]);
  vi.mocked(api.getRefinementTask).mockResolvedValue({
    task_id: 'old-task',
    status: 'REVIEW_REQUIRED',
    revision: 2,
    progress: 1,
    created_at: 'now',
    people: [],
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <LegacyQualityHistory />
    </QueryClientProvider>,
  );
  expect(api.listLegacyRefinementTasks).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('旧版精修质量记录'));
  fireEvent.click(await screen.findByRole('button', { name: /^old-task/ }));
  await screen.findByText('旧版质量：old-task');
  expect(api.listLegacyRefinementTasks).toHaveBeenCalledWith(30, 0);
  expect(api.getRefinementTask).toHaveBeenCalledWith('old-task');
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
});
it('does not substitute a new session when an old task is unavailable', async () => {
  vi.mocked(api.listLegacyRefinementTasks).mockResolvedValue([
    {
      task_id: 'gone',
      status: 'FAILED',
      created_at: '2026-01-01',
      revision: 2,
      has_candidate: false,
      has_accepted: false,
    },
  ]);
  vi.mocked(api.getRefinementTask).mockRejectedValue(new Error('404'));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <LegacyQualityHistory />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText('旧版精修质量记录'));
  fireEvent.click(await screen.findByRole('button', { name: /^gone/ }));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('不会使用新会话或其他版本替代'));
});

it('deletes a selected legacy record with its revision and closes its quality detail', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const record = {
    task_id: 'remove-old',
    status: 'ACCEPTED',
    created_at: 'now',
    revision: 4,
    has_candidate: false,
    has_accepted: true,
  };
  vi.mocked(api.listLegacyRefinementTasks).mockResolvedValue([record]);
  vi.mocked(api.getRefinementTask).mockResolvedValue({
    task_id: record.task_id,
    status: 'ACCEPTED',
    progress: 1,
    revision: 4,
    created_at: 'now',
    people: [],
  });
  vi.mocked(api.deleteLegacyRefinementTask).mockImplementation(async () => {
    vi.mocked(api.listLegacyRefinementTasks).mockResolvedValue([]);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <LegacyQualityHistory />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText('旧版精修质量记录'));
  fireEvent.click(await screen.findByRole('button', { name: /^remove-old/ }));
  await screen.findByText('旧版质量：remove-old');
  fireEvent.click(screen.getByRole('button', { name: '删除旧版记录 remove-old' }));
  await waitFor(() => expect(api.deleteLegacyRefinementTask).toHaveBeenCalledWith('remove-old', 4));
  await waitFor(() => expect(screen.queryByText('旧版质量：remove-old')).not.toBeInTheDocument());
  expect(screen.queryByRole('button', { name: /^remove-old/ })).not.toBeInTheDocument();
});
