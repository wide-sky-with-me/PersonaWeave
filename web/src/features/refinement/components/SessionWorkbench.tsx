import { Check, CheckCheck, Minus, Plus, RefreshCw, Sparkles, Trash2 } from 'lucide-react';
import { motion } from 'framer-motion';
import { useEffect, useRef } from 'react';
import { useToast } from '@/components/ui/ToastProvider';
import { apiErrorMessage } from '@/utils/apiError';
import { useStudioStore } from '@/stores/useStudioStore';
import { RefinementImageViewport } from './RefinementImageViewport';
import { useSessionWorkbench } from '../hooks/useSessionWorkbench';
import { useWorkbenchStore } from '../workbenchStore';
import { SessionConversation, turnLabels } from './SessionConversation';
import { SessionComposer } from './SessionComposer';
import { useSessionDeletion } from '../hooks/useSessionDeletion';

export function SessionWorkbench() {
  const targets = useStudioStore((state) => state.targetImages);
  const targetId = useWorkbenchStore((state) => state.targetId);
  const target = targets.find((item) => item.id === targetId);
  const c = useSessionWorkbench(target);
  const { session, workspace } = c;
  const toast = useToast();
  const reportedTurns = useRef(new Set<string>());
  const observedSessions = useRef(new Set<string>());
  useEffect(() => {
    if (c.error) toast({ title: 'AI 精修操作未完成', description: c.error, kind: 'error' });
  }, [c.error, toast]);
  useEffect(() => {
    const error = c.detail.error || c.sessions.error;
    if (error) toast({ title: 'AI 精修状态读取失败', description: apiErrorMessage(error), kind: 'error' });
  }, [c.detail.error, c.sessions.error, toast]);
  useEffect(() => {
    if (!session) return;
    const restoring = !observedSessions.current.has(session.id);
    observedSessions.current.add(session.id);
    for (const turn of session?.turns ?? []) {
      const message = turn.error || turn.review_error;
      const key = `${session!.id}:${turn.id}:${message}`;
      if (!message || reportedTurns.current.has(key)) continue;
      reportedTurns.current.add(key);
      // Historical failures remain in the conversation; opening a session must not replay old dialogs.
      if (restoring) continue;
      toast({
        title: turn.review_error ? '精修候选已保留，复评失败' : 'AI 精修失败',
        description: message,
        kind: 'error',
      });
    }
  }, [session, toast]);
  const deletion = useSessionDeletion(session ? [session] : []);
  const selectionId = session?.id ?? targetId ?? 'empty';
  const selection = workspace.selections[selectionId] ?? {};
  const pane = selection.pane ?? 'image';
  const comparison = selection.comparison ?? 'none';
  const zoom = selection.zoom ?? 1;
  const model = selection.model ?? '';
  const setPane = (pane: 'image' | 'conversation' | 'assets') => workspace.select(selectionId, { pane });
  const setComparison = (comparison: 'original' | 'baseline' | 'none') => workspace.select(selectionId, { comparison });
  const setModel = (model: string) => workspace.select(selectionId, { model });
  const setZoom = (value: number | ((current: number) => number)) =>
    workspace.select(selectionId, { zoom: typeof value === 'function' ? value(zoom) : value });
  const viewed = session?.versions.find((version) => version.id === c.viewVersionId);
  const baseline = session?.versions.find((version) => version.id === session.baseline_version_id);
  const label = (id: string | null | undefined) => {
    const index = session?.versions.findIndex((version) => version.id === id) ?? -1;
    return index < 0 ? '未选择' : index === 0 ? '本地初稿' : 'V' + index;
  };
  const running = session?.status === 'running' || !!session?.active_turn_id;
  const locked = c.busy || running || !!c.pending || deletion.busy;
  const modelAlias = c.pending?.model_alias || model || c.models.data?.default_alias;
  const active = session?.turns.find((turn) => turn.id === session.active_turn_id);
  const latestBaseTurn = session?.turns
    .slice()
    .reverse()
    .find((turn) => turn.base_version_id === c.baseVersionId);
  const step = running
    ? active?.mode === 'discuss'
      ? 1
      : 2
    : latestBaseTurn?.candidate_version_id
      ? 3
      : c.diagnosisTurn
        ? 2
        : 1;
  const before = comparison === 'baseline' ? baseline?.image_url : session?.original_url;
  const selectView = (id: string) => {
    if (session) workspace.select(session.id, { viewVersionId: id });
    setPane('image');
  };
  return (
    <section
      aria-label="对话式 AI 精修工作区"
      className="refinement-workbench flex min-h-0 flex-1 flex-col bg-background"
    >
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-sm font-semibold text-zinc-100">
            {session?.title || target?.name || 'AI 精修工作台'}
          </h1>
          <p className="mt-1 text-2xs text-zinc-500">AI 精修 · 先看清问题，再决定怎么改</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {session && (
            <button
              aria-label="删除当前精修会话"
              className="btn btn-ghost btn-icon hover:text-red-300"
              disabled={locked}
              onClick={() => void deletion.request([session.id])}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
          <span role="status" className="text-zinc-400">
            {running
              ? active
                ? turnLabels[active.status]
                : '后台处理中'
              : session?.accepted_version_id
                ? '已接受 ' + label(session.accepted_version_id)
                : c.diagnosisTurn
                  ? c.diagnosisConfirmed
                    ? '已确认分析，等待你的意见'
                    : '分析完成，等待确认'
                  : '等待检查画面'}
          </span>
        </div>
      </header>
      {deletion.error && (
        <p role="alert" className="border-b border-amber-500/20 bg-amber-500/10 px-4 py-2 text-xs text-amber-200">
          {deletion.error}
        </p>
      )}
      <ol
        aria-label="精修步骤"
        className="grid shrink-0 grid-cols-3 gap-2 border-b border-white/10 bg-zinc-950/50 px-4 py-3"
      >
        {['检查问题', '确认与修改', '对比与定稿'].map((title, index) => (
          <li key={title} className="relative flex items-center gap-2 rounded-xl px-2 py-2 text-xs sm:px-3">
            {step === index + 1 && (
              <motion.div
                layoutId="refinement-step"
                transition={{ type: 'spring', stiffness: 300, damping: 25 }}
                className="absolute inset-0 rounded-xl border border-indigo-400/20 bg-indigo-500/10"
              />
            )}
            <span
              className={
                'relative flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-2xs ' +
                (step > index + 1
                  ? 'border-cyan-500/30 text-cyan-300'
                  : step === index + 1
                    ? 'border-indigo-400/40 bg-indigo-500 text-white'
                    : 'border-white/10 text-zinc-500')
              }
            >
              {step > index + 1 ? <Check className="h-3 w-3" /> : index + 1}
            </span>
            <span className={'relative ' + (step === index + 1 ? 'font-medium text-indigo-100' : 'text-zinc-400')}>
              {title}
            </span>
          </li>
        ))}
      </ol>
      {target?.refinementError && (
        <p role="alert" className="border-b border-border px-4 py-2 text-xs text-amber-200">
          首轮精修未完成，本地结果已保留：{target.refinementError}
        </p>
      )}
      <nav aria-label="精修工作区分区" className="flex border-b border-border lg:hidden">
        {(
          [
            ['image', '画布与版本'],
            ['conversation', '问题与意见'],
            ['assets', '图片与参考'],
          ] as const
        ).map(([id, title]) => (
          <button
            key={id}
            aria-pressed={pane === id}
            onClick={() => setPane(id)}
            className={
              'min-h-11 flex-1 px-2 text-xs ' +
              (pane === id ? 'border-b-2 border-indigo-400 text-indigo-300' : 'text-zinc-400')
            }
          >
            {title}
          </button>
        ))}
      </nav>
      {(c.error || c.detail.isError || c.sessions.isError) && (
        <div
          role="alert"
          className="flex items-center justify-between gap-2 border-b border-amber-500/20 bg-amber-500/10 px-4 py-2 text-xs text-amber-200"
        >
          <span>{c.error || '无法读取服务端会话，可能服务尚未就绪或记录已清理。'}</span>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => {
              void c.sessions.refetch();
              if (workspace.sessionId) void c.detail.refetch();
            }}
          >
            刷新状态
          </button>
        </div>
      )}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[172px_minmax(0,1fr)_360px] xl:grid-cols-[200px_minmax(0,1fr)_410px]">
        <aside
          aria-label="图片与人物参考"
          className={
            (pane === 'assets' ? 'flex' : 'hidden') +
            ' min-h-0 flex-col overflow-y-auto border-r border-border bg-surface lg:flex'
          }
        >
          <div className="flex items-center justify-between px-3 py-3">
            <h2 className="text-xs font-semibold">精修图片</h2>
            <button
              aria-label="刷新会话列表"
              className="btn btn-ghost btn-icon"
              onClick={() => void c.sessions.refetch()}
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="space-y-1 px-2">
            {targets
              .filter(
                (item) =>
                  (item.processedUrl || item.refinementSessionId) &&
                  !c.sessions.data?.some((saved) => saved.id === item.refinementSessionId),
              )
              .map((item) => (
                <button
                  key={item.id}
                  className="flex w-full items-center gap-2 rounded-lg p-2 text-left text-xs hover:bg-surface-3"
                  onClick={() => {
                    workspace.openTarget(item.id, item.refinementSessionId);
                    setPane('image');
                  }}
                >
                  <img src={item.processedUrl || item.url} alt="" className="h-10 w-12 rounded object-cover" />
                  <span className="truncate">{item.name}</span>
                </button>
              ))}
          </div>
          <div className="space-y-1 px-2">
            {c.sessions.isLoading && (
              <p role="status" className="p-2 text-xs text-zinc-400">
                正在加载会话…
              </p>
            )}
            {c.sessions.data?.map((item) => (
              <button
                key={item.id}
                aria-current={session?.id === item.id ? 'page' : undefined}
                className={
                  'w-full rounded-lg px-2 py-2 text-left text-xs ' +
                  (session?.id === item.id ? 'bg-indigo-500/10 text-indigo-200' : 'hover:bg-surface-3 text-zinc-400')
                }
                onClick={() => {
                  workspace.openSession(item.id);
                  setPane('image');
                }}
              >
                <span className="block truncate">{item.title}</span>
                <span className="mt-1 block text-2xs text-zinc-500">
                  {item.status === 'running' ? '后台处理中' : item.accepted_version_id ? '已接受' : '待确认'}
                </span>
              </button>
            ))}
            {!c.sessions.isLoading && !c.sessions.isError && !c.sessions.data?.length && (
              <p className="p-2 text-xs text-zinc-500">暂无会话，完成本地换脸后进入。</p>
            )}
          </div>
          {session && (
            <details open className="mt-auto border-t border-white/10 p-3">
              <summary className="mb-3 cursor-pointer text-xs font-semibold text-zinc-400">人物参考</summary>
              {session.people.map((person) => (
                <article key={person.person_id} className="mb-4">
                  <h3 className="text-xs font-medium text-cyan-200">人物 P{person.person_id}</h3>
                  <details className="mt-1 text-2xs text-zinc-400">
                    <summary className="cursor-pointer hover:text-zinc-200">查看原图定位</summary>
                    <p className="mt-2 break-words leading-relaxed">{person.person_locator}</p>
                  </details>
                  <p className="mt-1 text-2xs text-zinc-500">
                    {person.reference_status === 'missing'
                      ? '参考缺失：未验证身份一致性'
                      : '参考已固定，不随素材库变更'}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {person.reference_urls.map((url, i) => (
                      <a key={url} href={url} target="_blank" rel="noreferrer">
                        <img
                          src={url}
                          alt={'P' + person.person_id + ' 参考板 ' + (i + 1)}
                          className="h-16 w-16 rounded border border-border object-contain"
                        />
                      </a>
                    ))}
                  </div>
                </article>
              ))}
            </details>
          )}
        </aside>
        <main
          aria-label="图像版本画布"
          className={(pane === 'image' ? 'flex' : 'hidden') + ' min-h-0 min-w-0 flex-col lg:flex'}
        >
          {!session ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
              <Sparkles className="h-8 w-8 text-indigo-400" />
              <p className="text-base text-zinc-200">
                {c.detail.isLoading ? '正在恢复精修会话…' : target ? '从本地初稿开始' : '选择一张图片，开始精修'}
              </p>
              <p className="max-w-sm text-sm leading-relaxed text-zinc-500">
                先检查融合、身份与光影问题，再结合你的意见生成精修结果。每个版本都可以回看和比较。
              </p>
              {!target && !c.detail.isLoading && (
                <div className="flex flex-wrap justify-center gap-2">
                  <button className="btn btn-md btn-primary" onClick={() => workspace.navigate('prepare')}>
                    前往准备与换脸
                  </button>
                  {!!c.sessions.data?.length && (
                    <button className="btn btn-md btn-outline lg:hidden" onClick={() => setPane('assets')}>
                      选择已有精修图片
                    </button>
                  )}
                </div>
              )}
              {target && (
                <button
                  className="btn btn-md btn-primary"
                  style={{ minHeight: 44 }}
                  disabled={c.busy || !target.refinementInput || c.sessions.isLoading}
                  onClick={() => void c.create()}
                >
                  创建精修会话
                </button>
              )}
              {target && !target.refinementInput && (
                <p className="text-xs text-amber-200">
                  此结果没有执行时的参考快照，请重新运行本地换脸，或从服务端会话恢复。
                </p>
              )}
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2 text-xs">
                <span data-testid="view-version">查看：{label(c.viewVersionId)}</span>
                <div className="flex items-center gap-1">
                  <select
                    aria-label="画布对比方式"
                    className="rounded border border-border bg-surface-2 p-1.5"
                    value={comparison}
                    onChange={(e) => setComparison(e.target.value as typeof comparison)}
                  >
                    <option value="original">对比原图</option>
                    <option value="baseline">对比本地初稿</option>
                    <option value="none">仅查看版本</option>
                  </select>
                  <button
                    aria-label="缩小"
                    className="btn btn-ghost btn-icon"
                    onClick={() => setZoom((v) => Math.max(0.5, v - 0.25))}
                  >
                    <Minus className="h-3.5 w-3.5" />
                  </button>
                  <button
                    title="缩放相对完整适应尺寸；点击恢复适应并重置滚动"
                    className="btn btn-ghost btn-sm"
                    onClick={() => setZoom(1)}
                  >
                    适应画布 · {Math.round(zoom * 100)}%
                  </button>
                  <button
                    aria-label="放大"
                    className="btn btn-ghost btn-icon"
                    onClick={() => setZoom((v) => Math.min(4, v + 0.25))}
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              {viewed ? (
                <RefinementImageViewport
                  imageUrl={viewed.image_url}
                  imageLabel={session.title}
                  beforeUrl={comparison !== 'none' ? before : undefined}
                  beforeLabel={comparison === 'original' ? '原图' : '本地初稿'}
                  afterLabel={label(viewed.id)}
                  zoom={zoom}
                />
              ) : (
                <p className="min-h-0 flex-1 p-4 text-sm text-zinc-500">所选版本已不可用，请选择其他版本。</p>
              )}
              <div className="flex flex-wrap items-center gap-2 border-t border-border px-3 py-2">
                <button
                  className="btn btn-outline btn-sm"
                  style={{ minHeight: 44 }}
                  disabled={locked || !viewed || c.baseVersionId === viewed.id}
                  onClick={() =>
                    viewed &&
                    workspace.select(session.id, {
                      baseVersionId: viewed.id,
                      confirmedDiagnosisTurnId: undefined,
                      message: '',
                    })
                  }
                >
                  以此版继续
                </button>
                <div className="ml-auto flex flex-wrap items-center gap-2">
                  {session.accepted_image_url && (
                    <a
                      className="btn btn-outline btn-sm"
                      style={{ minHeight: 44 }}
                      href={session.accepted_image_url}
                      download
                    >
                      导出已接受版本
                    </a>
                  )}
                  <button
                    className="btn btn-md btn-primary gap-2"
                    style={{ minHeight: 44 }}
                    disabled={!viewed || locked || session.accepted_version_id === viewed.id}
                    onClick={() => void c.accept()}
                  >
                    <CheckCheck className="h-4 w-4" />
                    接受当前 {label(c.viewVersionId)}
                  </button>
                </div>
                <p data-testid="accepted-version" className="w-full text-2xs text-zinc-500">
                  已接受：{label(session.accepted_version_id)} · 查看版本与修改基图独立；AI 复评供定稿参考。
                </p>
              </div>
              <div
                aria-label="版本轨道"
                className="flex min-h-24 shrink-0 gap-2 overflow-x-auto border-t border-border bg-surface p-3"
              >
                {session.versions.map((version) => (
                  <button
                    key={version.id}
                    aria-label={'查看' + label(version.id)}
                    aria-pressed={c.viewVersionId === version.id}
                    onClick={() => selectView(version.id)}
                    className={
                      'flex w-28 shrink-0 flex-col gap-1 rounded-lg border p-1.5 text-left text-xs ' +
                      (c.viewVersionId === version.id
                        ? 'border-indigo-400 bg-indigo-500/10 font-medium'
                        : 'border-border text-zinc-300')
                    }
                  >
                    <img src={version.image_url} alt="" className="h-12 w-full rounded object-cover" />
                    <span>
                      {label(version.id)}
                      {session.accepted_version_id === version.id ? ' · 已接受' : ''}
                      {c.baseVersionId === version.id ? ' · 基图' : ''}
                    </span>
                    {version.parent_version_id && (
                      <span className="text-xs text-zinc-400">来自 {label(version.parent_version_id)}</span>
                    )}
                  </button>
                ))}
              </div>
            </>
          )}
        </main>
        <aside
          aria-label="问题分析与修改意见"
          className={
            (pane === 'conversation' ? 'flex' : 'hidden') +
            ' min-h-0 flex-col border-l border-border bg-surface lg:flex'
          }
        >
          <div data-testid="refinement-conversation-scroll" className="min-h-0 flex-1 overflow-y-auto">
            {session ? (
              <SessionConversation
                turns={session.turns}
                baseVersionId={c.baseVersionId}
                versionLabel={label}
                onView={selectView}
              />
            ) : (
              <p className="p-5 text-sm text-zinc-500">选择或创建会话后，在这里提出意见。</p>
            )}
          </div>
          <SessionComposer
            controller={{ ...c, busy: c.busy || deletion.busy }}
            modelAlias={modelAlias}
            onModelChange={setModel}
            baseLabel={label(c.baseVersionId)}
          />
        </aside>
      </div>
    </section>
  );
}
