import axios from 'axios';
import { apiClient } from '@/services/api';
import { useStudioStore } from '@/stores/useStudioStore';
import { useWorkbenchStore } from '@/features/refinement/workbenchStore';
import { useProjectStore } from './projectStore';
import {
  freezeDocument,
  hydrateDocument,
  releaseSavedPhotos,
  sameStudio,
  studioKeys,
  studioSnapshot,
  type Asset,
  type ProjectDetail,
  type ProjectDocument,
  type Upload,
} from './projectSnapshot';

const POINTER = 'personaweave-current-project';
let initialized: Promise<void> | undefined;
let suspended = false;
let switching = false;
let lastEditorView: 'prepare' | 'refinement' = 'prepare';
let dirty = false;
let timer: ReturnType<typeof setTimeout> | undefined;
let saving: Promise<void> | undefined;
const assets = new Map<string, Promise<Asset[]>>();

function errorMessage(error: unknown) {
  if (axios.isAxiosError(error)) {
    if (error.response?.status === 409)
      return '工程已在其他页面修改。可另存为新工程保留当前内容，或刷新读取服务器版本。';
    if (error.response?.status === 404 || error.response?.status === 410)
      return '工程或图片已被清理，当前界面未被替换。';
  }
  return error instanceof Error ? error.message : '工程保存或恢复失败，请重试';
}
function capture(): ProjectDocument {
  const studio = studioSnapshot(),
    w = useWorkbenchStore.getState();
  const ids = new Set(studio.targetImages.map((t) => t.refinementSessionId).filter(Boolean));
  const ownSession = w.sessionId && ids.has(w.sessionId);
  const view = w.view === 'history' ? lastEditorView : w.view;
  return {
    schema: 1,
    studio,
    workbench: {
      view: view === 'refinement' && !ownSession && !w.targetId ? 'prepare' : view,
      sessionId: ownSession ? w.sessionId : null,
      targetId: w.targetId,
      selections: Object.fromEntries(Object.entries(w.selections).filter(([id]) => ids.has(id))),
    },
    warnings: useProjectStore.getState().warnings,
  };
}
function changed() {
  if (suspended || useProjectStore.getState().status === 'loading') return;
  if (
    !useProjectStore.getState().id &&
    !useStudioStore.getState().targetImages.length &&
    !useStudioStore.getState().sourceGallery.length
  )
    return;
  dirty = true;
  useProjectStore.setState({ status: 'dirty' });
  clearTimeout(timer);
  timer = setTimeout(() => {
    void saveProject().catch(() => undefined);
  }, 700);
}

