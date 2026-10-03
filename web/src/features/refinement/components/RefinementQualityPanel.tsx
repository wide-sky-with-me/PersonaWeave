import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type {
  RefinementQualityData,
  RefinementQualityEvaluation,
  RefinementQualityEvaluationRequest,
  RefinementTaskData,
} from '@/types';

type Candidate = RefinementQualityData['candidates'][number];
const STAGES = { identity: '身份未换到位', local: '本地结构或融合问题', refinement: '精修未改善或新增问题' } as const;
function TriState({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean | null;
  onChange: (value: boolean | null) => void;
}) {
  return (
    <label className="block text-2xs text-zinc-400">
      {label}
      <select
        aria-label={label}
        value={value === null ? 'unknown' : value ? 'yes' : 'no'}
        onChange={(event) => onChange(event.target.value === 'unknown' ? null : event.target.value === 'yes')}
        className="mt-1 h-8 w-full rounded-lg border border-border bg-surface-3 px-2 text-xs text-zinc-200"
      >
        <option value="unknown">未评价</option>
        <option value="yes">是</option>
        <option value="no">否</option>
      </select>
    </label>
  );
}
function EvidenceImage({ label, url }: { label: string; url: string | null }) {
  const [failed, setFailed] = useState(false);
  return (
    <figure className="min-w-0 rounded-lg border border-border bg-black/20 p-2">
      <figcaption className="mb-2 text-2xs text-zinc-300">{label}</figcaption>
      {url && !failed ? (
        <a href={url} target="_blank" rel="noreferrer">
          <img
            src={url}
            alt={label}
            onError={() => setFailed(true)}
            className="aspect-[4/3] w-full rounded object-contain"
          />
        </a>
      ) : (
        <p className="flex aspect-[4/3] items-center justify-center text-2xs text-zinc-400">不可用；不以当前画布替代</p>
      )}
    </figure>
  );
}

