import type { SourceReference } from '../types';

// The only place a managed portrait asset's image src is derived from its opaque ID.
export const portraitAssetThumbnailUrl = (assetId: string) => `/api/v1/portrait-assets/${assetId}/thumbnail`;
export const portraitAssetContentUrl = (assetId: string) => `/api/v1/portrait-assets/${assetId}/content`;

export const sourceReferenceImageUrl = (reference: SourceReference): string =>
  reference.kind === 'managed' ? portraitAssetThumbnailUrl(reference.assetId) : reference.imageData;

export const splitSourceReferences = (references: SourceReference[]) => ({
  source_asset_ids: references.filter((r): r is Extract<SourceReference, { kind: 'managed' }> => r.kind === 'managed').map((r) => r.assetId),
  source_manual_images: references
    .filter((r): r is Extract<SourceReference, { kind: 'manual' }> => r.kind === 'manual')
    .map((r) => r.imageData),
});
