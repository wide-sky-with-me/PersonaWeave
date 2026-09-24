import { describe, expect, it, vi } from 'vitest';
import { MAX_IMAGE_BYTES, validateImageFile, validateImageFiles } from './imageFiles';

describe('image file validation', () => {
  it('probes nine images sequentially and releases each decoded bitmap before opening the next', async () => {
    let active = 0;
    let peak = 0;
    const close = vi.fn(() => active--);
    vi.stubGlobal(
      'createImageBitmap',
      vi.fn(async () => {
        active++;
        peak = Math.max(peak, active);
        await Promise.resolve();
        return { width: 4000, height: 3000, close };
      }),
    );
    try {
      const files = Array.from({ length: 9 }, (_, i) => new File(['x'], `${i}.jpg`, { type: 'image/jpeg' }));
      const result = await validateImageFiles(files);
      expect(result.accepted).toHaveLength(9);
      expect(result.errors).toEqual([]);
      expect(peak).toBe(1);
      expect(active).toBe(0);
      expect(close).toHaveBeenCalledTimes(9);
    } finally {
      vi.unstubAllGlobals();
    }
  });
  it('rejects unsupported content types before decoding', async () => {
    const file = new File(['not an image'], 'notes.txt', { type: 'text/plain' });
    await expect(validateImageFile(file)).rejects.toThrow('JPEG、PNG 或 WebP');
  });

  it('rejects oversized files and reports independent batch errors', async () => {
    const oversized = new File([new Uint8Array(MAX_IMAGE_BYTES + 1)], 'large.jpg', { type: 'image/jpeg' });
    const unsupported = new File(['x'], 'vector.svg', { type: 'image/svg+xml' });
    const result = await validateImageFiles([oversized, unsupported]);
    expect(result.accepted).toEqual([]);
    expect(result.errors).toHaveLength(2);
    expect(result.errors.join(' ')).toContain('20 MiB');
  });
});
