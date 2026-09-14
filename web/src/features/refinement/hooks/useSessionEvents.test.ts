import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useSessionEvents } from './useSessionEvents';

afterEach(() => vi.unstubAllGlobals());
it('falls back to GET polling when EventSource is unavailable', () => {
  vi.stubGlobal('EventSource', undefined);
  const view = renderHook(() => useSessionEvents(new QueryClient(), 's'));
  view.unmount();
});
describe('SSE snapshot notifications', () => {
  it('closes a deleted session stream and clears its cached snapshot', async () => {
    const handlers = new Map<string, (event: MessageEvent) => void>();
    const close = vi.fn();
    vi.stubGlobal(
      'EventSource',
      class {
        close = close;
        addEventListener(name: string, handler: (event: MessageEvent) => void) {
          handlers.set(name, handler);
        }
      },
    );
    const client = new QueryClient();
    client.setQueryData(['refinement-session', 's'], { id: 's' });
    const view = renderHook(() => useSessionEvents(client, 's'));
    act(() => handlers.get('deleted')?.({ data: JSON.stringify({ session_id: 's' }) } as MessageEvent));
    await waitFor(() => expect(client.getQueryData(['refinement-session', 's'])).toBeUndefined());
    expect(close).toHaveBeenCalled();
    view.unmount();
    client.clear();
  });
  it('opens from server cursor, invalidates newer revisions and only closes on navigation', () => {
    const streams: FakeSource[] = [];
    class FakeSource {
      close = vi.fn();
      receive: ((event: MessageEvent) => void) | undefined;
      constructor(public url: string) {
        streams.push(this);
      }
      addEventListener(_name: string, handler: (event: MessageEvent) => void) {
        if (_name === 'snapshot') this.receive = handler;
      }
    }
    vi.stubGlobal('EventSource', FakeSource);
    const client = new QueryClient();
    client.setQueryData(['refinement-session', 's'], { id: 's', revision: 5, event_cursor: 12 });
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const view = renderHook(({ id }) => useSessionEvents(client, id), { initialProps: { id: 's' } });
    expect(streams[0].url).toBe('/api/v1/refinement/sessions/s/events?after=12');
    const emit = (data: string) => streams[0].receive?.({ data } as MessageEvent);
    emit('bad json');
    emit(JSON.stringify({ session_id: 'other', revision: 7 }));
    emit(JSON.stringify({ session_id: 's', revision: 5 }));
    expect(invalidate).not.toHaveBeenCalled();
    emit(JSON.stringify({ session_id: 's', revision: 6 }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['refinement-session', 's'] });
    client.setQueryData(['refinement-session', 's'], { id: 's', revision: 6, event_cursor: 13 });
    view.rerender({ id: 's' });
    expect(streams).toHaveLength(1);
    view.rerender({ id: 'other' });
    expect(streams[0].close).toHaveBeenCalledOnce();
    expect(streams).toHaveLength(2);
    view.unmount();
    expect(streams[1].close).toHaveBeenCalledOnce();
    client.clear();
  });
});
