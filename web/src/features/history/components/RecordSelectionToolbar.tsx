import { Loader2, Trash2 } from 'lucide-react';
import type { useRecordDeletion } from '@/hooks/useRecordDeletion';

/** Shared selection controls for revision-checked refinement history records. */
export function RecordSelectionToolbar({
  label,
  ids,
  deletion,
}: {
  label: string;
  ids: string[];
  deletion: ReturnType<typeof useRecordDeletion>;
}) {
  return (
    <>
      {(ids.length > 0 || deletion.selected.size > 0) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/10 bg-white/[0.02] p-2.5 text-xs">
          <label className="flex items-center gap-2 text-zinc-400">
            <input
              type="checkbox"
              ref={(input) => {
                if (input)
                  input.indeterminate =
                    ids.some((id) => deletion.selected.has(id)) && !ids.every((id) => deletion.selected.has(id));
              }}
              aria-label={`全选本页${label}`}
              checked={!!ids.length && ids.every((id) => deletion.selected.has(id))}
              disabled={!ids.length || deletion.busy}
              onChange={(event) => deletion.toggle(ids, event.target.checked)}
            />
            本页可删除 {ids.length} 项
          </label>
          {!!deletion.selected.size && (
            <div className="flex items-center gap-2">
              <button
                className="btn btn-outline btn-sm text-red-300"
                disabled={deletion.busy}
                onClick={() => void deletion.request([...deletion.selected])}
                aria-label={`批量删除${label} ${deletion.selected.size} 项`}
              >
                {deletion.busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                删除已选 ({deletion.selected.size})
              </button>
              <button
                className="btn btn-ghost btn-sm"
                disabled={deletion.busy}
                onClick={() => deletion.toggle([...deletion.selected], false)}
              >
                取消选择
              </button>
            </div>
          )}
        </div>
      )}
      {deletion.error && (
        <p
          role="alert"
          className="mb-3 rounded-lg border border-amber-500/20 bg-amber-500/10 p-3 text-xs text-amber-200"
        >
          {deletion.error}
        </p>
      )}
    </>
  );
}
