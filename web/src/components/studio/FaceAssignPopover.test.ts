import { describe, expect, it } from 'vitest';
import { SHEET_BREAKPOINT, computePlacement } from './FaceAssignPopover';

const viewport = { width: 1440, height: 900 };
const size = { width: 260, height: 340 };
const anchorAt = (left: number, top: number) => ({ left, top, bottom: top + 60, width: 60 });

describe('computePlacement', () => {
  it('opens below the face when there is room', () => {
    const placement = computePlacement(anchorAt(600, 100), size, viewport);
    expect(placement).toMatchObject({ sheet: false, top: 168 });
    expect(placement.left).toBe(600 + 30 - 130);
  });

  it('flips above the face when the space below is too small', () => {
    const placement = computePlacement(anchorAt(600, 700), size, viewport);
    expect(placement.top).toBe(700 - 8 - 340);
  });

  it('pins to the viewport when neither side fits and caps the height', () => {
    const tall = computePlacement(anchorAt(600, 400), { width: 260, height: 2000 }, viewport);
    expect(tall.maxHeight).toBe(900 - 16);
    expect(tall.top).toBeGreaterThanOrEqual(8);
    expect(tall.top + tall.maxHeight).toBeLessThanOrEqual(900 - 8);
  });

  it.each([
    ['left edge', 0],
    ['right edge', 1400],
  ])('clamps horizontally at the %s', (_name, left) => {
    const placement = computePlacement(anchorAt(left, 100), size, viewport);
    expect(placement.left).toBeGreaterThanOrEqual(8);
    expect(placement.left + size.width).toBeLessThanOrEqual(viewport.width - 8);
  });

  it('becomes a bottom sheet below the phone breakpoint', () => {
    const placement = computePlacement(anchorAt(10, 10), size, { width: SHEET_BREAKPOINT - 1, height: 800 });
    expect(placement).toEqual({ sheet: true, top: 0, left: 0, maxHeight: 600 });
  });
});
