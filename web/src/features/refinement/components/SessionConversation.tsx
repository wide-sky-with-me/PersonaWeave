import { AlertTriangle, Brain, ChevronDown, Eye } from 'lucide-react';
import type { ReactNode } from 'react';
import type { SessionTurn, SessionIssue, PersonAssessment, SessionDiagnosis } from '../sessionTypes';

export const turnLabels: Record<SessionTurn['status'], string> = {
  queued: '排队中',
  analyzing: '检查画面与整理意见',
  generating: '整图生成中',
  reviewing: '质量复评中',
  review_required: '等待人工确认',
  completed: '已完成',
  failed: '执行失败',
  cancel_requested: '正在取消',
  cancelled: '已取消',
};

function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <details className="group rounded-xl border border-white/10 bg-white/[0.02]">
      <summary className="flex cursor-pointer items-center justify-between gap-3 px-3 py-3 text-xs font-medium text-zinc-200">
        <span>
          {title}
          {count !== undefined && <span className="ml-2 text-zinc-400">{count} 项</span>}
        </span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 text-zinc-400 group-open:rotate-180" />
      </summary>
      <div className="space-y-3 border-t border-white/5 p-3 text-xs leading-relaxed text-zinc-400">{children}</div>
    </details>
  );
}

/** Group existing contract categories for reading without changing model categories. */
function Issues({ items }: { items: SessionIssue[] }) {
  const groups = [
    { title: '人物与五官', categories: ['identity', 'anatomy'] },
    { title: '遮挡与融合', categories: ['occlusion', 'blending'] },
    { title: '光影与质感', categories: ['lighting', 'texture'] },
    { title: '构图与场景', categories: ['composition'] },
  ];
  return groups.map(({ title, categories }) => {
    const issues = items.filter((issue) => categories.includes(issue.category));
    if (!issues.length) return null;
    return (
      <Section key={title} title={title} count={issues.length}>
        {issues.map((issue, i) => (
          <div key={i} className="space-y-2 rounded-lg bg-black/20 p-3">
            <div className="flex flex-wrap items-center gap-2">
              {issue.person_id != null && <span className="text-indigo-300">P{issue.person_id}</span>}
              <strong className="text-zinc-200">{issue.region}</strong>
              <span className={issue.severity === 'high' ? 'text-rose-300' : 'text-zinc-400'}>
                {{ high: '明显', medium: '注意', low: '轻微' }[issue.severity]}
              </span>
            </div>
            <p className="text-zinc-300">{issue.description}</p>
            <p>
              <span className="text-zinc-400">观察依据 · </span>
              {issue.evidence}
            </p>
            <p>
              <span className="text-indigo-300">修改建议 · </span>
              {issue.suggestion}
            </p>
            {issue.uncertainty && <p className="text-amber-300">尚不确定 · {issue.uncertainty}</p>}
          </div>
        ))}
      </Section>
    );
  });
}

function Notes({ items }: { items: string[] }) {
  return (
    <ul className="list-disc space-y-2 pl-4">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  );
}

function People({ people }: { people: PersonAssessment[] }) {
  const labels = {
    match: '参考相符（AI 意见）',
    mismatch: '存在可见差异',
    uncertain: '证据不足',
    unavailable: '缺少参考',
  };
  return (
    people.length > 0 && (
      <Section title="人物参考比对" count={people.length}>
        {people.map((person) => (
          <div key={person.person_id} className="space-y-1">
            <p className="text-zinc-300">
              P{person.person_id} · {person.person_locator} · {labels[person.identity_status]}
            </p>
            <p>{person.evidence}</p>
            {person.uncertainty && <p className="text-amber-300">{person.uncertainty}</p>}
          </div>
        ))}
      </Section>
    )
  );
}

function Diagnosis({ diagnosis }: { diagnosis: SessionDiagnosis }) {
  return (
    <>
      <div className="flex flex-wrap gap-2 text-xs">
        <span className="rounded-lg bg-indigo-500/10 px-3 py-2 text-indigo-200">
          {diagnosis.issues.length} 项有据问题
        </span>
        {!!diagnosis.uncertainties.length && (
          <span className="rounded-lg bg-amber-500/10 px-3 py-2 text-amber-200">
            {diagnosis.uncertainties.length} 项待核实
          </span>
        )}
      </div>
      <p className="text-xs leading-relaxed text-zinc-300 whitespace-pre-wrap">{diagnosis.assistant_message}</p>
      <Issues items={diagnosis.issues} />
      {!diagnosis.issues.length && (
        <p className="text-xs text-zinc-400">未发现有充分证据的画面缺陷；证据不足的部分不代表已经验证通过。</p>
      )}
      {diagnosis.mapping_uncertain && (
        <p className="flex gap-2 rounded-lg bg-amber-500/10 p-3 text-xs text-amber-200">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          人物位置尚不明确，请先补充说明再检查。
        </p>
      )}
      {!!diagnosis.uncertainties.length && (
        <Section title="需要核实的部分" count={diagnosis.uncertainties.length}>
          <Notes items={diagnosis.uncertainties} />
        </Section>
      )}
      <Section title="建议修改与保留">
        {!!diagnosis.edit_plan.changes.length && (
          <>
            <p className="text-indigo-300">建议修改</p>
            <Notes items={diagnosis.edit_plan.changes} />
          </>
        )}
        {!!diagnosis.edit_plan.preserve.length && (
          <>
            <p className="text-zinc-300">希望保留</p>
            <Notes items={diagnosis.edit_plan.preserve} />
          </>
        )}
        {diagnosis.edit_plan.instructions && <p>{diagnosis.edit_plan.instructions}</p>}
      </Section>
      <People people={diagnosis.people} />
    </>
  );
}

