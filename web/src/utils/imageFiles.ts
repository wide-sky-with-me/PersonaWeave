export const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
const MAX_IMAGE_PIXELS = 40_000_000;
const SUPPORTED_IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);
export interface ValidatedImageFile {
  file: File;
  width: number;
  height: number;
}
export async function validateImageFile(file: File): Promise<ValidatedImageFile> {
  if (!SUPPORTED_IMAGE_TYPES.has(file.type)) throw new Error(`${file.name} 不是支持的 JPEG、PNG 或 WebP 图片`);
  if (file.size > MAX_IMAGE_BYTES) throw new Error(`${file.name} 超过 20 MiB`);
  const dimensions = await probeImageDimensions(file);
  if (dimensions.width * dimensions.height > MAX_IMAGE_PIXELS) throw new Error(`${file.name} 超过 4000 万像素`);
  return { file, ...dimensions };
}
async function probeImageDimensions(file: File): Promise<{ width: number; height: number }> {
  if (typeof createImageBitmap === 'function') {
    const bitmap = await createImageBitmap(file);
    try {
      return { width: bitmap.width, height: bitmap.height };
    } finally {
      bitmap.close();
    }
  }
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve({ width: image.naturalWidth, height: image.naturalHeight });
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error(`${file.name} 无法读取或图片已损坏`));
    };
    image.src = url;
  });
}
export async function validateImageFiles(
  files: Iterable<File>,
): Promise<{ accepted: ValidatedImageFile[]; errors: string[] }> {
  const accepted: ValidatedImageFile[] = [];
  const errors: string[] = [];
  // Dimension probing may decode the full bitmap; release each one before opening the next file.
  for (const file of files) {
    try {
      accepted.push(await validateImageFile(file));
    } catch (error) {
      errors.push(error instanceof Error ? error.message : String(error));
    }
  }
  return { accepted, errors };
}
