import { describe, expect, it } from 'vitest';

import {
  comparisonPositionFromPointer,
  FACE_MARKER_PADDING,
  faceBoxToPercentage,
  fitImageWithinViewport,
} from './imageViewport';

describe('image viewport coordinates', () => {
  it('fits the complete image to the limiting stage dimension without distortion', () => {
    const fitted = fitImageWithinViewport(5293, 3529, 1578, 1002);
    expect(fitted?.width).toBeCloseTo(1502.86, 2);
    expect(fitted?.height).toBeCloseTo(1002, 5);
    expect(fitImageWithinViewport(800, 600, 1600, 1200)).toEqual({ width: 800, height: 600 });
    expect(fitImageWithinViewport(0, 600, 1600, 1200)).toBeNull();
  });

  it('maps comparison dragging against the image bounds rather than the outer stage', () => {
    expect(comparisonPositionFromPointer(300, 200, 400)).toBe(25);
    expect(comparisonPositionFromPointer(100, 200, 400)).toBe(5);
    expect(comparisonPositionFromPointer(700, 200, 400)).toBe(95);
    expect(comparisonPositionFromPointer(300, 200, 0)).toBeNull();
  });

  it('maps and clips detector boxes to the source-image canvas', () => {
    expect(faceBoxToPercentage([100, 50, 500, 250], 1000, 500)).toEqual({
      left: 10,
      top: 10,
      width: 40,
      height: 40,
    });
    expect(faceBoxToPercentage([-20, -10, 1100, 600], 1000, 500)).toEqual({
      left: 0,
      top: 0,
      width: 100,
      height: 100,
    });
  });

  it('expands only the displayed face marker with extra room below the detector ROI', () => {
    const box = faceBoxToPercentage([100, 50, 500, 250], 1000, 500, FACE_MARKER_PADDING);

    expect(box?.left).toBeCloseTo(6.8);
    expect(box?.top).toBeCloseTo(6);
    expect(box?.width).toBeCloseTo(46.4);
    expect(box?.height).toBeCloseTo(52.8);
  });
});