/** Evaluating a candidate never accepts it or changes the active Studio canvas. */
function QualityForm({
  task,
  candidate,
  saved,
}: {
  task: RefinementTaskData;
  candidate: Candidate;
  saved?: RefinementQualityEvaluation;
}) {
  const queryClient = useQueryClient();
  const [verdict, setVerdict] = useState<RefinementQualityEvaluationRequest['verdict'] | ''>(saved?.verdict ?? '');
  const [people, setPeople] = useState(() =>
    task.people.map(
      (person) =>
        saved?.people.find((item) => item.person_id === person.person_id) ?? {
          person_id: person.person_id,
          identity_satisfied: null,
          naturalness_satisfied: null,
        },
    ),
  );
  const [stages, setStages] = useState<RefinementQualityEvaluationRequest['failure_stages']>(
    saved?.failure_stages ?? [],
  );
  const [unrelated, setUnrelated] = useState<boolean | null>(saved?.unrelated_changes ?? null);
  const [notes, setNotes] = useState(saved?.notes ?? '');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const save = async () => {
    if (!verdict || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await api.saveRefinementQuality(task.task_id, {
        expected_revision: task.revision,
        candidate_digest: candidate.candidate_digest,
        verdict,
        failure_stages: stages,
        people,
        unrelated_changes: unrelated,
        notes,
      });
      setNotice('评价已保存；候选尚未因此被接受。');
      void queryClient.invalidateQueries({ queryKey: ['refinement-quality', task.task_id] });
      void queryClient.invalidateQueries({ queryKey: ['refinement-quality-summary'] });
    } catch {
      setNotice('保存失败或候选版本已变化，请重新读取评价后再试。');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-3 space-y-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <EvidenceImage label="不可变原图" url={candidate.original_url} />
        <EvidenceImage label="最初本地换脸" url={candidate.initial_local_url} />
        <EvidenceImage label="本轮精修候选" url={candidate.candidate_url} />
      </div>
      <p className="text-2xs text-zinc-400">
        第 {candidate.generation_count} 次生成 · 请求模型 {candidate.model_alias} · 实际模型{' '}
        {typeof candidate.audit.model === 'string' ? candidate.audit.model : '未知'} · 耗时{' '}
        {typeof candidate.audit.elapsed_seconds === 'number'
          ? candidate.audit.elapsed_seconds.toFixed(1) + 's'
          : '未知'}{' '}
        · 费用未知 · 以上只使用服务端保存的证据，不将继续精修的底图冒充最初本地结果。
      </p>
      {saved && (
        <p className="text-2xs text-zinc-400">
          上次保存：{saved.updated_at}；未保存的修改不影响已有评价，评价不等于接受。
        </p>
      )}
      <fieldset disabled={busy} className="space-y-3">
        <label className="block text-2xs text-zinc-300">
          本轮成品评价
          <select
            aria-label="本轮成品评价"
            value={verdict}
            onChange={(event) => {
              setVerdict(event.target.value as typeof verdict);
              setNotice(null);
            }}
            className="mt-1 h-9 w-full rounded-lg border border-border bg-surface-3 px-2 text-xs"
          >
            <option value="">请选择，不默认合格</option>
            <option value="ready">可交付</option>
            <option value="needs_work">还需修改</option>
            <option value="unusable">不可用</option>
          </select>
        </label>
        {people.map((person) => (
          <div key={person.person_id} className="rounded-lg border border-border p-2">
            <h4 className="mb-2 text-xs text-zinc-300">人物 #{person.person_id}</h4>
            <div className="grid grid-cols-2 gap-2">
              <TriState
                label={'人物 ' + person.person_id + ' 最终身份满意'}
                value={person.identity_satisfied}
                onChange={(value) =>
                  setPeople((current) =>
                    current.map((item) =>
                      item.person_id === person.person_id ? { ...item, identity_satisfied: value } : item,
                    ),
                  )
                }
              />
              <TriState
                label={'人物 ' + person.person_id + ' 自然度满意'}
                value={person.naturalness_satisfied}
                onChange={(value) =>
                  setPeople((current) =>
                    current.map((item) =>
                      item.person_id === person.person_id ? { ...item, naturalness_satisfied: value } : item,
                    ),
                  )
                }
              />
            </div>
          </div>
        ))}
        <TriState label="出现非目标改动" value={unrelated} onChange={setUnrelated} />
        <div>
          <p className="mb-2 text-2xs text-zinc-400">问题阶段（可多选，不代替逐人评价）</p>
          {Object.entries(STAGES).map(([code, label]) => (
            <label key={code} className="mr-3 inline-flex items-center gap-1.5 text-2xs text-zinc-300">
              <input
                type="checkbox"
                checked={stages.includes(code as keyof typeof STAGES)}
                onChange={(event) =>
                  setStages((current) =>
                    event.target.checked
                      ? [...current, code as keyof typeof STAGES]
                      : current.filter((item) => item !== code),
                  )
                }
              />
              {label}
            </label>
          ))}
        </div>
        <textarea
          aria-label="成品评价备注"
          value={notes}
          maxLength={2000}
          onChange={(event) => setNotes(event.target.value)}
          placeholder="具体指出还差在哪里（最多2000字）"
          className="h-16 w-full text-xs"
        />
        <button
          type="button"
          className="btn btn-sm btn-outline"
          disabled={!verdict || busy}
          onClick={() => void save()}
        >
          {busy ? '保存评价中…' : '保存人工评价'}
        </button>
      </fieldset>
      {notice && (
        <p role="status" className="text-xs text-zinc-300">
          {notice}
        </p>
      )}
    </div>
  );
}

export function RefinementQualityPanel({ task }: { task: RefinementTaskData }) {
  const summary = useQuery({
    queryKey: ['refinement-quality-summary'],
    queryFn: ({ signal }) => api.getRefinementQualitySummary(signal),
    retry: false,
  });
  const quality = useQuery({
    queryKey: ['refinement-quality', task.task_id, task.candidate_digest],
    queryFn: ({ signal }) => api.getRefinementQuality(task.task_id, signal),
    retry: false,
  });
  const digest = task.candidate_digest ?? quality.data?.current_candidate_digest;
  const candidate = quality.data?.candidates.find((item) => item.candidate_digest === digest);
  const saved = quality.data?.evaluations.find((item) => item.candidate_digest === digest);
  return (
    <section aria-label="端到端成品评价" className="mt-4 rounded-xl border border-border bg-surface-3 p-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-medium text-zinc-200">原图 → 最初本地 → 本轮精修</h3>
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => void quality.refetch()}>
          重新读取评价
        </button>
      </div>
      <p className="mt-1 text-2xs text-zinc-400">人工评价与接受独立；未评价不计为可交付，模型评分不是成片率。</p>
      {summary.data && (
        <details className="mt-3 rounded-lg border border-border p-2 text-2xs text-zinc-400">
          <summary className="cursor-pointer text-zinc-300">
            已评价首轮成品率（每任务）：
            {summary.data.first_pass_acceptance_rate === null
              ? '暂无人工评价'
              : (summary.data.first_pass_acceptance_rate * 100).toFixed(1) + '%'}
          </summary>
          <p className="mt-2">
            已评价首轮 {summary.data.first_pass_denominator} · 可交付 {summary.data.first_pass_ready_count} · 首轮未评价{' '}
            {summary.data.first_pass_unevaluated}
          </p>
          <p>
            全部任务样本 {summary.data.total_samples} · 已评价 {summary.data.evaluated_samples} · 未评价{' '}
            {summary.data.unevaluated_samples} · 生成失败尝试 {summary.data.generation_failed_total}
          </p>
          <p>
            来源未知 {summary.data.lineage_unknown_samples} · 生成次数未知{' '}
            {summary.data.generation_count_unknown_samples} · 未生成 {summary.data.not_generated_samples}
          </p>
          <p>
            身份满意：{summary.data.identity_satisfied.true}/
            {summary.data.identity_satisfied.true + summary.data.identity_satisfied.false} 已评价人物；未评{' '}
            {summary.data.identity_satisfied.unknown}
          </p>
          <p>
            自然度满意：{summary.data.naturalness_satisfied.true}/
            {summary.data.naturalness_satisfied.true + summary.data.naturalness_satisfied.false} 已评价人物；未评{' '}
            {summary.data.naturalness_satisfied.unknown}
          </p>
          <p className="mt-2">
            仅已绑定本地换脸起点且首轮有人评价的样本进入分母，不代表所有提交任务成片率。{summary.data.rate_definition}
          </p>
        </details>
      )}
      {summary.isError && <p className="mt-2 text-2xs text-zinc-400">汇总暂不可用，不显示推测成片率。</p>}
      {quality.isLoading && (
        <p role="status" className="mt-2 text-xs text-zinc-400">
          正在读取不可变证据…
        </p>
      )}
      {quality.isError && (
        <p role="alert" className="mt-2 text-xs text-err">
          评价证据暂不可用，请重新读取；不会使用当前画布补造历史。
        </p>
      )}
      {quality.data && !candidate && (
        <p className="mt-2 text-xs text-zinc-400">此候选尚无可用的历史证据，暂不能评价。</p>
      )}
      {quality.data && quality.data.provenance?.lineage !== 'batch-local' && (
        <p className="mt-2 text-2xs text-warn">来源未知：不能确认为一次本地换脸后的精修，不纳入首轮成品率。</p>
      )}
      {candidate && (
        <QualityForm
          key={task.task_id + ':' + digest + ':' + (saved?.updated_at ?? 'new')}
          task={task}
          candidate={candidate}
          saved={saved}
        />
      )}
    </section>
  );
}
