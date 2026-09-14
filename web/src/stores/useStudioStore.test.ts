import { beforeEach, describe, expect, it } from 'vitest';

import { useStudioStore } from './useStudioStore';
import type { TargetImageItem } from '@/types';

const target = (id: string): TargetImageItem => ({
  id,
  name: `${id}.jpg`,
  url: `/${id}.jpg`,
  status: 'IDLE',
});

describe('stable target result ownership', () => {
  beforeEach(() => {
    useStudioStore.setState({ targetImages: [target('a'), target('b')], activeTargetIndex: 0 });
  });

  it('keeps a stale quick response on its submitted target after switching and removing targets', () => {
    const submittedId = useStudioStore.getState().targetImages[0].id;
    useStudioStore.getState().setActiveTargetIndex(1);
    useStudioStore.getState().removeTargetImage(0);
    useStudioStore.getState().updateTargetImageById(submittedId, { processedUrl: '/stale.jpg' });

    expect(useStudioStore.getState().targetImages).toEqual([expect.objectContaining({ id: 'b' })]);
    expect(useStudioStore.getState().targetImages[0]).not.toHaveProperty('processedUrl');
  });

  it('maps batch results using the immutable submitted id order and ignores removed targets', () => {
    const submittedIds = useStudioStore.getState().targetImages.map(({ id }) => id);
    useStudioStore.getState().removeTargetImage(0);
    useStudioStore.getState().batchUpdateTargetImages([
      { id: submittedIds[0], patch: { processedUrl: '/a-result.jpg' } },
      { id: submittedIds[1], patch: { processedUrl: '/b-result.jpg' } },
    ]);

    expect(useStudioStore.getState().targetImages).toEqual([
      expect.objectContaining({ id: 'b', processedUrl: '/b-result.jpg' }),
    ]);
  });

  it('clears stale refinement state when a new swap is submitted and completed', () => {
    useStudioStore.setState({
      targetImages: [
        {
          ...target('a'),
          processedUrl: '/old-swap.jpg',
          refinedUrl: '/accepted-refinement.jpg',
          refinementSessionId: 'old-session',
        },
      ],
    });

    useStudioStore.getState().updateTargetImageById('a', {
      status: 'PROCESSING',
      refinedUrl: undefined,
      refinementSessionId: undefined,
    });
    useStudioStore.getState().updateTargetImageById('a', { processedUrl: '/new-swap.jpg', status: 'SUCCESS' });

    expect(useStudioStore.getState().targetImages[0]).toMatchObject({ processedUrl: '/new-swap.jpg' });
    expect(useStudioStore.getState().targetImages[0].refinedUrl).toBeUndefined();
    expect(useStudioStore.getState().targetImages[0].refinementSessionId).toBeUndefined();
  });

  it('invalidates mappings only when a face topology patch is applied', () => {
    useStudioStore.setState({
      personMappings: [
        {
          person_index: 1,
          target_reference_url: '/a.jpg',
          target_reference_person_index: 1,
          source_asset_ids: [],
          source_manual_images: [],
        },
      ],
    });
    useStudioStore.getState().updateTargetImageById('a', { status: 'PROCESSING' });
    expect(useStudioStore.getState().personMappings).toHaveLength(1);

    useStudioStore
      .getState()
      .updateTargetImageById('a', { faces: [{ index: 1, global_person_id: 9, bbox: [0, 0, 1, 1] }] });
    expect(useStudioStore.getState().personMappings).toEqual([]);
  });
});

describe('manual protection masks', () => {
  beforeEach(() => {
    useStudioStore.setState({ targetImages: [target('a'), target('b')], activeTargetIndex: 0 });
  });

  it('isolates masks and preserves them while switching targets', () => {
    useStudioStore.getState().setManualMaskDataUrl('mask-a');
    useStudioStore.getState().setActiveTargetIndex(1);
    expect(useStudioStore.getState().targetImages[1].manualMask).toBeUndefined();
    useStudioStore.getState().setManualMaskDataUrl('mask-b');
    useStudioStore.getState().setActiveTargetIndex(0);
    expect(useStudioStore.getState().targetImages[0].manualMask?.dataUrl).toBe('mask-a');
    expect(useStudioStore.getState().targetImages[1].manualMask?.dataUrl).toBe('mask-b');
  });

  it('undoes, redoes, and records clear as an edit', () => {
    useStudioStore.getState().setManualMaskDataUrl('one');
    useStudioStore.getState().setManualMaskDataUrl('two');
    useStudioStore.getState().setManualMaskDataUrl(null);
    useStudioStore.getState().undoManualMask();
    expect(useStudioStore.getState().targetImages[0].manualMask?.dataUrl).toBe('two');
    useStudioStore.getState().undoManualMask();
    expect(useStudioStore.getState().targetImages[0].manualMask?.dataUrl).toBe('one');
    useStudioStore.getState().redoManualMask();
    expect(useStudioStore.getState().targetImages[0].manualMask?.dataUrl).toBe('two');
  });

  it('bounds history and releases state with removed or cleared targets', () => {
    for (let i = 0; i < 25; i += 1) useStudioStore.getState().setManualMaskDataUrl(`mask-${i}`);
    expect(useStudioStore.getState().targetImages[0].manualMask?.undoStack).toHaveLength(20);
    useStudioStore.getState().removeTargetImage(0);
    expect(useStudioStore.getState().targetImages.map((item) => item.id)).toEqual(['b']);
    useStudioStore.getState().clearTargetImages();
    expect(useStudioStore.getState().targetImages).toEqual([]);
  });
});
