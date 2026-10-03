import { cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useStudioStore } from '@/stores/useStudioStore';
import { sessionApi } from '../sessionApi';
import type { SessionDetail } from '../sessionTypes';
import { useAcceptedResults } from './useAcceptedResults';

vi.mock('../sessionApi', () => ({ sessionApi: { get: vi.fn() } }));
const clients: QueryClient[] = [];
beforeEach(() => {
  vi.clearAllMocks();
  useStudioStore.setState({
    ...useStudioStore.getInitialState(),
    targetImages: [
      {
        id: 't',
        name: 'Target',
        url: '/original',
        processedUrl: '/baseline',
        status: 'SUCCESS',
        refinementSessionId: 's',
        refinedUrl: '/accepted/old',
        acceptedRefinement: { sessionId: 's', versionId: 'v1', revision: 1 },
      },
    ],
  });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(client);
  renderHook(() => useAcceptedResults(), {
    wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
  });
  return client;
}
it('updates normal accepted results while the session workbench is closed', async () => {
  vi.mocked(sessionApi.get).mockResolvedValue({
    id: 's',
    target_id: 't',
    revision: 2,
    accepted_version_id: 'v2',
    accepted_image_url: '/accepted/new',
    versions: [{ id: 'v2' }],
  } as SessionDetail);
  mount();
  await waitFor(() => expect(useStudioStore.getState().targetImages[0].refinedUrl).toBe('/accepted/new'));
  expect(useStudioStore.getState().targetImages[0].processedUrl).toBe('/baseline');
});
it.each([false, true])('handles removed sessions after project recovery (frozen=%s)', async (frozen) => {
  if (frozen) useStudioStore.getState().updateTargetImageById('t', { refinedUrl: 'data:image/png;base64,frozen' });
  vi.mocked(sessionApi.get).mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
  mount();
  await waitFor(() => expect(useStudioStore.getState().targetImages[0].refinementSessionId).toBeUndefined());
  const target = useStudioStore.getState().targetImages[0];
  expect(target.refinedUrl).toBe(frozen ? 'data:image/png;base64,frozen' : undefined);
  expect(target.refinementResultUnavailable).toBe(frozen ? undefined : true);
});
it('keeps the association on connection failure so exports must still verify the server version', async () => {
  vi.mocked(sessionApi.get).mockRejectedValue(new Error('Network Error'));
  const client = mount();
  await waitFor(() => expect(client.getQueryState(['refinement-session', 's'])?.status).toBe('error'));
  expect(useStudioStore.getState().targetImages[0].refinementSessionId).toBe('s');
  expect(useStudioStore.getState().targetImages[0].refinementResultUnavailable).toBeUndefined();
});
