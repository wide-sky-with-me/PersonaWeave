import { useStudioStore } from '@/stores/useStudioStore';
import type { SessionDetail } from './sessionTypes';
import type { TargetImageItem } from '@/types';

export const unavailableRefinementMessage = '已接受的精修结果已不可用；当前显示本地初稿，不能作为最终精修结果导出。';

/** A restored project owns these bytes independently of the session's files. */
export function hasFrozenAcceptedResult(target: TargetImageItem) {
  return Boolean(
    target.refinedUrl?.startsWith('data:image/') ||
    /^\/api\/v1\/files\/projects\/[a-f0-9]{32}\/[a-f0-9]{64}$/.test(target.refinedUrl || ''),
  );
}

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
      refinementResultUnavailable: undefined,
      acceptedRefinement: { sessionId: session.id, versionId: version.id, revision: session.revision },
    });
  }
}
