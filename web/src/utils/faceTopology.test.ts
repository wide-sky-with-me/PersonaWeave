import { describe, expect, it } from 'vitest';

import type { DetectedFaceBox } from '@/types';
import { buildFacePositionLabels, relabelFaceSafely } from './faceTopology';

const faces: DetectedFaceBox[] = [
  { index: 1, global_person_id: 10, bbox: [0, 0, 10, 10] },
  { index: 2, global_person_id: 20, bbox: [10, 0, 20, 10] },
];

describe('relabelFaceSafely', () => {
  it('prevents duplicate global person IDs within one image', () => {
    const result = relabelFaceSafely(faces, 2, 10);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain('重新运行身份聚类');
  });

  it('allows a non-conflicting topology correction', () => {
    const result = relabelFaceSafely(faces, 2, 30);
    expect(result).toMatchObject({ ok: true, faces: [{ global_person_id: 10 }, { global_person_id: 30 }] });
  });
});

describe('buildFacePositionLabels', () => {
  it('labels a two-row group photo by row and then left-to-right within each row', () => {
    const labels = buildFacePositionLabels([
      { index: 1, bbox: [10, 10, 30, 30] },
      { index: 2, bbox: [20, 110, 40, 130] },
      { index: 3, bbox: [90, 12, 110, 32] },
      { index: 4, bbox: [100, 108, 120, 128] },
    ]);

    expect(Object.fromEntries(labels)).toEqual({
      1: '上排·从左到右第 1 位',
      2: '下排·从左到右第 1 位',
      3: '上排·从左到右第 2 位',
      4: '下排·从左到右第 2 位',
    });
  });

  it('keeps the concise left-to-right wording for a single row', () => {
    const labels = buildFacePositionLabels([
      { index: 8, bbox: [70, 10, 90, 30] },
      { index: 3, bbox: [10, 12, 30, 32] },
    ]);

    expect(labels.get(3)).toBe('从左到右第 1 位');
    expect(labels.get(8)).toBe('从左到右第 2 位');
  });

  it('keeps a curved seated row together when its face heights vary vertically', () => {
    const labels = buildFacePositionLabels([
      { index: 1, bbox: [0, 100, 200, 300] },
      { index: 4, bbox: [400, 80, 600, 280] },
      { index: 2, bbox: [20, 800, 220, 1000] },
      { index: 3, bbox: [300, 870, 500, 1070] },
      { index: 5, bbox: [600, 1030, 800, 1230] },
      { index: 6, bbox: [900, 1190, 1100, 1390] },
    ]);

    expect(Object.fromEntries(labels)).toEqual({
      1: '上排·从左到右第 1 位',
      4: '上排·从左到右第 2 位',
      2: '下排·从左到右第 1 位',
      3: '下排·从左到右第 2 位',
      5: '下排·从左到右第 3 位',
      6: '下排·从左到右第 4 位',
    });
  });
});
