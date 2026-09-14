import { useEffect } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import type { SessionDetail } from '../sessionTypes';
import { forgetSession } from '../sessionCache';

/** SSE carries notifications, never authoritative UI state; GET remains the source of facts. */
export function useSessionEvents(client: QueryClient, sessionId: string | null) {
  useEffect(() => {
    if (!sessionId || typeof EventSource === 'undefined') return;
    const key = ['refinement-session', sessionId];
    const initial = client.getQueryData<SessionDetail>(key);
    const stream = new EventSource(
      '/api/v1/refinement/sessions/' + encodeURIComponent(sessionId) + '/events?after=' + (initial?.event_cursor ?? 0),
    );
    stream.addEventListener('snapshot', (event: MessageEvent) => {
      try {
        const notice: unknown = JSON.parse(event.data);
        if (
          !notice ||
          typeof notice !== 'object' ||
          !('session_id' in notice) ||
          notice.session_id !== sessionId ||
          !('revision' in notice) ||
          typeof notice.revision !== 'number'
        )
          return;
        const current = client.getQueryData<SessionDetail>(key);
        if (!current || notice.revision > current.revision) {
          void client.invalidateQueries({ queryKey: key });
          void client.invalidateQueries({ queryKey: ['refinement-sessions'] });
        }
      } catch {
        // Malformed notifications have no authority. Running-task polling still repairs state.
      }
    });
    stream.addEventListener('deleted', (event: MessageEvent) => {
      try {
        if (JSON.parse(event.data)?.session_id !== sessionId) return;
        stream.close();
        void forgetSession(client, sessionId).then(() =>
          client.invalidateQueries({ queryKey: ['refinement-sessions'] }),
        );
      } catch {
        // Ignore malformed notices; a subsequent detail read still checks availability.
      }
    });
    // EventSource handles reconnect/Last-Event-ID. Leaving this view only closes the subscription.
    return () => stream.close();
  }, [client, sessionId]);
}
