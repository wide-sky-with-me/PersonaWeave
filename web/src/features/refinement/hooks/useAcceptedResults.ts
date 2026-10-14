import { useEffect } from 'react';
import { useQueries, useQueryClient } from '@tanstack/react-query';
import { useStudioStore } from '@/stores/useStudioStore';
import { sessionApi } from '../sessionApi';
import type { SessionDetail } from '../sessionTypes';
import { syncAcceptedResult } from '../acceptedResults';
import { forgetSession } from '../sessionCache';
import { isResourceUnavailable } from '@/utils/apiError';
import { sessionKey } from '../queryKeys';

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
      queryKey: sessionKey(id),
      queryFn: async () => {
        const fresh = await sessionApi.get(id);
        const current = client.getQueryData<SessionDetail>(sessionKey(id));
        return current && current.revision > fresh.revision ? current : fresh;
      },
      retry: false,
      refetchInterval: 5000,
    })),
  });
  useEffect(() => {
    queries.forEach((query, index) => {
      if (isResourceUnavailable(query.error)) void forgetSession(client, ids[index]);
      if (query.data && !query.isError) syncAcceptedResult(query.data);
    });
  }, [queries, client, ids]);
}
