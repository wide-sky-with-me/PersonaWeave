import { QueryClient } from '@tanstack/react-query';
import { expect, it } from 'vitest';
import { useWorkbenchStore } from './workbenchStore';
import { useStudioStore } from '@/stores/useStudioStore';
import { forgetSession } from './sessionCache';
import { freezeDocument, studioSnapshot } from '@/features/projects/projectSnapshot';

it('clears a deleted session and its lightweight associations without changing local images or another session', async () => {
  const client = new QueryClient();
  useWorkbenchStore.setState({
    sessionId: 's1',
    targetId: 't',
    selections: { s1: { message: 'old' }, s2: { message: 'keep' } },
    pendingTurns: {
      s1: { request_id: 'r', expected_revision: 1, mode: 'generate', base_version_id: 'v', message: 'old' },
    },
  });
  useStudioStore.setState({
    targetImages: [
      { id: 't', name: 'keep', url: '/original', processedUrl: '/local', status: 'SUCCESS', refinementSessionId: 's1' },
    ],
  });
  client.setQueryData(['refinement-sessions'], [{ id: 's1' }, { id: 's2' }]);
  client.setQueryData(['refinement-session', 's1'], { id: 's1' });
  await forgetSession(client, 's1');
  expect(useWorkbenchStore.getState().sessionId).toBeNull();
  expect(useWorkbenchStore.getState().pendingTurns.s1).toBeUndefined();
  expect(useWorkbenchStore.getState().selections).toEqual({ s2: { message: 'keep' } });
  expect(client.getQueryData(['refinement-session', 's1'])).toBeUndefined();
  expect(client.getQueryData(['refinement-sessions'])).toEqual([{ id: 's2' }]);
  expect(useStudioStore.getState().targetImages[0]).toMatchObject({
    url: '/original',
    processedUrl: '/local',
    status: 'SUCCESS',
  });
  expect(useStudioStore.getState().targetImages[0].refinementSessionId).toBeUndefined();
  client.clear();
});

it.each([false, true])(
  'handles deleted accepted results without mislabeling the local baseline (frozen=%s)',
  async (frozen) => {
    const client = new QueryClient();
    const refinedUrl = frozen ? 'data:image/png;base64,frozen' : '/api/v1/files/refinement/accepted/sessions/s-v.png';
    const acceptedRefinement = { sessionId: 's', versionId: 'v', revision: 4 };
    useStudioStore.setState({
      targetImages: [
        {
          id: 't',
          name: 'Target',
          url: '/original',
          processedUrl: '/local',
          status: 'SUCCESS',
          refinementSessionId: 's',
          refinedUrl,
          acceptedRefinement,
        },
      ],
    });
    await forgetSession(client, 's');
    const target = useStudioStore.getState().targetImages[0];
    expect(target.refinementSessionId).toBeUndefined();
    expect(target.processedUrl).toBe('/local');
    expect(target.refinedUrl).toBe(frozen ? refinedUrl : undefined);
    expect(target.acceptedRefinement).toEqual(frozen ? acceptedRefinement : undefined);
    expect(target.refinementResultUnavailable).toBe(frozen ? undefined : true);
    // Autosave must not attempt to freeze a now-deleted session URL.
    const uploaded: string[] = [];
    const saved = (await freezeDocument(
      {
        schema: 1,
        studio: studioSnapshot(),
        workbench: { view: 'prepare', sessionId: null, targetId: null, selections: {} },
        warnings: [],
      },
      async (source) => {
        if (source.image) uploaded.push(source.image);
        return [{ $asset: 'a'.repeat(64) }];
      },
    )) as any;
    expect(uploaded).not.toContain('/api/v1/files/refinement/accepted/sessions/s-v.png');
    expect(saved.studio.targetImages[0].refinementResultUnavailable).toBe(frozen ? undefined : true);
    client.clear();
  },
);
