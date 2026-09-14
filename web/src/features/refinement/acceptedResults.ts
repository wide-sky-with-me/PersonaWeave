import { useStudioStore } from '@/stores/useStudioStore';
import type { SessionDetail } from './sessionTypes';

/** Project only explicit server acceptance into its associated canvas; keep the local baseline intact. */
export function syncAcceptedResult(session: SessionDetail) {
  const version = session.versions.find((v) => v.id === session.accepted_version_id);
  if (!version || !session.accepted_image_url) return;
  const studio = useStudioStore.getState();
  for (const target of studio.targetImages) {
    if (target.id !== session.target_id || target.refinementSessionId !== session.id || target.status !== 'SUCCESS')
      continue;
    const previous = target.acceptedRefinement;
    if (previous?.sessionId === session.id && previous.revision > session.revision) continue;
    // A saved project may own a frozen copy of this same version. Keep it available independently.
    if (previous?.sessionId === session.id && previous.versionId === version.id && target.refinedUrl) {
      if (previous.revision < session.revision)
        studio.updateTargetImageById(target.id, {
          acceptedRefinement: { ...previous, revision: session.revision },
        });
      continue;
    }
    studio.updateTargetImageById(target.id, {
      refinedUrl: session.accepted_image_url,
      acceptedRefinement: { sessionId: session.id, versionId: version.id, revision: session.revision },
    });
  }
}
