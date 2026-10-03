import { describe, expect, it } from 'vitest';
import { buildBatchSwapPayload, buildQuickSwapPayload, resolveExecutionMappings } from './swapPayload';
import type { SwapIdentityContext } from './swapPayload';
const parameters = {
  similarity_threshold: 0.35,
  identity_blend_strength: 0.72,
  enable_color_transfer: true,
  color_transfer_mode: 'boundary',
  enable_dense_mask: true,
  source_mode: 'best-match',
  swapper_model: 'auto',
  allow_research_models: false,
} as const;
const context = (): SwapIdentityContext => ({
  sourcePeople: [{ id: 7, name: 'Alice' }],
  sourceGallery: [{ id: 's', name: 'source', kind: 'manual', imageData: 'src', personId: 7 }],
  targetImages: [
    {
      id: 't',
      name: 'target',
      url: 'target',
      status: 'IDLE',
      faces: [{ index: 1, global_person_id: 42, bbox: [0, 0, 1, 1] }],
    },
  ],
  personMappings: [],
});
describe('swap payload identity semantics', () => {
  it('auto-creates one explicit mapping without numeric equality', () => {
    const result = resolveExecutionMappings(context());
    expect(result.ready).toBe(true);
    if (result.ready)
      expect(result.mappings[0]).toMatchObject({
        person_index: 42,
        source_manual_images: ['src'],
        target_reference_person_index: 1,
      });
  });
  it('requires confirmed mappings for multiple targets', () => {
    const value = context();
    value.targetImages[0].faces!.push({ index: 2, global_person_id: 99, bbox: [1, 1, 2, 2] });
    expect(resolveExecutionMappings(value)).toMatchObject({ ready: false });
  });
  it('uses identical mappings and correlated per-image masks for batch', () => {
    const value = context();
    value.targetImages[0].manualMask = { dataUrl: 'data:image/png;base64,bWFzaw==', undoStack: [], redoStack: [] };
    const resolved = resolveExecutionMappings(value);
    if (!resolved.ready) throw new Error();
    value.personMappings = resolved.mappings;
    const quick = buildQuickSwapPayload(value, value.targetImages[0], parameters);
    const batch = buildBatchSwapPayload(value, parameters);
    if (!quick.ready || !batch.ready) throw new Error();
    expect(quick.payload.person_mappings).toEqual(batch.payload.person_mappings);
    expect(batch.payload.batch_images).toEqual([
      { client_target_id: 't', image_url: 'target', manual_mask: 'data:image/png;base64,bWFzaw==' },
    ]);
    expect(batch.payload.parameters.identity_blend_strength).toBe(0.72);
    expect(batch.payload.parameters.manual_mask).toBeUndefined();
  });
  it('does not invent targets before clustering', () => {
    const value = context();
    value.targetImages[0].faces = [{ index: 1, bbox: [0, 0, 1, 1] }];
    expect(resolveExecutionMappings(value)).toMatchObject({ ready: false });
  });
  it('honors selected managed assets without expanding to the full identity', () => {
    const value = context();
    value.sourcePeople = [{ id: 7, name: 'Alice', libraryIdentityId: 'identity-1' }];
    value.sourceGallery = [{ id: 'a', name: 'selected', kind: 'managed', assetId: 'asset-a', personId: 7 }];
    const result = resolveExecutionMappings(value, 'gallery');
    expect(result.ready).toBe(true);
    if (result.ready)
      expect(result.mappings[0]).toMatchObject({
        source_identity_id: undefined,
        source_asset_ids: ['asset-a'],
        source_mode: 'gallery',
      });
  });
  it('applies the global source mode to confirmed mappings', () => {
    const value = context();
    const resolved = resolveExecutionMappings(value);
    if (!resolved.ready) throw new Error();
    value.personMappings = resolved.mappings;
    const result = resolveExecutionMappings(value, 'gallery');
    expect(result.ready).toBe(true);
    if (result.ready) expect(result.mappings[0].source_mode).toBe('gallery');
  });
});

describe('nullable IDs returned by single-image detection', () => {
  it.each([1, 3])('does not collapse %i unclustered faces into an automatic target', (count) => {
    const value = context();
    value.targetImages[0].faces = Array.from({ length: count }, (_, i) => ({
      index: i + 1,
      global_person_id: null,
      bbox: [0, 0, 10, 10] as [number, number, number, number],
    }));
    expect(buildQuickSwapPayload(value, value.targetImages[0], parameters).ready).toBe(false);
    expect(buildBatchSwapPayload(value, parameters).ready).toBe(false);
  });
  it('requires confirmation when only some faces have stable identities', () => {
    const value = context();
    value.targetImages[0].faces!.push({ index: 2, global_person_id: null, bbox: [0, 0, 10, 10] });
    expect(resolveExecutionMappings(value).ready).toBe(false);
  });
  it('does not treat unanalyzed images as having no people', () => {
    const value = context();
    value.targetImages.push({ id: 'new', name: 'new', url: 'new', status: 'IDLE' });
    expect(resolveExecutionMappings(value).ready).toBe(false);
  });
  it.each([null, undefined, NaN, 0, -1, 1.5, '1'])('blocks a restored invalid mapping ID: %s', (id) => {
    const value = context();
    const resolved = resolveExecutionMappings(value);
    if (!resolved.ready) throw new Error();
    value.personMappings = [{ ...resolved.mappings[0], person_index: id as number }];
    expect(buildQuickSwapPayload(value, value.targetImages[0], parameters)).toMatchObject({ ready: false });
    expect(buildBatchSwapPayload(value, parameters)).toMatchObject({ ready: false });
  });
  it('accepts an explicitly chosen local face in a three-person single image', () => {
    const value = context();
    const resolved = resolveExecutionMappings(value);
    if (!resolved.ready) throw new Error();
    value.targetImages[0].faces = [1, 2, 3].map((index) => ({ index, global_person_id: null, bbox: [0, 0, 10, 10] }));
    value.personMappings = [{ ...resolved.mappings[0], person_index: 2, target_reference_person_index: 2 }];
    const built = buildQuickSwapPayload(value, value.targetImages[0], parameters);
    expect(built.ready).toBe(true);
    if (built.ready) expect(built.payload.person_mappings[0].person_index).toBe(2);
  });
});
