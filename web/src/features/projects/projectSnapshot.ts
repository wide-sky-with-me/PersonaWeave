import { apiClient } from '@/services/api';
import { useStudioStore } from '@/stores/useStudioStore';
import { useWorkbenchStore } from '@/features/refinement/workbenchStore';

// Runtime/task facts and functions are deliberately excluded from saved editor input.
export const studioKeys = [
  'sourcePeople',
  'activeSourcePersonId',
  'sourceGallery',
  'targetImages',
  'activeTargetIndex',
  'selectedTargetPersonId',
  'parameters',
  'viewMode',
  'splitPosition',
  'showFaceMarkers',
  'brushSize',
  'isEraser',
  'personMappings',
] as const;
export type StudioSnapshot = Pick<ReturnType<typeof useStudioStore.getState>, (typeof studioKeys)[number]>;
export interface ProjectDocument {
  schema: 1;
  studio: StudioSnapshot;
  workbench: Pick<ReturnType<typeof useWorkbenchStore.getState>, 'view' | 'sessionId' | 'targetId' | 'selections'>;
  warnings: string[];
}
export interface ProjectDetail {
  id: string;
  title: string;
  revision: number;
  updated_at: string;
  document: ProjectDocument;
}
export type ProjectSummary = Omit<ProjectDetail, 'document'>;
export function studioSnapshot(): StudioSnapshot {
  const state = useStudioStore.getState();
  return Object.fromEntries(studioKeys.map((key) => [key, state[key]])) as unknown as StudioSnapshot;
}
export function sameStudio(
  a: ReturnType<typeof useStudioStore.getState>,
  b: ReturnType<typeof useStudioStore.getState>,
) {
  return studioKeys.every((key) => a[key] === b[key]);
}

const imageKeys = new Set([
  'url',
  'imageData',
  'facePreview',
  'base64Data',
  'processedUrl',
  'processedBase64',
  'refinedUrl',
  'target_reference_url',
  'original_image_url',
  'baseline_image_url',
  'source_manual_images',
  'dataUrl',
  'undoStack',
  'redoStack',
  'manual_mask',
]);
export type Asset = { $asset: string };
export type Upload = (source: { image?: string; asset_id?: string; identity_id?: string }) => Promise<Asset[]>;

/** Freeze every image occurrence, including brush history and original managed portrait bytes. */
export async function freezeDocument(document: ProjectDocument, upload: Upload): Promise<unknown> {
  async function walk(value: any, key = ''): Promise<any> {
    if (typeof value === 'string' && value && imageKeys.has(key)) {
      let image = value;
      if (image.startsWith('blob:')) image = await blobDataUrl(await (await fetch(image)).blob());
      // Some older mask fields contain raw base64 rather than a data URI.
      if (key === 'manual_mask' && !image.startsWith('data:')) image = 'data:image/png;base64,' + image;
      return (await upload({ image }))[0];
    }
    if (Array.isArray(value)) {
      const items = [];
      for (const item of value) items.push(await walk(item, key));
      return items;
    }
    if (value && typeof value === 'object') {
      const copy = { ...value };
      if (copy.kind === 'managed' && copy.assetId) {
        copy.imageData = (await upload({ asset_id: copy.assetId }))[0];
        copy.kind = 'manual';
        delete copy.assetId;
      }
      if (copy.source_identity_id || copy.source_asset_ids?.length) {
        const images: Asset[] = [];
        if (copy.source_identity_id) images.push(...(await upload({ identity_id: copy.source_identity_id })));
        for (const id of copy.source_asset_ids ?? []) images.push(...(await upload({ asset_id: id })));
        copy.source_manual_images = [...(copy.source_manual_images ?? []), ...images];
        copy.source_asset_ids = [];
        delete copy.source_identity_id;
      }
      const entries = [];
      for (const [name, item] of Object.entries(copy))
        if (item !== undefined) entries.push([name, await walk(item, name)]);
      return Object.fromEntries(entries);
    }
    return value;
  }
  return walk(document);
}

export function blobDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error('图片读取失败'));
    reader.readAsDataURL(blob);
  });
}

/** Hydrate verified media before touching either store; a missing image leaves the current editor intact. */
export async function hydrateDocument(project: ProjectDetail): Promise<ProjectDocument> {
  const cache = new Map<string, Promise<string>>();
  async function walk(value: any, key = ''): Promise<any> {
    if (value && typeof value === 'object' && '$asset' in value) {
      const digest = value.$asset;
      if (typeof digest !== 'string' || !/^[a-f0-9]{64}$/.test(digest)) throw new Error('工程图片引用无效');
      const inlineMask = ['dataUrl', 'undoStack', 'redoStack', 'manual_mask'].includes(key);
      const cacheKey = digest + (inlineMask ? ':mask' : ':image');
      if (!cache.has(cacheKey))
        cache.set(
          cacheKey,
          (async () => {
            const response = await apiClient.get(`/api/v1/projects/${project.id}/assets/${digest}`, {
              responseType: 'arraybuffer',
            });
            const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', response.data))]
              .map((n) => n.toString(16).padStart(2, '0'))
              .join('');
            if (hash !== digest) throw new Error('工程图片校验失败');
            // Keep photos on disk after verification. Masks retain their existing base64 API contract.
            if (!inlineMask) return `/api/v1/files/projects/${project.id}/${digest}`;
            return blobDataUrl(
              new Blob([response.data], { type: String(response.headers['content-type'] || 'image/png') }),
            );
          })(),
        );
      return cache.get(cacheKey);
    }
    if (Array.isArray(value)) {
      const items = [];
      for (const item of value) items.push(await walk(item, key));
      return items;
    }
    if (value && typeof value === 'object') {
      const result: Record<string, unknown> = {};
      for (const [key, item] of Object.entries(value)) {
        if (['__proto__', 'constructor', 'prototype'].includes(key)) throw new Error('工程字段无效');
        result[key] = await walk(item, key);
      }
      return result;
    }
    return value;
  }
  const document = (await walk(project.document)) as ProjectDocument;
  if (
    document.schema !== 1 ||
    !document.studio ||
    !document.workbench ||
    !Array.isArray(document.studio.targetImages) ||
    !Array.isArray(document.studio.sourceGallery) ||
    !Array.isArray(document.studio.sourcePeople) ||
    !Array.isArray(document.studio.personMappings)
  )
    throw new Error('工程格式不受支持');
  return document;
}

/** Replace only saved inline photographs, leaving masks and newer edits untouched by the caller. */
export function releaseSavedPhotos(original: StudioSnapshot, frozen: any, projectId: string): StudioSnapshot {
  function walk(value: any, saved: any, key = ''): any {
    if (
      typeof value === 'string' &&
      value.startsWith('data:image/') &&
      imageKeys.has(key) &&
      !['dataUrl', 'undoStack', 'redoStack', 'manual_mask'].includes(key) &&
      typeof saved?.$asset === 'string' &&
      /^[a-f0-9]{64}$/.test(saved.$asset)
    ) {
      return `/api/v1/files/projects/${projectId}/${saved.$asset}`;
    }
    if (Array.isArray(value)) {
      const items = value.map((item, index) => walk(item, saved?.[index], key));
      return items.every((item, index) => item === value[index]) ? value : items;
    }
    if (value && typeof value === 'object') {
      const entries = Object.entries(value).map(([name, item]) => [name, walk(item, saved?.[name], name)] as const);
      return entries.every(([name, item]) => item === value[name]) ? value : Object.fromEntries(entries);
    }
    return value;
  }
  return walk(original, frozen);
}
