import { describe, expect, it, vi } from 'vitest';
import { freezeDocument, studioSnapshot, type ProjectDocument } from './projectSnapshot';

function fixture(): ProjectDocument {
  return {
    schema: 1,
    studio: {
      ...studioSnapshot(),
      sourcePeople: [{ id: 7, name: '角色七' }],
      sourceGallery: [
        {
          id: 'asset',
          personId: 7,
          kind: 'managed',
          assetId: 'original-asset',
          name: '原尺寸参考',
          facePreview: '/api/v1/portrait-assets/original-asset/thumbnail',
        },
      ],
      targetImages: [
        {
          id: 'target',
          name: '画布',
          url: 'data:image/png;base64,canvas',
          status: 'SUCCESS',
          pendingRefinement: {
            taskId: 'running-batch',
            input: {
              original_image_url: 'data:image/png;base64,canvas',
              baseline_image_url: '',
              people: [
                {
                  person_id: 9,
                  face_index: 2,
                  person_locator: 'Frozen position',
                  source_asset_ids: ['original-asset'],
                },
              ],
            },
          },
          faces: [{ index: 2, global_person_id: 9, bbox: [1, 2, 3, 4] }],
          manualMask: {
            dataUrl: 'data:image/png;base64,current',
            undoStack: [null, 'data:image/png;base64,undo'],
            redoStack: ['data:image/png;base64,redo'],
          },
        },
      ],
      personMappings: [
        {
          person_index: 9,
          target_reference_person_index: 2,
          target_reference_url: 'data:image/png;base64,canvas',
          source_asset_ids: ['original-asset'],
          source_manual_images: [],
          source_mode: 'gallery',
        },
      ],
    },
    workbench: {
      view: 'refinement',
      sessionId: 'session',
      targetId: 'target',
      selections: {
        session: {
          viewVersionId: 'v2',
          baseVersionId: 'v1',
          confirmedDiagnosisTurnId: 'diagnosis',
          message: '未提交意见',
          zoom: 2,
        },
      },
    },
    warnings: [],
  };
}

describe('complete project snapshot', () => {
  it('freezes original managed references and every brush-history frame while preserving stable mappings and draft choices', async () => {
    const original = fixture();
    const upload = vi.fn(async (source) => [{ $asset: source.asset_id ?? source.image }]);
    const frozen = (await freezeDocument(original, upload)) as any;
    expect(upload).toHaveBeenCalledWith({ asset_id: 'original-asset' });
    expect(upload).toHaveBeenCalledWith({ image: '/api/v1/portrait-assets/original-asset/thumbnail' });
    expect(frozen.studio.sourceGallery[0]).toMatchObject({
      kind: 'manual',
      personId: 7,
      imageData: { $asset: 'original-asset' },
      facePreview: { $asset: '/api/v1/portrait-assets/original-asset/thumbnail' },
    });
    expect(frozen.studio.personMappings[0]).toMatchObject({
      person_index: 9,
      target_reference_person_index: 2,
      source_mode: 'gallery',
      source_asset_ids: [],
      source_manual_images: [{ $asset: 'original-asset' }],
    });
    expect(frozen.studio.targetImages[0].manualMask).toEqual({
      dataUrl: { $asset: 'data:image/png;base64,current' },
      undoStack: [null, { $asset: 'data:image/png;base64,undo' }],
      redoStack: [{ $asset: 'data:image/png;base64,redo' }],
    });
    expect(frozen.workbench).toEqual(original.workbench);
    expect(frozen.studio.targetImages[0].faces).toEqual(original.studio.targetImages[0].faces);
    expect(frozen.studio.targetImages[0].pendingRefinement).toMatchObject({
      taskId: 'running-batch',
      input: {
        baseline_image_url: '',
        people: [{ person_id: 9, face_index: 2, source_manual_images: [{ $asset: 'original-asset' }] }],
      },
    });
    expect(original.studio.sourceGallery[0].kind).toBe('managed');
  });
  it('does not save task controls, callbacks, health or runtime functions', () => {
    const snapshot = studioSnapshot();
    expect(snapshot).not.toHaveProperty('activeTaskId');
    expect(snapshot).not.toHaveProperty('isProcessing');
    expect(snapshot).not.toHaveProperty('setIsProcessing');
    expect(snapshot).not.toHaveProperty('serverHealth');
  });
  it('rejects a required missing image instead of silently dropping it', async () => {
    const upload = vi.fn().mockRejectedValue(new Error('unavailable'));
    await expect(freezeDocument(fixture(), upload)).rejects.toThrow('unavailable');
  });
});
