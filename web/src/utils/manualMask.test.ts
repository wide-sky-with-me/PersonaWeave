import { describe, expect, it } from 'vitest';
import { createManualMaskPixels } from './manualMask';

describe('createManualMaskPixels', () => {
  it('creates an opaque grayscale mask from painted alpha', () => {
    const source = new Uint8ClampedArray([255, 160, 0, 0, 255, 160, 0, 11]);
    const result = createManualMaskPixels(source, 10);

    expect(result.hasPainted).toBe(true);
    expect([...result.pixels]).toEqual([0, 0, 0, 255, 255, 255, 255, 255]);
  });

  it('treats alpha equal to the threshold as unpainted', () => {
    const result = createManualMaskPixels(new Uint8ClampedArray([1, 2, 3, 10]), 10);
    expect(result.hasPainted).toBe(false);
    expect([...result.pixels]).toEqual([0, 0, 0, 255]);
  });
});
