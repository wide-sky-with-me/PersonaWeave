import type { DetectedFaceBox, RefinementDraftState } from '../types';

/** Creates first-open refinement selections from the people that were actually swapped. */
export function buildInitialRefinementDrafts(
  faces: DetectedFaceBox[],
  swappedPersonIds: number[],
  selectedPersonId: number | null,
): Record<number, RefinementDraftState> {
  const fallbackIds = selectedPersonId === null ? [] : [selectedPersonId];
  const selectedIds = new Set(swappedPersonIds.length > 0 ? swappedPersonIds : fallbackIds);

  return Object.fromEntries(
    faces.map((face) => {
      const personId = face.global_person_id ?? face.index;
      return [personId, { selected: selectedIds.has(personId), instructions: '' }];
    }),
  );
}