export function SessionConversation({
  turns,
  baseVersionId,
  versionLabel,
  onView,
}: {
  turns: SessionTurn[];
  baseVersionId?: string;
  versionLabel: (id: string | null | undefined) => string;
  onView: (id: string) => void;
}) {
  const current = turns
    .slice()
    .reverse()
    .find((turn) => turn.base_version_id === baseVersionId);
  // Keep confirmed analysis visible even if a subsequent generation failed.
  const analysis = turns
    .slice()
    .reverse()
    .find(
      (turn) =>
        turn.base_version_id === baseVersionId &&
        turn.mode === 'discuss' &&
        turn.status === 'completed' &&
        turn.diagnosis,
    );
  const visibleIds = new Set([current?.id, analysis?.id]);
  const history = turns.filter((turn) => !visibleIds.has(turn.id));
  const renderTurn = (turn: SessionTurn) => {
    const generated = turn.mode !== 'discuss';
    const nativeSize = turn.generation_metadata?.provider_output_size;
    const canvasSize = turn.generation_metadata?.target_canvas_size;
    const enlarged = nativeSize && canvasSize && (nativeSize[0] < canvasSize[0] || nativeSize[1] < canvasSize[1]);
    return (
      <article key={turn.id} className="space-y-3 rounded-2xl border border-white/10 bg-zinc-900/40 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 text-2xs text-zinc-400">
          <span>
            {generated ? '修改' : '分析'} · 基于 {versionLabel(turn.base_version_id)} · 第 {turns.indexOf(turn) + 1}{' '}
            次请求
          </span>
          <span>{turnLabels[turn.status]}</span>
        </div>
        {turn.diagnosis &&
          (generated ? (
            <Section title="本次综合修改方案">
              <Diagnosis diagnosis={turn.diagnosis} />
            </Section>
          ) : (
            <Diagnosis diagnosis={turn.diagnosis} />
          ))}
        {turn.candidate_version_id && (
          <button
            type="button"
            className="btn btn-outline btn-sm w-full gap-2 text-indigo-200"
            onClick={() => onView(turn.candidate_version_id!)}
          >
            <Eye className="h-4 w-4" />
            在画布查看 {versionLabel(turn.candidate_version_id)}
          </button>
        )}
        {nativeSize && canvasSize && (
          <p
            className={
              'rounded-lg p-3 text-xs leading-relaxed ' +
              (enlarged ? 'bg-amber-500/10 text-amber-200' : 'bg-white/5 text-zinc-400')
            }
          >
            模型原生输出 {nativeSize.join(' × ')}，当前画布 {canvasSize.join(' × ')}。
            {enlarged && '已放大适配原图尺寸，放大不等于恢复细节，请对比检查。'}
          </p>
        )}
        {turn.review && (
          <div className="space-y-3">
            <p className="text-sm font-semibold text-zinc-100">生成后的复评</p>
            <p className="text-xs leading-relaxed text-zinc-300">{turn.review.summary}</p>
            {!!turn.review.improved.length && (
              <Section title="已改善" count={turn.review.improved.length}>
                <Notes items={turn.review.improved} />
              </Section>
            )}
            {!!turn.review.remaining.length && (
              <Section title="仍需修改" count={turn.review.remaining.length}>
                <Issues items={turn.review.remaining} />
              </Section>
            )}
            {!!turn.review.new_issues.length && (
              <Section title="新增问题" count={turn.review.new_issues.length}>
                <Issues items={turn.review.new_issues} />
              </Section>
            )}
            {!!turn.review.uncertainties.length && (
              <Section title="复评不确定项" count={turn.review.uncertainties.length}>
                <Notes items={turn.review.uncertainties} />
              </Section>
            )}
            <People people={turn.review.people} />
          </div>
        )}
        {turn.candidate_version_id && (
          <p className="text-xs leading-relaxed text-zinc-400">
            查看候选后，点击“以此版继续”可开始下一轮分析；当前修改基图与定稿不会自动改变。
          </p>
        )}
        <Section title="本次人工要求">
          <p className="whitespace-pre-wrap break-words">{turn.message}</p>
        </Section>
        {turn.error && (
          <div role="alert" className="rounded-lg bg-red-500/10 p-3 text-xs text-red-300">
            {turn.error}
          </div>
        )}
        {turn.review_error && <p className="text-xs text-amber-300">复评未完成，候选仍保留：{turn.review_error}</p>}
      </article>
    );
  };
  return (
    <div role="log" aria-label="精修对话" className="space-y-4 p-4">
      <div className="flex items-center gap-2 text-xs font-semibold text-zinc-300">
        <Brain className="h-4 w-4 text-indigo-400" />
        当前基图的问题分析
      </div>
      {current ? (
        renderTurn(current)
      ) : (
        <div className="rounded-2xl border border-dashed border-white/10 bg-zinc-900/40 px-4 py-8 text-center">
          <Brain className="mx-auto mb-3 h-6 w-6 text-indigo-300" />
          <h3 className="text-sm font-semibold text-zinc-100">先让 AI 看看哪里需要改</h3>
          <p className="mt-2 text-xs leading-relaxed text-zinc-400">
            点击下方“开始检查问题”。分析会按问题分组展示，你确认后再补充意见。
          </p>
        </div>
      )}
      {analysis && current?.id !== analysis.id && <Section title="本轮先前的问题分析">{renderTurn(analysis)}</Section>}
      {!!history.length && (
        <Section title={'历史轮次 · ' + history.length}>{history.slice().reverse().map(renderTurn)}</Section>
      )}
    </div>
  );
}
