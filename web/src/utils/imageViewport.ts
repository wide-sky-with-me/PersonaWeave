import type { DetectedFaceBox } from '@/types';

export interface PercentageBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface FaceBoxPadding {
  horizontal: number;
  top: number;
  bottom: number;
}

export interface ViewportSize {
  width: number;
  height: number;
}

export const FACE_MARKER_PADDING: FaceBoxPadding = {
  horizontal: 0.08,
  top: 0.1,
  bottom: 0.22,
};

const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value));

/** Fits the complete image inside the available stage while preserving its aspect ratio. */
export function fitImageWithinViewport(
  imageWidth: number,
  imageHeight: number,
  availableWidth: number,
  availableHeight: number,
): ViewportSize | null {
  if (imageWidth <= 0 || imageHeight <= 0 || availableWidth <= 0 || availableHeight <= 0) {
    return null;
  }

  const scale = Math.min(availableWidth / imageWidth, availableHeight / imageHeight, 1);
  return {
    width: imageWidth * scale,
    height: imageHeight * scale,
  };
}

/** Maps a horizontal pointer coordinate onto the rendered image viewport. */
export function comparisonPositionFromPointer(
  clientX: number,
  viewportLeft: number,
  viewportWidth: number,
  minimum = 5,
  maximum = 95,
): number | null {
  if (!Number.isFinite(viewportWidth) || viewportWidth <= 0) return null;
  const percentage = ((clientX - viewportLeft) / viewportWidth) * 100;
  return clamp(percentage, minimum, maximum);
}

/** Converts detector pixels into a box relative to the exact source-image canvas. */
export function faceBoxToPercentage(
  bbox: DetectedFaceBox['bbox'],
  imageWidth: number,
  imageHeight: number,
  padding?: FaceBoxPadding,
): PercentageBox | null {
  if (imageWidth <= 0 || imageHeight <= 0) return null;

  const boxWidth = Math.max(0, bbox[2] - bbox[0]);
  const boxHeight = Math.max(0, bbox[3] - bbox[1]);
  const horizontalPadding = boxWidth * (padding?.horizontal ?? 0);
  const topPadding = boxHeight * (padding?.top ?? 0);
  const bottomPadding = boxHeight * (padding?.bottom ?? 0);
  const x1 = clamp(bbox[0] - horizontalPadding, 0, imageWidth);
  const y1 = clamp(bbox[1] - topPadding, 0, imageHeight);
  const x2 = clamp(bbox[2] + horizontalPadding, x1, imageWidth);
  const y2 = clamp(bbox[3] + bottomPadding, y1, imageHeight);

  return {
    left: (x1 / imageWidth) * 100,
    top: (y1 / imageHeight) * 100,
    width: ((x2 - x1) / imageWidth) * 100,
    height: ((y2 - y1) / imageHeight) * 100,
  };
}
