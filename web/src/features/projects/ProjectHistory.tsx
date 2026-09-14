import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/services/api';
import { useProjectStore } from './projectStore';
import { restoreProject, newProject, saveProject } from './projectController';
import type { ProjectSummary } from './projectSnapshot';

export function RestoreProjectButton({ id, kind }: { id: string; kind?: 'batch' | 'session' }) {
  const busy = useProjectStore((state) => state.status === 'loading' || state.status === 'saving');
  return (
    <button
      className="btn btn-outline btn-xs shrink-0"
      disabled={busy}
      aria-label={`恢复工程 ${id}`}
      onClick={() => void restoreProject(id, kind).catch(() => undefined)}
    >
      恢复工程
    </button>
  );
}

export function ProjectHistory() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ['projects'],
    queryFn: async () => (await apiClient.get('/api/v1/projects')).data.data as ProjectSummary[],
    refetchInterval: 5000,
  });
  const state = useProjectStore();
  async function remove(project: ProjectSummary) {
    if (!window.confirm(`删除工程“${project.title}”及其保存的图片、画笔和草稿？原换脸任务及精修会话会保留。`)) return;
    try {
      // Finish the active project's final save before checking its deletion revision.
      let revision = project.revision;
      if (state.id === project.id) {
        await saveProject();
        revision = useProjectStore.getState().revision;
        await newProject();
      }
      await apiClient.delete(`/api/v1/projects/${project.id}`, { params: { expected_revision: revision } });
      await client.invalidateQueries({ queryKey: ['projects'] });
    } catch {
      useProjectStore.setState({ error: '删除工程失败，请刷新后重试。' });
    }
  }
  return (
    <section
      aria-label="可恢复工程"
      className="mx-auto mb-5 max-w-7xl rounded-2xl border border-indigo-400/20 bg-indigo-500/5 p-4"
    >
      <h2 className="text-sm font-semibold text-zinc-100">编辑工程</h2>
      <p className="mt-1 text-xs text-zinc-400">
        恢复素材、画布、参数、画笔与精修草稿，继续上次编辑。下方任务记录仍可独立回看。
      </p>
      {query.isLoading && <p className="mt-3 text-xs text-zinc-400">正在读取工程…</p>}
      {query.isError && (
        <p role="alert" className="mt-3 text-xs text-amber-300">
          工程历史读取失败。
        </p>
      )}
      {!query.isLoading && !query.isError && !query.data?.length && (
        <p className="mt-3 text-xs text-zinc-500">还没有保存的工程。旧任务可通过“恢复工程”迁移已有内容。</p>
      )}
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        {query.data?.map((project) => (
          <article
            key={project.id}
            className="flex min-w-0 items-center gap-3 rounded-xl border border-white/10 bg-surface-1 p-3"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm">{project.title}</p>
              <p className="mt-1 text-2xs text-zinc-500">{new Date(project.updated_at).toLocaleString('zh-CN')}</p>
            </div>
            <RestoreProjectButton id={project.id} />
            <button
              className="btn btn-ghost btn-xs text-zinc-500 hover:text-red-300"
              disabled={state.status === 'loading' || state.status === 'saving'}
              aria-label={`删除工程 ${project.id}`}
              onClick={() => void remove(project)}
            >
              删除
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}
