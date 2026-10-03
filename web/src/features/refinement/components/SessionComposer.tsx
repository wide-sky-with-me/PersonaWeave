import { Brain, Loader2, Sparkles } from 'lucide-react';
import type { useSessionWorkbench } from '../hooks/useSessionWorkbench';

interface Props {
  controller: ReturnType<typeof useSessionWorkbench>;
  modelAlias?: string;
  onModelChange: (alias: string) => void;
  baseLabel: string;
}

/** One next action: inspect first, then combine the findings with human feedback. */
export function SessionComposer({ controller: c, modelAlias, onModelChange, baseLabel }: Props) {
  const { session, workspace } = c;
  const running = session?.status === 'running' || !!session?.active_turn_id;
  const locked = c.busy || running || !!c.pending;
  const ready = !!c.models.data?.models.length && !c.models.isError;
  const diagnosis = c.diagnosisTurn?.diagnosis;
  const hasFeedback = !!c.message.trim();
  const clarify = !!diagnosis?.mapping_uncertain;
  const canGenerate = !!diagnosis && c.diagnosisConfirmed && !clarify && (diagnosis.needs_edit || hasFeedback);
  const active = session?.turns.find((turn) => turn.id === session.active_turn_id);

  return (
    <div
      data-testid="refinement-composer"
      className="max-h-[60%] shrink-0 space-y-3 overflow-y-auto border-t border-white/10 bg-zinc-950/80 p-4 backdrop-blur-md"
    >
      {!ready && (
        <div
          role="status"
          className="rounded-lg border border-amber-500/20 bg-amber-500/10 p-2.5 text-xs text-amber-200"
        >
          {c.models.isLoading ? '正在检查模型服务…' : 'AI 服务未配置或不可用。请配置模型后重新检查。'}
          <button className="ml-1 underline" onClick={() => void c.models.refetch()}>
            重新检查
          </button>
        </div>
      )}
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-100">
          {clarify
            ? '先澄清人物位置'
            : c.diagnosisConfirmed
              ? '补充意见并修改'
              : diagnosis
                ? '确认分析结果'
                : '先检查画面'}
        </h2>
        <span data-testid="base-version" className="rounded-md bg-white/5 px-2 py-1 text-2xs text-zinc-400">
          修改基图：{baseLabel}
        </span>
      </div>
      <p className="text-xs leading-relaxed text-zinc-400">
        {clarify
          ? '人物位置还不明确，请补充对应关系后重新检查。'
          : diagnosis
            ? c.diagnosisConfirmed
              ? '已确认的分析会自动带入。填写要修改或保留的内容；与 AI 建议冲突时，以你的意见为准。'
              : '先展开上方的问题分组，核对位置、证据和建议。确认后再补充自己的意见。'
            : '视觉模型先对照原图、当前图与人物参考，整理有证据的问题。本步骤不会生成图片。'}
      </p>
      {diagnosis && !clarify && !c.diagnosisConfirmed && (
        <button
          className="btn btn-md btn-primary w-full min-h-touch"
          disabled={locked}
          onClick={() => session && workspace.select(session.id, { confirmedDiagnosisTurnId: c.diagnosisTurn!.id })}
        >
          确认分析，填写意见
        </button>
      )}
      {(c.diagnosisConfirmed || clarify || c.pending) && (
        <textarea
          aria-label="精修意见"
          maxLength={4000}
          placeholder={diagnosis ? '选填：哪些建议不采纳？还想修改或保留什么？' : '选填：希望 AI 重点检查什么？'}
          value={c.message}
          disabled={!session || locked || (!clarify && !c.diagnosisConfirmed)}
          onChange={(e) => session && workspace.select(session.id, { message: e.target.value })}
          className="min-h-24 max-h-40 w-full resize-y rounded-xl border border-white/10 bg-zinc-900/80 p-3 text-sm leading-relaxed placeholder:text-zinc-600 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/10"
        />
      )}
      {diagnosis && c.diagnosisConfirmed && (
        <div className="flex items-center justify-between gap-2 text-2xs text-zinc-400">
          <label htmlFor="refinement-model">生成模型</label>
          <select
            id="refinement-model"
            aria-label="精修生成模型"
            value={modelAlias ?? ''}
            onChange={(e) => onModelChange(e.target.value)}
            disabled={locked || !ready}
            className="min-w-0 max-w-[70%] rounded-lg border border-white/10 bg-zinc-900 p-2 text-xs text-zinc-300"
          >
            <option value="">选择模型</option>
            {c.models.data?.models.map((item) => (
              <option key={item.alias} value={item.alias}>
                {item.display_name}
              </option>
            ))}
          </select>
        </div>
      )}
      {(c.pending || !diagnosis || clarify || c.diagnosisConfirmed) &&
        (c.pending ? (
          <button
            disabled={c.busy || running}
            className="btn btn-md btn-primary w-full min-h-touch"
            onClick={() => void c.submit(c.pending!.mode)}
          >
            重试同一请求
          </button>
        ) : (
          <button
            className="btn btn-md btn-primary w-full gap-2 min-h-touch"
            disabled={!session || locked || !ready || (clarify ? !hasFeedback : !!diagnosis && !canGenerate)}
            onClick={() =>
              void c.submit(
                diagnosis && !clarify ? 'generate' : 'discuss',
                diagnosis && !clarify ? modelAlias : undefined,
              )
            }
          >
            {running || c.busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : diagnosis && !clarify ? (
              <Sparkles className="h-4 w-4" />
            ) : (
              <Brain className="h-4 w-4" />
            )}
            {running ? '正在处理本轮…' : clarify ? '澄清并重新检查' : diagnosis ? '结合分析与意见生成' : '开始检查问题'}
          </button>
        ))}
      {diagnosis && !clarify && !c.pending && (
        <button
          className="btn btn-md btn-ghost w-full text-xs text-zinc-400 min-h-touch"
          disabled={!session || locked || !ready}
          onClick={() => void c.submit('discuss')}
        >
          只更新分析，暂不生成
        </button>
      )}
      {diagnosis && !diagnosis.needs_edit && !hasFeedback && !clarify && (
        <p className="text-xs leading-relaxed text-ok">
          暂未发现需要修改的问题。可以直接定稿，也可以输入新的修改要求。
        </p>
      )}
      {running && (
        <button
          className="btn btn-outline btn-sm w-full min-h-touch"
          disabled={c.busy || active?.status === 'cancel_requested'}
          onClick={() => void c.cancel()}
        >
          取消本轮
        </button>
      )}
      <p className="flex justify-between gap-2 text-2xs leading-relaxed text-zinc-400">
        <span>
          {running
            ? '关闭界面不会中止本轮，取消不撤销已发生费用。'
            : '检查调用视觉模型；生成时整合意见，每次生成一版。'}
        </span>
        <span className="shrink-0 tabular-nums">{c.message.length}/4000</span>
      </p>
    </div>
  );
}
