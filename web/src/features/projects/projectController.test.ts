import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/services/api', () => ({ apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }));

async function setup() {
  const { apiClient } = await import('@/services/api');
  const { useStudioStore } = await import('@/stores/useStudioStore');
  const { useWorkbenchStore } = await import('@/features/refinement/workbenchStore');
  const { useProjectStore } = await import('./projectStore');
  const controller = await import('./projectController');
  const { studioSnapshot } = await import('./projectSnapshot');
  useProjectStore.setState({ status: 'idle' });
  return {
    apiClient,
    studio: useStudioStore,
    workbench: useWorkbenchStore,
    project: useProjectStore,
    controller,
    studioSnapshot,
  };
}

describe('explicit project recovery', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });
  it('restores the complete editor atomically without restoring task execution or acceptance', async () => {
    const s = await setup();
    const snapshot = {
      ...s.studioSnapshot(),
      targetImages: [
        {
          id: 't',
          name: 'Target',
          url: '',
          status: 'PROCESSING',
          faces: [],
          refinementSessionId: 'session',
          manualMask: { dataUrl: null, undoStack: [null], redoStack: [null] },
        },
      ],
      parameters: { ...s.studioSnapshot().parameters, identity_blend_strength: 0.67 },
      personMappings: [
        {
          person_index: 9,
          target_reference_person_index: 2,
          target_reference_url: '',
          source_asset_ids: [],
          source_manual_images: [],
        },
      ],
      isProcessing: true,
      activeTaskId: 'must-not-reconnect',
    };
    const workbench = {
      view: 'refinement',
      sessionId: 'session',
      targetId: 't',
      selections: {
        session: { baseVersionId: 'v1', viewVersionId: 'v2', message: 'Keep my draft', confirmedDiagnosisTurnId: 'd' },
      },
    };
    vi.mocked(s.apiClient.post).mockResolvedValue({
      data: {
        data: {
          id: 'project',
          revision: 7,
          title: 'Editor',
          document: { schema: 1, studio: snapshot, workbench, warnings: [] },
        },
      },
    });
    await s.controller.restoreProject('project');
    expect(s.studio.getState().parameters.identity_blend_strength).toBe(0.67);
    expect(s.studio.getState().personMappings).toEqual(snapshot.personMappings);
    expect(s.studio.getState().targetImages[0].manualMask).toEqual(snapshot.targetImages[0].manualMask);
    expect(s.studio.getState().activeTaskId).toBeNull();
    expect(s.studio.getState().isProcessing).toBe(false);
    expect(s.studio.getState().targetImages[0].status).toBe('IDLE');
    expect(s.workbench.getState().selections).toEqual(workbench.selections);
    expect(s.apiClient.post).toHaveBeenCalledExactlyOnceWith('/api/v1/projects/project/restore');
    expect(localStorage.getItem('personaweave-current-project')).toBe('project');
  });
  it('leaves the current editor unchanged when the saved document is unavailable', async () => {
    const s = await setup();
    s.studio.getState().addSourcePerson('Current');
    const before = s.studio.getState();
    vi.mocked(s.apiClient.post).mockRejectedValue(new Error('unavailable'));
    await expect(s.controller.restoreProject('missing')).rejects.toThrow('unavailable');
    expect(s.studio.getState()).toBe(before);
    expect(s.apiClient.put).not.toHaveBeenCalled();
  });
  it('does not switch projects during an active local task', async () => {
    const s = await setup();
    s.studio.getState().setIsProcessing(true);
    await expect(s.controller.restoreProject('other')).rejects.toThrow('正在执行');
    expect(s.apiClient.get).not.toHaveBeenCalled();
    expect(s.apiClient.post).not.toHaveBeenCalled();
  });

  it('repairs on refresh and saves switching between refinement targets with no session yet', async () => {
    const s = await setup();
    localStorage.setItem('personaweave-current-project', 'saved');
    vi.mocked(s.apiClient.post).mockResolvedValue({
      data: {
        data: {
          id: 'saved',
          revision: 3,
          title: 'Saved',
          document: {
            schema: 1,
            studio: {
              ...s.studioSnapshot(),
              targetImages: [
                { id: 'a', name: 'A', url: '', status: 'SUCCESS' },
                { id: 'b', name: 'B', url: '', status: 'SUCCESS' },
              ],
            },
            workbench: { view: 'refinement', targetId: 'a', sessionId: null, selections: {} },
            warnings: [],
          },
        },
      },
    });
    const stop = s.controller.startProjectAutosave();
    await vi.waitFor(() => expect(s.project.getState().id).toBe('saved'));
    expect(s.apiClient.post).toHaveBeenCalledWith('/api/v1/projects/saved/restore');
    vi.mocked(s.apiClient.put).mockResolvedValue({ data: { data: { revision: 4 } } });
    s.workbench.getState().openTarget('b');
    await s.controller.saveProject();
    expect(s.apiClient.put).toHaveBeenLastCalledWith(
      '/api/v1/projects/saved',
      expect.objectContaining({
        document: expect.objectContaining({ workbench: expect.objectContaining({ targetId: 'b' }) }),
      }),
    );
    stop();
  });
});

