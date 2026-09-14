import { useEffect } from 'react';
import { useQueries, useQueryClient } from '@tanstack/react-query';
import { useStudioStore } from '@/stores/useStudioStore';
import { sessionApi } from '../sessionApi';
import type { SessionDetail } from '../sessionTypes';
import { syncAcceptedResult } from '../acceptedResults';

/** Keep accepted results current even while the preparation workspace is visible after restoration. */
export function useAcceptedResults() {
  const targets = useStudioStore((s) => s.targetImages);
  const client = useQueryClient();
  const ids = [
    ...new Set(
      targets
        .filter((t) => t.status === 'SUCCESS')
        .flatMap((t) => (t.refinementSessionId ? [t.refinementSessionId] : [])),
    ),
  ];
  const queries = useQueries({
    queries: ids.map((id) => ({
      queryKey: ['refinement-session', id],
      queryFn: async () => {
        const fresh = await sessionApi.get(id);
        const current = client.getQueryData<SessionDetail>(['refinement-session', id]);
        return current && current.revision > fresh.revision ? current : fresh;
      },
      retry: false,
      refetchInterval: 5000,
    })),
  });
  useEffect(() => {
    queries.forEach((query) => {
      if (query.data && !query.isError) syncAcceptedResult(query.data);
    });
  }, [queries]);
}
