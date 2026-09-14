import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { buildWorkspaceZip } from './workspaceExport';
import { sessionApi } from '@/features/refinement/sessionApi';
import type { TargetImageItem } from '@/types';
import type { SessionDetail } from '@/features/refinement/sessionTypes';

vi.mock('@/features/refinement/sessionApi', () => ({ sessionApi: { get: vi.fn() } }));
const png = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 1, 2, 3]);
const jpg = new Uint8Array([255, 216, 255, 1, 2, 3]);
const target = (id: string, sessionId?: string): TargetImageItem => ({
  id,
  name: 'same.jpg',
  url: '/original',
  processedUrl: '/local/' + id,
  status: 'SUCCESS',
  refinementSessionId: sessionId,
});
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => ({
      ok: true,
      arrayBuffer: async () => (url.startsWith('/accepted') ? png : jpg).buffer,
    })),
  );
});
afterEach(() => vi.unstubAllGlobals());
it('packs accepted bytes and untouched local results from a restored workspace with no active batch', async () => {
  vi.mocked(sessionApi.get).mockResolvedValue({
    id: 's',
    target_id: 'a',
    accepted_version_id: 'v2',
    accepted_image_url: '/accepted/v2',
    versions: [],
  } as unknown as SessionDetail);
  const { zip, packed, failures } = await buildWorkspaceZip([target('a', 's'), target('b'), target('c')]);
  expect(packed).toBe(3);
  expect(failures).toEqual([]);
  expect(fetch).toHaveBeenCalledWith('/accepted/v2');
  expect(fetch).not.toHaveBeenCalledWith('/local/a');
  expect(await zip.file('PersonaWeave_Results/same_result.png')!.async('uint8array')).toEqual(png);
  expect(await zip.file('PersonaWeave_Results/same_result.jpg')!.async('uint8array')).toEqual(jpg);
  expect(zip.file('PersonaWeave_Results/same_result_2.jpg')).not.toBeNull();
});
it('never promotes unaccepted candidates or falls back to local when accepted download fails', async () => {
  vi.mocked(sessionApi.get).mockResolvedValue({
    target_id: 'a',
    accepted_version_id: 'v2',
    accepted_image_url: '/accepted/missing',
    versions: [],
  } as unknown as SessionDetail);
  vi.mocked(fetch).mockImplementation(
    async (url) => ({ ok: url !== '/accepted/missing', status: 404, arrayBuffer: async () => jpg.buffer }) as Response,
  );
  const { zip, packed, failures } = await buildWorkspaceZip([target('a', 's'), target('b')]);
  expect(packed).toBe(1);
  expect(failures).toHaveLength(1);
  expect(zip.file('PersonaWeave_Results/FAILED_DOWNLOADS.txt')).not.toBeNull();
  expect(fetch).not.toHaveBeenCalledWith('/local/a');
  vi.mocked(sessionApi.get).mockResolvedValue({
    target_id: 'a',
    accepted_version_id: null,
    accepted_image_url: null,
    versions: [{ image_url: '/candidate' }],
  } as unknown as SessionDetail);
  await buildWorkspaceZip([target('a', 's')]);
  expect(fetch).toHaveBeenCalledWith('/local/a');
  expect(fetch).not.toHaveBeenCalledWith('/candidate');
});
it('can export an independent frozen accepted image after the session was deleted', async () => {
  vi.mocked(sessionApi.get).mockRejectedValue(new Error('deleted'));
  const frozen = 'data:image/png;base64,frozen';
  const { packed } = await buildWorkspaceZip([{ ...target('a', 's'), refinedUrl: frozen }]);
  expect(packed).toBe(1);
  expect(fetch).toHaveBeenCalledWith(frozen);
});
