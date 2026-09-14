import { describe, expect, it } from 'vitest';
import type { PersonMappingItem, TargetImageItem } from '@/types';
import { snapshotRefinementInput, validateSessionInput } from './sessionRecipe';
const target: TargetImageItem = {
  id: 't',
  name: 'group',
  url: '/original',
  refinedUrl: '/accepted-old',
  processedUrl: '/old',
  status: 'SUCCESS',
  faces: [{ index: 1, global_person_id: 2, bbox: [1, 2, 3, 4] }],
};
const recipe = (overrides: Partial<PersonMappingItem>): PersonMappingItem => ({
  person_index: 2,
  target_reference_url: '/original',
  target_reference_person_index: 1,
  source_asset_ids: [],
  source_manual_images: [],
  ...overrides,
});
describe('executed refinement reference snapshots', () => {
  it('anchors stable IDs to original detection coordinates rather than candidate face order', () => {
    const positioned = {
      ...target,
      imageWidth: 1000,
      imageHeight: 2000,
      faces: [{ index: 2, global_person_id: 7, bbox: [600, 800, 700, 1000] as [number, number, number, number] }],
    };
    const snapshot = snapshotRefinementInput(positioned, [], '/candidate');
    expect(snapshot.people[0]).toMatchObject({ person_id: 7, face_index: 2 });
    expect(snapshot.people[0].person_locator).toContain('原图中从左至右第 2 张');
    expect(snapshot.people[0].person_locator).toContain('左边 65.0%、距上边 45.0%');
    expect(snapshot.people[0].person_locator).toContain('不按候选图重新排序');
    positioned.faces[0].bbox = [0, 0, 1, 1];
    expect(snapshot.people[0].person_locator).toContain('65.0%');
  });
  it('uses explicit original-image order without inventing coordinates when dimensions are absent', () => {
    const locator = snapshotRefinementInput(target, [], '/candidate').people[0].person_locator;
    expect(locator).toContain('稳定人物 P2');
    expect(locator).toContain('原图中从左至右第 1 张');
    expect(locator).not.toContain('%');
  });

  it('freezes mixed explicit references, original and local baseline; never selects accepted pixels', () => {
    const mapping = recipe({
      source_identity_id: 'identity',
      source_asset_ids: ['asset'],
      source_manual_images: ['data:reference'],
    });
    const snapshot = snapshotRefinementInput(target, [mapping], '/new-baseline');
    mapping.source_asset_ids.push('later');
    mapping.source_manual_images[0] = 'changed';
    expect(snapshot.original_image_url).toBe('/original');
    expect(snapshot.baseline_image_url).toBe('/new-baseline');
    expect(snapshot.people[0]).toMatchObject({
      person_id: 2,
      face_index: 1,
      source_asset_ids: ['asset'],
      source_manual_images: ['data:reference'],
    });
    expect(snapshot.people[0]).not.toHaveProperty('source_identity_id');
    expect(validateSessionInput(snapshot)).toBeNull();
  });
  it('retains identity-only mode and does not invent a missing identity', () => {
    expect(
      snapshotRefinementInput(target, [recipe({ source_identity_id: 'identity' })], '/base').people[0]
        .source_identity_id,
    ).toBe('identity');
    const missing = snapshotRefinementInput(target, [], '/base');
    expect(missing.people[0]).not.toHaveProperty('source_identity_id');
    expect(missing.people[0]).not.toHaveProperty('source_manual_images');
  });
  it('rejects excess references, people and locator budgets without truncation', () => {
    const snapshot = snapshotRefinementInput(
      target,
      [recipe({ source_asset_ids: Array.from({ length: 9 }, (_, i) => String(i)) })],
      '/base',
    );
    expect(validateSessionInput(snapshot)).toContain('参考超过 8 张');
    expect(snapshot.people[0].source_asset_ids).toHaveLength(9);
    snapshot.people[0].source_asset_ids = [];
    snapshot.people[0].person_locator = 'x'.repeat(301);
    expect(validateSessionInput(snapshot)).toContain('超过 300 字');
    snapshot.people = Array.from({ length: 25 }, () => ({ ...snapshot.people[0], person_locator: 'left' }));
    expect(validateSessionInput(snapshot)).toContain('最多支持 24 位');
    expect(snapshot.people).toHaveLength(25);
  });
});
