import { useEffect } from 'react';
import { FolderPlus, Loader2, Save } from 'lucide-react';
import { useProjectStore } from './projectStore';
import { newProject, retryProject, forkProject, startProjectAutosave } from './projectController';

export function ProjectControls() {
  const state = useProjectStore();
  useEffect(startProjectAutosave, []);
  const busy = state.status === 'loading' || state.status === 'saving';
  return (
    <>
      <div
        aria-label="工程保存"
        className="flex min-h-10 flex-wrap items-center justify-between gap-2 border-b border-white/10 bg-surface-1 px-4 py-1.5 text-xs"
      >
        <div className="flex min-w-0 items-center gap-2">
          <span className="max-w-60 truncate text-zinc-300">{state.title || '新工程'}</span>
          <span role="status" className={state.status === 'error' ? 'text-amber-300' : 'text-zinc-500'}>
            {state.status === 'loading'
              ? '正在恢复工程…'
              : state.status === 'saving'
                ? '正在保存…'
                : state.status === 'dirty'
                  ? '有未保存修改'
                  : state.status === 'error'
                    ? '工程保存/恢复失败'
                    : state.id
                      ? '已保存到服务器'
                      : '添加图片后自动保存'}
          </span>
        </div>
        <div className="flex shrink-0 gap-2">
          {state.status === 'error' && state.id && (
            <button className="btn btn-outline btn-xs" onClick={() => void forkProject().catch(() => undefined)}>
              另存为新工程
            </button>
          )}
          <button
            className="btn btn-ghost btn-xs"
            disabled={busy}
            onClick={() => void retryProject().catch(() => undefined)}
          >
            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}保存工程
          </button>
          <button
            className="btn btn-outline btn-xs"
            disabled={busy}
            onClick={() => void newProject().catch(() => undefined)}
          >
            <FolderPlus className="h-3 w-3" />
            新工程
          </button>
        </div>
        {state.error && (
          <p role="alert" className="w-full text-amber-300">
            {state.error}
          </p>
        )}
        {!!state.warnings.length && (
          <details className="w-full text-amber-200/80">
            <summary className="cursor-pointer">此工程由旧记录恢复，有 {state.warnings.length} 项信息不完整</summary>
            <ul className="mt-2 list-disc space-y-1 pl-4">
              {state.warnings.map((warning, i) => (
                <li key={i}>{warning}</li>
              ))}
            </ul>
          </details>
        )}
      </div>
      {state.status === 'loading' && (
        <div
          role="status"
          className="fixed inset-0 z-[120] flex items-center justify-center bg-black/70 backdrop-blur-sm"
        >
          <div className="flex items-center gap-3 rounded-xl border border-white/10 bg-zinc-900 p-6 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在校验图片并恢复整个工程…
          </div>
        </div>
      )}
    </>
  );
}
