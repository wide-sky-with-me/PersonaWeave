import { beforeEach, describe, expect, it } from 'vitest';
import { useStudioStore } from '@/stores/useStudioStore';
import { syncAcceptedResult } from './acceptedResults';
import type { SessionDetail } from './sessionTypes';

const session = (version = 'v1', revision = 5) =>
  ({
    id: 's',
    target_id: 't',
    revision,
    accepted_version_id: version,
    accepted_image_url: '/accepted/' + version,
    versions: [{ id: 'v0' }, { id: 'v1' }, { id: 'v2' }],
  }) as SessionDetail;
beforeEach(() =>
  useStudioStore.setState({
    targetImages: [
      {
        id: 't',
        name: 'Image',
        url: '/original',
        processedUrl: '/local',
        status: 'SUCCESS',
        refinementSessionId: 's',
      },
    ],
  }),
);

describe('accepted image projection', () => {
  it('updates the final result while preserving the original and local baseline', () => {
    syncAcceptedResult(session());
    expect(useStudioStore.getState().targetImages[0]).toMatchObject({
      url: '/original',
      processedUrl: '/local',
      refinedUrl: '/accepted/v1',
    });
    syncAcceptedResult(session('v2', 6));
    expect(useStudioStore.getState().targetImages[0].refinedUrl).toBe('/accepted/v2');
    syncAcceptedResult(session('v0', 7));
    expect(useStudioStore.getState().targetImages[0].refinedUrl).toBe('/accepted/v0');
  });
  it('preserves a frozen same-version copy and rejects older responses after further discussion', () => {
    syncAcceptedResult(session());
    useStudioStore.getState().updateTargetImageById('t', { refinedUrl: 'data:image/png;base64,frozen' });
    syncAcceptedResult(session('v1', 10));
    syncAcceptedResult(session('v2', 8));
    expect(useStudioStore.getState().targetImages[0].refinedUrl).toBe('data:image/png;base64,frozen');
    expect(useStudioStore.getState().targetImages[0].acceptedRefinement?.revision).toBe(10);
  });
  it('does not sync unaccepted candidates, unrelated history, or a target being reprocessed', () => {
    syncAcceptedResult({ ...session(), accepted_version_id: null, accepted_image_url: null });
    syncAcceptedResult({ ...session(), id: 'other' });
    syncAcceptedResult({ ...session(), target_id: 'other' });
    useStudioStore.getState().updateTargetImageById('t', { status: 'PROCESSING' });
    syncAcceptedResult(session());
    expect(useStudioStore.getState().targetImages[0].refinedUrl).toBeUndefined();
  });
});
