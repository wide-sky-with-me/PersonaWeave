import { describe, expect, it } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { sessionKey, sessionKeys, sessionsKey } from './queryKeys';

// Keep literals independent of the implementation: existing caches use these exact tuples.
describe('refinement cache identity', () => {
  it('preserves detail, disabled-detail and list keys', () => {
    expect(sessionKey('s1')).toEqual(['refinement-session', 's1']);
    expect(sessionKey(null)).toEqual(['refinement-session', null]);
    expect(sessionKeys).toEqual(['refinement-session']);
    expect(sessionsKey).toEqual(['refinement-sessions']);
  });

  it('invalidates paginated lists without touching session details', async () => {
    const client = new QueryClient();
    client.setQueryData(['refinement-sessions'], []);
    client.setQueryData(['refinement-sessions', 'history', 2], []);
    client.setQueryData(['refinement-session', 's1'], { id: 's1' });
    await client.invalidateQueries({ queryKey: sessionsKey });
    expect(client.getQueryState(['refinement-sessions'])?.isInvalidated).toBe(true);
    expect(client.getQueryState(['refinement-sessions', 'history', 2])?.isInvalidated).toBe(true);
    expect(client.getQueryState(['refinement-session', 's1'])?.isInvalidated).toBe(false);
    await client.invalidateQueries({ queryKey: sessionKeys });
    expect(client.getQueryState(['refinement-session', 's1'])?.isInvalidated).toBe(true);
    client.clear();
  });
});