/** Serialize saves, so slow picture uploads cannot overwrite a newer editor state. */
export async function saveProject(): Promise<void> {
  if (saving) {
    await saving;
    if (dirty) return saveProject();
    return;
  }
  if (!dirty) return;
  saving = (async () => {
    try {
      while (dirty) {
        dirty = false;
        useProjectStore.setState({ status: 'saving', error: null });
        let state = useProjectStore.getState();
        if (!state.id) {
          const { data } = await apiClient.post('/api/v1/projects');
          const project = data.data as ProjectDetail;
          localStorage.setItem(POINTER, project.id);
          useProjectStore.setState({ id: project.id, revision: project.revision });
          state = useProjectStore.getState();
        }
        const snapshot = capture();
        const title = state.title || snapshot.studio.targetImages[0]?.name || '人物编辑工程';
        const id = state.id!;
        const usedAssets = new Set<string>();
        const upload: Upload = (source) => {
          const owned = source.image?.match(/^\/api\/v1\/files\/projects\/([a-f0-9]{32})\/([a-f0-9]{64})$/);
          if (owned && owned[1] === id) return Promise.resolve([{ $asset: owned[2] }]);
          const key = id + JSON.stringify(source);
          usedAssets.add(key);
          if (!assets.has(key))
            assets.set(
              key,
              apiClient
                .post(`/api/v1/projects/${id}/assets`, source)
                .then((r) => r.data.data as Asset[])
                .catch((error) => {
                  assets.delete(key);
                  throw error;
                }),
            );
          return assets.get(key)!;
        };
        const document = (await freezeDocument(snapshot, upload)) as ProjectDocument;
        const taskId = useStudioStore.getState().activeTaskId;
        const { data } = await apiClient.put(`/api/v1/projects/${id}`, {
          expected_revision: state.revision,
          title,
          document,
          batch_ids: taskId ? [taskId] : [],
          session_ids: snapshot.studio.targetImages.flatMap((t) =>
            t.refinementSessionId ? [t.refinementSessionId] : [],
          ),
        });
        useProjectStore.setState({ revision: data.data.revision, title });
        const currentStudio = useStudioStore.getState();
        if (
          !currentStudio.isProcessing &&
          currentStudio.targetImages.every((t) => t.faces !== undefined) &&
          studioKeys.every((key) => snapshot.studio[key] === currentStudio[key])
        ) {
          // Commit succeeded; swap inline photo copies for our immutable disk-backed URLs.
          // Never replace input edited during the upload, or recursively schedule autosave.
          const wasSuspended = suspended;
          suspended = true;
          try {
            const compact = releaseSavedPhotos(snapshot.studio, document.studio, id);
            if (compact !== snapshot.studio) useStudioStore.setState(compact);
          } finally {
            suspended = wasSuspended;
          }
          assets.clear();
        }

        // Keys can contain full data URIs. Keep deduplication for this saved editor,
        // but release removed photos and superseded mask frames after a successful save.
        for (const key of assets.keys()) if (!usedAssets.has(key)) assets.delete(key);
      }
      useProjectStore.setState({ status: 'idle' });
    } catch (error) {
      dirty = true;
      useProjectStore.setState({ status: 'error', error: errorMessage(error) });
      throw error;
    }
  })();
  try {
    await saving;
  } finally {
    saving = undefined;
  }
}

function assertIdle() {
  const s = useStudioStore.getState();
  if (s.isProcessing || s.targetImages.some((t) => t.status === 'PROCESSING'))
    throw new Error('当前换脸任务正在执行，请完成后再切换工程。');
}

async function apply(project: ProjectDetail) {
  const document = await hydrateDocument(project);
  assertIdle();
  const defaults = useStudioStore.getInitialState();
  const editor = Object.fromEntries(studioKeys.map((key) => [key, document.studio[key] ?? defaults[key]]));
  editor.parameters = { ...defaults.parameters, ...document.studio.parameters };
  editor.targetImages = document.studio.targetImages.map((t) => ({
    ...t,
    status: t.status === 'PROCESSING' ? 'IDLE' : t.status,
  }));
  editor.activeTargetIndex = Math.min(
    Math.max(Number(editor.activeTargetIndex) || 0, 0),
    Math.max(document.studio.targetImages.length - 1, 0),
  );
  const w = document.workbench;
  const ids = new Set(document.studio.targetImages.map((t) => t.refinementSessionId).filter(Boolean));
  const previousSelections = useWorkbenchStore.getState().selections;
  useStudioStore.setState({
    ...editor,
    isProcessing: false,
    activeTaskId: null,
    batchProgress: 0,
    isMatrixOpen: false,
  });
  useWorkbenchStore.setState({
    view: ['prepare', 'refinement', 'history'].includes(w.view) ? w.view : 'prepare',
    sessionId: w.sessionId && ids.has(w.sessionId) ? w.sessionId : null,
    targetId: document.studio.targetImages.some((t) => t.id === w.targetId) ? w.targetId : null,
    selections: {
      ...previousSelections,
      ...Object.fromEntries(Object.entries(w.selections ?? {}).filter(([id]) => ids.has(id))),
    },
  });
  useProjectStore.setState({
    id: project.id,
    revision: project.revision,
    title: project.title,
    warnings: document.warnings,
    status: 'idle',
    error: null,
    workspaceRevision: useProjectStore.getState().workspaceRevision + 1,
  });
  localStorage.setItem(POINTER, project.id);
  assets.clear();
  dirty = false;
}

