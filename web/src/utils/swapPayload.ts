import type {
  PersonMappingItem,
  SourcePerson,
  SourceReference,
  SwapParameters,
  TargetImageItem,
  TargetPersonId,
} from '@/types';
import { splitSourceReferences } from './sourceReferences';
export type MappingReady = { ready: true; mappings: PersonMappingItem[]; autoCreated: boolean };
export type MappingBlocked = { ready: false; message: string };
export type MappingPrerequisite = MappingReady | MappingBlocked;
export type QuickSwapBuildResult =
  | MappingBlocked
  | (MappingReady & {
      payload: {
        person_mappings: PersonMappingItem[];
        target_image_url: string;
        parameters: SwapParameters & { manual_mask?: string };
        return_base64: true;
      };
    });
export type BatchSwapBuildResult =
  | MappingBlocked
  | (MappingReady & {
      payload: {
        person_mappings: PersonMappingItem[];
        batch_images: { client_target_id: string; image_url: string; manual_mask?: string }[];
        parameters: SwapParameters;
      };
    });
export interface SwapIdentityContext {
  sourcePeople: SourcePerson[];
  sourceGallery: SourceReference[];
  targetImages: TargetImageItem[];
  personMappings: PersonMappingItem[];
}
function isPersonIndex(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
}
/** Validate restored/runtime data as well as typed callers; never coerce null into an identity. */
export function hasValidPersonIndices(mapping: PersonMappingItem) {
  return isPersonIndex(mapping.person_index) && isPersonIndex(mapping.target_reference_person_index);
}
function listTargetPeople(targetImages: TargetImageItem[]) {
  const people = new Map<
    TargetPersonId,
    { targetPersonId: TargetPersonId; targetReferenceUrl: string; targetReferencePersonIndex: number }
  >();
  for (const image of targetImages) {
    if (!image.faces) return [];
    for (const face of image.faces) {
      // detect-faces explicitly returns null. Grouping those nulls collapses a
      // multi-person photo into a false one-person automatic mapping.
      if (!isPersonIndex(face.global_person_id) || !isPersonIndex(face.index)) return [];
      if (people.has(face.global_person_id)) continue;
      people.set(face.global_person_id, {
        targetPersonId: face.global_person_id,
        targetReferenceUrl: image.base64Data || image.url,
        targetReferencePersonIndex: face.index,
      });
    }
  }
  return [...people.values()].sort((a, b) => a.targetPersonId - b.targetPersonId);
}
function mappingSource(person: SourcePerson, references: SourceReference[]) {
  const { source_asset_ids, source_manual_images } = splitSourceReferences(references);
  return {
    source_identity_id:
      source_asset_ids.length === 0 && source_manual_images.length === 0 ? person.libraryIdentityId : undefined,
    source_asset_ids,
    source_manual_images,
  };
}
function singlePair(context: SwapIdentityContext, sourceMode: SwapParameters['source_mode']): PersonMappingItem | null {
  const targets = listTargetPeople(context.targetImages);
  const sources = context.sourcePeople
    .map((person) => ({
      person,
      references: context.sourceGallery.filter((reference) => reference.personId === person.id),
    }))
    .filter(({ references }) => references.length > 0);
  if (targets.length !== 1 || sources.length !== 1) return null;
  const [target] = targets;
  const [{ person, references }] = sources;
  return {
    person_index: target.targetPersonId,
    target_reference_url: target.targetReferenceUrl,
    target_reference_person_index: target.targetReferencePersonIndex,
    ...mappingSource(person, references),
    source_mode: sourceMode,
    label: person.name,
  };
}
/** Resolves execution identities exclusively through explicit target-to-source mappings. */
export function resolveExecutionMappings(
  context: SwapIdentityContext,
  sourceMode: SwapParameters['source_mode'] = 'best-match',
): MappingPrerequisite {
  const applyMode = (mappings: PersonMappingItem[]) =>
    mappings.map((mapping) => ({ ...mapping, source_mode: sourceMode }));
  if (context.personMappings.some((mapping) => !hasValidPersonIndices(mapping)))
    return { ready: false, message: '人物映射编号无效，请重新绑定目标人物' };
  if (context.personMappings.length > 0)
    return { ready: true, mappings: applyMode(context.personMappings), autoCreated: false };
  const automatic = singlePair(context, sourceMode);
  return automatic ? { ready: true, mappings: [automatic], autoCreated: true } : { ready: false, message: '待准备' };
}
export function buildQuickSwapPayload(
  context: SwapIdentityContext,
  target: TargetImageItem,
  parameters: SwapParameters,
  manualMask?: string,
): QuickSwapBuildResult {
  const resolved = resolveExecutionMappings(context, parameters.source_mode);
  if (resolved.ready === false) return resolved;
  return {
    ...resolved,
    payload: {
      person_mappings: resolved.mappings,
      target_image_url: target.base64Data || target.url,
      parameters: { ...parameters, manual_mask: manualMask || undefined },
      return_base64: true,
    },
  };
}
export function buildBatchSwapPayload(context: SwapIdentityContext, parameters: SwapParameters): BatchSwapBuildResult {
  const resolved = resolveExecutionMappings(context, parameters.source_mode);
  if (resolved.ready === false) return resolved;
  return {
    ...resolved,
    payload: {
      person_mappings: resolved.mappings,
      batch_images: context.targetImages.map((target) => ({
        client_target_id: target.id,
        image_url: target.base64Data || target.url,
        manual_mask: target.manualMask?.dataUrl || undefined,
      })),
      parameters: { ...parameters, manual_mask: undefined },
    },
  };
}
