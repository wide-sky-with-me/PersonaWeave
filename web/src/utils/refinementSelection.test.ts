import { describe, expect, it } from 'vitest';

import type { DetectedFaceBox } from '../types';
import { buildInitialRefinementDrafts } from './refinementSelection';

const faces: DetectedFaceBox[] = [
  { index: 1, global_person_id: 11, bbox: [0, 0, 10, 10] },
  { index: 2, global_person_id: 22, bbox: [10, 0, 20, 10] },
  { index: 3, global_person_id: 33, bbox: [20, 0, 30, 10] },
];

describe('buildInitialRefinementDrafts', () => {
  it('selects every actually swapped person and leaves untouched faces unselected', () => {
    const drafts = buildInitialRefinementDrafts(faces, [11, 33], null);

    expect(drafts[11].selected).toBe(true);
    expect(drafts[22].selected).toBe(false);
    expect(drafts[33].selected).toBe(true);
  });

  it('uses the manually selected person only for legacy results without swap IDs', () => {
    const drafts = buildInitialRefinementDrafts(faces, [], 22);

    expect(drafts[11].selected).toBe(false);
    expect(drafts[22].selected).toBe(true);
    expect(drafts[33].selected).toBe(false);
  });
});
