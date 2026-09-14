import type { PersonMappingItem, TargetImageItem } from '@/types';
import type { ReferenceRecipe, SessionInput } from './sessionTypes';

/** Freeze the executed mapping, not whichever library selection is visible later. */
export function snapshotRefinementInput(
  target: TargetImageItem,
  mappings: PersonMappingItem[],
  baseline: string,
  original?: string,
): SessionInput {
  const people: ReferenceRecipe[] = (target.faces ?? []).map((face) => {
    const personId = face.global_person_id ?? face.index;
    const mapping = mappings.find((item) => item.person_index === personId);
    const [x1, y1, x2, y2] = face.bbox;
    const width = target.imageWidth;
    const height = target.imageHeight;
    // Anchor IDs to the executed original-image detection, not a fresh candidate ordering.
    const position =
      width && height && width > 0 && height > 0 && face.bbox.every(Number.isFinite)
        ? '；脸框中心距原图左边 ' +
          (((x1 + x2) / 2 / width) * 100).toFixed(1) +
          '%、距上边 ' +
          (((y1 + y2) / 2 / height) * 100).toFixed(1) +
          '%'
        : '';
    return {
      person_id: personId,
      face_index: face.index,
      person_locator:
        '稳定人物 P' +
        personId +
        '：本次原图中从左至右第 ' +
        face.index +
        ' 张已检测人脸' +
        position +
        '。此对应来自执行快照，不按候选图重新排序。',
      ...(mapping?.source_identity_id && !mapping.source_asset_ids?.length && !mapping.source_manual_images?.length
        ? { source_identity_id: mapping.source_identity_id }
        : {}),
      ...(mapping?.source_asset_ids?.length ? { source_asset_ids: [...mapping.source_asset_ids] } : {}),
      ...(mapping?.source_manual_images?.length ? { source_manual_images: [...mapping.source_manual_images] } : {}),
    };
  });
  return { original_image_url: original || target.base64Data || target.url, baseline_image_url: baseline, people };
}

export function validateSessionInput(input: SessionInput): string | null {
  if (input.people.length > 24) return '单张图最多支持 24 位人物，请拆分任务；不会静默丢弃人物。';
  for (const person of input.people) {
    if (person.person_locator.length > 300)
      return 'P' + person.person_id + ' 的定位描述超过 300 字，请明确缩短后重试。';
    if ((person.source_asset_ids?.length ?? 0) + (person.source_manual_images?.length ?? 0) > 8)
      return 'P' + person.person_id + ' 的参考超过 8 张，请明确选择参考后重新换脸；不会静默删减来源。';
  }
  return null;
}
