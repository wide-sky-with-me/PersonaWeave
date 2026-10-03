import type { QueryClient } from '@tanstack/react-query';
import { useStudioStore } from '@/stores/useStudioStore';
import { useWorkbenchStore } from './workbenchStore';
import type { SessionSummary } from './sessionTypes';
import { hasFrozenAcceptedResult } from './acceptedResults';

/** Clear confirmed unavailable associations only; never restore old editing inputs. */
export async function forgetSession(client: QueryClient, id: string) {
  useWorkbenchStore.getState().forgetSession(id);
  const studio = useStudioStore.getState();
  for (const target of studio.targetImages) {
    if (target.refinementSessionId !== id) continue;
    const unavailable = !hasFrozenAcceptedResult(target) && Boolean(target.refinedUrl || target.acceptedRefinement);
    studio.updateTargetImageById(target.id, {
      refinementSessionId: undefined,
      ...(unavailable
        ? { refinedUrl: undefined, acceptedRefinement: undefined, refinementResultUnavailable: true }
        : {}),
    });
  }
  await client.cancelQueries({ queryKey: ['refinement-session', id] });
  await client.cancelQueries({ queryKey: ['refinement-sessions'] });
  client.removeQueries({ queryKey: ['refinement-session', id] });
  client.setQueriesData<SessionSummary[]>({ queryKey: ['refinement-sessions'] }, (current) =>
    current?.filter((item) => item.id !== id),
  );
}
