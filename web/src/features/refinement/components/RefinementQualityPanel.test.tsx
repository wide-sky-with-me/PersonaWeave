import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/services/api';
import { RefinementQualityPanel } from './RefinementQualityPanel';
import type { RefinementQualityData, RefinementTaskData } from '@/types';
vi.mock('@/services/api', () => ({
  api: {
    getRefinementQuality: vi.fn(),
    saveRefinementQuality: vi.fn(),
    getRefinementQualitySummary: vi.fn(),
  },
}));
const task: RefinementTaskData = {
  task_id: 'task-one',
  revision: 7,
  candidate_digest: 'digest-one',
  status: 'REVIEW_REQUIRED',
  progress: 1,
  created_at: 'now',
  people: [{ person_id: 2, face_index: 1, status: 'REVIEW_REQUIRED', progress: 1, attempt: 2 }],
};
const evidence: RefinementQualityData = {
  task_id: task.task_id,
  generation_count: 2,
  current_candidate_digest: 'digest-one',
  evaluations: [],
  candidates: [
    {
      candidate_digest: 'digest-one',
      generation_count: 2,
      original_url: '/api/evidence/original',
      initial_local_url: '/api/evidence/initial',
      baseline_url: '/api/evidence/later-baseline',
      candidate_url: '/api/evidence/current',
      model_alias: 'model',
      hashes: {},
      audit: {},
    },
  ],
};
const wrap = (value = task) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <RefinementQualityPanel task={value} />
  </QueryClientProvider>
);
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getRefinementQuality).mockResolvedValue(evidence);
  vi.mocked(api.getRefinementQualitySummary).mockResolvedValue({
    total_samples: 2,
    evaluated_samples: 0,
    unevaluated_samples: 2,
    generation_count_unknown_samples: 0,
    lineage_unknown_samples: 1,
    not_generated_samples: 0,
    generation_failed_total: 0,
    eligible_total: 1,
    first_pass_unevaluated: 1,
    first_pass_ready_count: 0,
    first_pass_denominator: 0,
    first_pass_acceptance_rate: null,
    rate_definition: '仅首轮人工评价',
    identity_satisfied: { true: 0, false: 0, unknown: 2, rate_known_only: null },
    naturalness_satisfied: { true: 0, false: 0, unknown: 2, rate_known_only: null },
  });
});
describe('human quality evidence', () => {
  it('uses immutable initial local image, requires explicit verdict, and saves nulls without accepting', async () => {
    vi.mocked(api.saveRefinementQuality).mockResolvedValue({
      expected_revision: 7,
      candidate_digest: 'digest-one',
      task_id: task.task_id,
      updated_at: 'now',
      verdict: 'needs_work',
      failure_stages: [],
      people: [],
      unrelated_changes: null,
      notes: '',
    });
    render(wrap());
    const verdict = await screen.findByLabelText('本轮成品评价');
    expect(await screen.findByText('已评价首轮成品率（每任务）：暂无人工评价')).toBeInTheDocument();
    expect(screen.queryByText('已评价首轮成品率（每任务）：0.0%')).not.toBeInTheDocument();
    expect(screen.getByAltText('最初本地换脸')).toHaveAttribute('src', '/api/evidence/initial');
    expect(screen.getByRole('button', { name: '保存人工评价' })).toBeDisabled();
    expect(screen.getByLabelText('人物 2 最终身份满意')).toHaveValue('unknown');
    fireEvent.change(verdict, { target: { value: 'needs_work' } });
    fireEvent.click(screen.getByText('身份未换到位'));
    fireEvent.click(screen.getByText('保存人工评价'));
    await waitFor(() =>
      expect(api.saveRefinementQuality).toHaveBeenCalledWith('task-one', {
        expected_revision: 7,
        candidate_digest: 'digest-one',
        verdict: 'needs_work',
        failure_stages: ['identity'],
        people: [{ person_id: 2, identity_satisfied: null, naturalness_satisfied: null }],
        unrelated_changes: null,
        notes: '',
      }),
    );
    expect(api).not.toHaveProperty('acceptRefinementTask');
  });
  it('restores a saved evaluation and never fabricates missing local evidence', async () => {
    vi.mocked(api.getRefinementQuality).mockResolvedValue({
      ...evidence,
      candidates: [{ ...evidence.candidates[0], initial_local_url: null }],
      evaluations: [
        {
          task_id: task.task_id,
          updated_at: 'saved',
          expected_revision: 6,
          candidate_digest: 'digest-one',
          verdict: 'unusable',
          failure_stages: ['local'],
          people: [{ person_id: 2, identity_satisfied: false, naturalness_satisfied: null }],
          unrelated_changes: true,
          notes: '眼部不自然',
        },
      ],
    });
    render(wrap());
    expect(await screen.findByLabelText('本轮成品评价')).toHaveValue('unusable');
    expect(screen.getByLabelText('人物 2 最终身份满意')).toHaveValue('no');
    expect(screen.getByLabelText('出现非目标改动')).toHaveValue('yes');
    expect(screen.queryByAltText('最初本地换脸')).not.toBeInTheDocument();
    expect(screen.getByText('不可用；不以当前画布替代')).toBeInTheDocument();
  });
  it('does not show another candidate or previous tasks evaluation', async () => {
    render(wrap({ ...task, candidate_digest: 'new-digest' }));
    expect(await screen.findByText('此候选尚无可用的历史证据，暂不能评价。')).toBeInTheDocument();
    expect(screen.queryByLabelText('本轮成品评价')).not.toBeInTheDocument();
  });
});