/** Explicit recovery saves the current editor first; viewing a history record never calls this. */
export async function restoreProject(id: string, kind?: 'batch' | 'session') {
  if (switching) return;
  switching = true;
  try {
    assertIdle();
    await saveProject();
    suspended = true;
    clearTimeout(timer);
    useProjectStore.setState({ status: 'loading', error: null });
    const { data } = kind
      ? await apiClient.post(`/api/v1/projects/recover/${kind}/${id}`)
      : await apiClient.post(`/api/v1/projects/${id}/restore`);
    await apply(data.data);
    if (kind === 'session') useWorkbenchStore.getState().openSession(id);
    else if (kind === 'batch') useWorkbenchStore.getState().navigate('prepare');
  } catch (error) {
    useProjectStore.setState({ status: 'error', error: errorMessage(error) });
    throw error;
  } finally {
    suspended = false;
    switching = false;
  }
}

export async function newProject() {
  if (switching) throw new Error('正在切换工程，请稍候。');
  switching = true;
  try {
    assertIdle();
    await saveProject();
    suspended = true;
    useStudioStore.setState(Object.fromEntries(studioKeys.map((key) => [key, useStudioStore.getInitialState()[key]])));
    useStudioStore.setState({ activeTaskId: null, isProcessing: false, batchProgress: 0 });
    useWorkbenchStore.setState({ view: 'prepare', sessionId: null, targetId: null });
    useProjectStore.setState({
      id: null,
      revision: 0,
      title: '',
      warnings: [],
      status: 'idle',
      error: null,
      workspaceRevision: useProjectStore.getState().workspaceRevision + 1,
    });
    localStorage.removeItem(POINTER);
    assets.clear();
    dirty = false;
  } catch (error) {
    useProjectStore.setState({ error: errorMessage(error), status: 'error' });
    throw error;
  } finally {
    suspended = false;
    switching = false;
  }
}

/** Preserve a conflicted editor as a separate project without overwriting either saved copy. */
export async function forkProject() {
  if (switching) return;
  assertIdle();
  useProjectStore.setState({ id: null, revision: 0, title: (useProjectStore.getState().title || '工程') + ' · 副本' });
  assets.clear();
  dirty = true;
  await saveProject();
}

export async function retryProject() {
  const pointer = localStorage.getItem(POINTER);
  if (!useProjectStore.getState().id && pointer && !dirty) await restoreProject(pointer);
  else await saveProject();
}

export function startProjectAutosave() {
  if (!initialized)
    initialized = (async () => {
      const id = localStorage.getItem(POINTER);
      if (id) {
        try {
          suspended = true;
          const { data } = await apiClient.post(`/api/v1/projects/${id}/restore`);
          await apply(data.data);
        } catch (error) {
          useProjectStore.setState({ status: 'error', error: errorMessage(error) });
          // Keep the pointer for explicit retry; never overwrite an unavailable project with an empty canvas.
        } finally {
          suspended = false;
        }
      } else useProjectStore.setState({ status: 'idle' });
    })();
  const offStudio = useStudioStore.subscribe((state, previous) => {
    if (!sameStudio(state, previous) || state.activeTaskId !== previous.activeTaskId) changed();
  });
  const offWorkbench = useWorkbenchStore.subscribe((state, previous) => {
    if (state.view !== 'history') lastEditorView = state.view;
    if (
      state.selections !== previous.selections ||
      state.view !== previous.view ||
      state.targetId !== previous.targetId ||
      state.sessionId !== previous.sessionId
    )
      changed();
  });
  const beforeUnload = (event: BeforeUnloadEvent) => {
    if (dirty || saving) {
      event.preventDefault();
      event.returnValue = '';
    }
  };
  window.addEventListener('beforeunload', beforeUnload);
  return () => {
    offStudio();
    offWorkbench();
    window.removeEventListener('beforeunload', beforeUnload);
  };
}
