import { webcrypto } from 'node:crypto';
import { afterEach, expect, it, vi } from 'vitest';
import { apiClient } from '@/services/api';
import { hasFrozenAcceptedResult } from '@/features/refinement/acceptedResults';
import { freezeDocument, hydrateDocument, studioSnapshot, type ProjectDetail } from './projectSnapshot';

vi.mock('@/services/api', () => ({ apiClient: { get: vi.fn() } }));
// Exercise the size branch with small bytes; backend regression uses a real >20 MiB PNG.
vi.mock('@/utils/imageFiles', () => ({ MAX_IMAGE_BYTES: 4 }));
afterEach(() => vi.unstubAllGlobals());

it('restores a verified large snapshot as a managed image and preserves it on the next save', async () => {
  vi.stubGlobal('crypto', webcrypto);
  const bytes = new Uint8Array([1, 2, 3, 4, 5]).buffer;
  const digest = Buffer.from(await webcrypto.subtle.digest('SHA-256', bytes)).toString('hex');
  const id = 'a'.repeat(32);
  vi.mocked(apiClient.get).mockResolvedValue({ data: bytes, headers: { 'content-type': 'image/png' } });
  const project = {
    id,
    document: {
      schema: 1,
      studio: {
        ...studioSnapshot(),
        targetImages: [{ id: 't', name: 'Large', status: 'SUCCESS', refinedUrl: { $asset: digest } }],
      },
      workbench: { view: 'studio', selections: {} },
      warnings: [],
    },
  } as unknown as ProjectDetail;
  const restored = await hydrateDocument(project);
  const target = restored.studio.targetImages[0];
  expect(target.refinedUrl).toBe(`/api/v1/files/projects/${id}/${digest}`);
  expect(hasFrozenAcceptedResult(target)).toBe(true);
  const upload = vi.fn().mockResolvedValue([{ $asset: digest }]);
  await freezeDocument(restored, upload);
  expect(upload).toHaveBeenCalledWith({ image: target.refinedUrl });
  vi.mocked(apiClient.get).mockResolvedValue({ data: new Uint8Array([9, 9, 9, 9, 9]).buffer, headers: {} });
  await expect(hydrateDocument(project)).rejects.toThrow('工程图片校验失败');
});

it('does not mistake session publications or unvalidated paths for independent project copies', () => {
  const base = { id: 't', name: 'Target', url: '/original', status: 'SUCCESS' as const };
  for (const refinedUrl of [
    '/api/v1/files/refinement/accepted/sessions/result.png',
    '/api/v1/files/projects/../secret',
    `https://other.test/api/v1/files/projects/${'a'.repeat(32)}/${'b'.repeat(64)}`,
  ])
    expect(hasFrozenAcceptedResult({ ...base, refinedUrl })).toBe(false);
});

it('keeps even small restored photos on disk while preserving the base64 brush contract', async () => {
  vi.stubGlobal('crypto', webcrypto);
  const bytes = new Uint8Array([1, 2]).buffer;
  const digest = Buffer.from(await webcrypto.subtle.digest('SHA-256', bytes)).toString('hex');
  const id = 'c'.repeat(32);
  vi.mocked(apiClient.get).mockResolvedValue({ data: bytes, headers: { 'content-type': 'image/png' } });
  const project = {
    id,
    document: {
      schema: 1,
      studio: {
        ...studioSnapshot(),
        targetImages: [
          {
            id: 't',
            status: 'SUCCESS',
            url: { $asset: digest },
            refinedUrl: { $asset: digest },
            manualMask: { dataUrl: { $asset: digest }, undoStack: [], redoStack: [] },
          },
        ],
      },
      workbench: { view: 'prepare', selections: {} },
      warnings: [],
    },
  } as unknown as ProjectDetail;
  const restored = await hydrateDocument(project);
  expect(restored.studio.targetImages[0].url).toBe(`/api/v1/files/projects/${id}/${digest}`);
  expect(restored.studio.targetImages[0].manualMask?.dataUrl).toMatch(/^data:image\/png;base64,/);
});
