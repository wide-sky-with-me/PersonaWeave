import type { DetectedFaceBox } from '../types';

export type FaceRelabelResult =
  | { ok: true; faces: DetectedFaceBox[] }
  | { ok: false; message: string };

type FaceRow = {
  centerY: number;
  faces: Array<{ face: DetectedFaceBox; centerX: number; centerY: number }>;
};

/**
 * Builds human-readable visual positions without changing the stable face indexes.
 *
 * Detection indexes stay globally left-to-right because the API uses them to select
 * the exact reference face. The row labels are display-only and make multi-row group
 * photos unambiguous to people reviewing the mapping.
 */
export function buildFacePositionLabels(faces: DetectedFaceBox[]): Map<number, string> {
  if (faces.length === 0) return new Map();

  const measured = faces.map((face) => ({
    face,
    centerX: (face.bbox[0] + face.bbox[2]) / 2,
    centerY: (face.bbox[1] + face.bbox[3]) / 2,
    height: Math.max(1, face.bbox[3] - face.bbox[1]),
  }));
  const heights = measured.map(({ height }) => height).sort((a, b) => a - b);
  const medianHeight = heights[Math.floor(heights.length / 2)];
  const rowBreakThreshold = medianHeight * 1.35;
  const rows: FaceRow[] = [];
  let previousCenterY: number | null = null;

  for (const candidate of measured.sort((a, b) => a.centerY - b.centerY)) {
    // A seated/front row often forms an arc: its center Y can drift by more than one
    // face height across the image. Split only at a significant gap between adjacent
    // faces instead of comparing every face with a row centroid, which fragmented the
    // real two-row group photo into three rows.
    if (previousCenterY === null || candidate.centerY - previousCenterY > rowBreakThreshold) {
      rows.push({ centerY: candidate.centerY, faces: [candidate] });
    } else {
      const currentRow = rows[rows.length - 1];
      currentRow.faces.push(candidate);
      currentRow.centerY =
        currentRow.faces.reduce((sum, item) => sum + item.centerY, 0) /
        currentRow.faces.length;
    }
    previousCenterY = candidate.centerY;
  }

  rows.sort((a, b) => a.centerY - b.centerY);
  const labels = new Map<number, string>();
  rows.forEach((row, rowIndex) => {
    const rowLabel =
      rows.length === 1
        ? ''
        : rows.length === 2
          ? rowIndex === 0
            ? '上排·'
            : '下排·'
          : `第 ${rowIndex + 1} 排·`;
    row.faces
      .sort((a, b) => a.centerX - b.centerX)
      .forEach(({ face }, columnIndex) => {
        labels.set(face.index, `${rowLabel}从左到右第 ${columnIndex + 1} 位`);
      });
  });

  return labels;
}

/** Relabels one face without creating two copies of the same target identity in an image. */
export function relabelFaceSafely(
  faces: DetectedFaceBox[],
  faceIndex: number,
  globalPersonId: number,
): FaceRelabelResult {
  const face = faces.find((candidate) => candidate.index === faceIndex);
  if (!face) return { ok: false, message: `未找到位置人脸 #${faceIndex}。` };

  const duplicate = faces.some(
    (candidate) => candidate.index !== faceIndex && candidate.global_person_id === globalPersonId,
  );
  if (duplicate) {
    return {
      ok: false,
      message: `人物 #${globalPersonId} 已在当前图片中。无法安全自动重标；请重新运行身份聚类，或手动解决冲突后再试。`,
    };
  }

  return {
    ok: true,
    faces: faces.map((candidate) =>
      candidate.index === faceIndex ? { ...candidate, global_person_id: globalPersonId } : candidate,
    ),
  };
}