it('keeps a conflicted editor intact and can save it as a separate project', async () => {
  vi.resetModules();
  localStorage.clear();
  sessionStorage.clear();
  const s = await setup();
  s.project.setState({ id: 'old', revision: 1, title: 'Existing', status: 'idle' });
  const stop = s.controller.startProjectAutosave();
  s.studio.getState().addTargetImages([{ id: 't', name: 'Unsaved editor', url: '', faces: [], status: 'IDLE' }]);
  const before = s.studio.getState();
  vi.mocked(s.apiClient.put).mockRejectedValueOnce({ isAxiosError: true, response: { status: 409 } });
  await expect(s.controller.saveProject()).rejects.toMatchObject({ response: { status: 409 } });
  expect(s.studio.getState()).toBe(before);
  vi.mocked(s.apiClient.post).mockResolvedValueOnce({ data: { data: { id: 'copy', revision: 0 } } });
  vi.mocked(s.apiClient.put).mockResolvedValueOnce({ data: { data: { revision: 1 } } });
  await s.controller.forkProject();
  expect(s.project.getState().id).toBe('copy');
  expect(s.studio.getState()).toBe(before);
  expect(s.apiClient.put).toHaveBeenLastCalledWith(
    '/api/v1/projects/copy',
    expect.objectContaining({ expected_revision: 0 }),
  );
  stop();
});

it('releases removed/replaced image cache entries after saving while reusing current media', async () => {
  vi.resetModules();
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  const s = await setup();
  s.project.setState({ id: 'project', revision: 1, title: 'Editor', status: 'idle' });
  vi.mocked(s.apiClient.post).mockImplementation(async () => ({ data: { data: [{ $asset: 'a'.repeat(64) }] } }) as any);
  vi.mocked(s.apiClient.put).mockResolvedValue({ data: { data: { revision: 2 } } });
  const stop = s.controller.startProjectAutosave();
  const oldImage = 'data:image/png;base64,old-photo';
  const newImage = 'data:image/png;base64,new-photo';
  s.studio.getState().addTargetImages([{ id: 't', name: 'Photo', url: oldImage, status: 'IDLE' }]);
  await s.controller.saveProject();
  s.studio.getState().updateTargetImageById('t', { name: 'Renamed' });
  await s.controller.saveProject();
  expect(s.apiClient.post).toHaveBeenCalledTimes(1);
  s.studio.getState().updateTargetImageById('t', { url: newImage });
  await s.controller.saveProject();
  expect(s.apiClient.post).toHaveBeenCalledTimes(2);
  s.studio.getState().updateTargetImageById('t', { url: oldImage });
  await s.controller.saveProject();
  expect(s.apiClient.post).toHaveBeenCalledTimes(3);
  s.studio.getState().removeTargetImage(0);
  await s.controller.saveProject();
  s.studio.getState().addTargetImages([{ id: 'again', name: 'Photo', url: oldImage, status: 'IDLE' }]);
  await s.controller.saveProject();
  expect(s.apiClient.post).toHaveBeenCalledTimes(4);
  stop();
});

it('prunes against the final serialized save when the editor changes during an upload', async () => {
  vi.resetModules();
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  const s = await setup();
  s.project.setState({ id: 'project', revision: 1, title: 'Editor', status: 'idle' });
  let finishUpload!: (value: any) => void;
  vi.mocked(s.apiClient.post).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishUpload = resolve;
      }),
  );
  vi.mocked(s.apiClient.post).mockResolvedValue({ data: { data: [{ $asset: 'b'.repeat(64) }] } });
  vi.mocked(s.apiClient.put).mockResolvedValue({ data: { data: { revision: 2 } } });
  const stop = s.controller.startProjectAutosave();
  s.studio.getState().addTargetImages([{ id: 't', name: 'Photo', url: 'data:image/png;base64,old', status: 'IDLE' }]);
  const saving = s.controller.saveProject();
  await vi.waitFor(() => expect(s.apiClient.post).toHaveBeenCalledTimes(1));
  s.studio.getState().updateTargetImageById('t', { url: 'data:image/png;base64,new' });
  finishUpload({ data: { data: [{ $asset: 'a'.repeat(64) }] } });
  await saving;
  expect(s.apiClient.put).toHaveBeenCalledTimes(2);
  expect(s.apiClient.put).toHaveBeenLastCalledWith(
    '/api/v1/projects/project',
    expect.objectContaining({
      document: expect.objectContaining({
        studio: expect.objectContaining({
          targetImages: [expect.objectContaining({ url: { $asset: 'b'.repeat(64) } })],
        }),
      }),
    }),
  );
  s.studio.getState().updateTargetImageById('t', { name: 'Rename current photo' });
  await s.controller.saveProject();
  expect(s.apiClient.post).toHaveBeenCalledTimes(2);
  stop();
});

it('releases saved inline photographs only after the committed project owns them, preserving brush data', async () => {
  vi.resetModules();
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  const s = await setup();
  const id = 'a'.repeat(32),
    digest = 'b'.repeat(64);
  s.project.setState({ id, revision: 1, title: 'Saved', status: 'idle' });
  vi.mocked(s.apiClient.post).mockResolvedValue({ data: { data: [{ $asset: digest }] } });
  let commit!: (value: any) => void;
  vi.mocked(s.apiClient.put).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        commit = resolve;
      }),
  );
  const stop = s.controller.startProjectAutosave();
  const image = 'data:image/png;base64,photo',
    mask = 'data:image/png;base64,mask';
  s.studio.getState().addTargetImages([
    {
      id: 't',
      name: 'Photo',
      url: image,
      faces: [],
      status: 'SUCCESS',
      refinedUrl: image,
      manualMask: { dataUrl: mask, undoStack: [], redoStack: [] },
    },
  ]);
  const saving = s.controller.saveProject();
  await vi.waitFor(() => expect(s.apiClient.put).toHaveBeenCalled());
  expect(s.studio.getState().targetImages[0].url).toBe(image);
  commit({ data: { data: { revision: 2 } } });
  await saving;
  expect(s.studio.getState().targetImages[0]).toMatchObject({
    url: `/api/v1/files/projects/${id}/${digest}`,
    refinedUrl: `/api/v1/files/projects/${id}/${digest}`,
    manualMask: { dataUrl: mask },
  });
  stop();
});
