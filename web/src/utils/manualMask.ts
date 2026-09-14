export interface ManualMaskResult {
  pixels: Uint8ClampedArray;
  hasPainted: boolean;
}

/** Converts the transparent amber brush layer into an opaque grayscale protection mask. */
export function createManualMaskPixels(source: Uint8ClampedArray, alphaThreshold = 10): ManualMaskResult {
  const pixels = new Uint8ClampedArray(source.length);
  let hasPainted = false;

  for (let i = 0; i < source.length; i += 4) {
    const painted = source[i + 3] > alphaThreshold;
    const value = painted ? 255 : 0;
    pixels[i] = value;
    pixels[i + 1] = value;
    pixels[i + 2] = value;
    pixels[i + 3] = 255;
    hasPainted ||= painted;
  }

  return { pixels, hasPainted };
}
