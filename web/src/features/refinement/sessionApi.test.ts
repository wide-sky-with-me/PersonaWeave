import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/services/api';
import { sessionApi } from './sessionApi';

afterEach(() => vi.restoreAllMocks());
describe('refinement session HTTP contract', () => {
  it('deletes only the explicit ID at the reviewed revision', async () => {
    const remove = vi.spyOn(apiClient, 'delete').mockResolvedValue({ status: 204 });
    await sessionApi.delete('session', 8);
    expect(remove).toHaveBeenCalledExactlyOnceWith('/api/v1/refinement/sessions/session', {
      params: { expected_revision: 8 },
    });
  });
  it('unwraps summary/detail envelopes and never fetches images for the summary list', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { code: 200, data: [{ id: 'session' }] } });
    expect(await sessionApi.list()).toEqual([{ id: 'session' }]);
    expect(get).toHaveBeenCalledWith('/api/v1/refinement/sessions', { params: { limit: 50, offset: 0 } });
    get.mockResolvedValue({ data: { code: 200, data: { id: 'session' } } });
    expect(await sessionApi.get('session')).toEqual({ id: 'session' });
    expect(get).toHaveBeenLastCalledWith('/api/v1/refinement/sessions/session');
  });
  it('passes request UUID/revision/base unchanged and accepts an explicit version digest', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { code: 200, data: { id: 's' } } });
    const request = {
      request_id: 'uuid',
      expected_revision: 2,
      base_version_id: 'v0',
      mode: 'discuss' as const,
      message: '看看遮挡',
    };
    await sessionApi.turn('s', request);
    expect(post).toHaveBeenLastCalledWith('/api/v1/refinement/sessions/s/turns', request);
    await sessionApi.accept('s', 3, 'v1', 'digest');
    expect(post).toHaveBeenLastCalledWith('/api/v1/refinement/sessions/s/accept', {
      expected_revision: 3,
      version_id: 'v1',
      candidate_digest: 'digest',
    });
    await sessionApi.cancel('s', 4);
    expect(post).toHaveBeenLastCalledWith('/api/v1/refinement/sessions/s/cancel', { expected_revision: 4 });
  });
});
