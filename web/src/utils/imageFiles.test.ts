import { describe, expect, it } from 'vitest';
import { MAX_IMAGE_BYTES, validateImageFile, validateImageFiles } from './imageFiles';

describe('image file validation', () => {
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
