import { QueryClient } from '@tanstack/react-query';
import { expect, it } from 'vitest';
import { useWorkbenchStore } from './workbenchStore';
import { useStudioStore } from '@/stores/useStudioStore';
import { forgetSession } from './sessionCache';

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
