import type { UploadFile } from 'antd'

export const IMAGE_FILE_ACCEPT = '.jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp'

const supportedExtensions = new Set(['jpg', 'jpeg', 'png', 'webp'])
const supportedMediaTypes = new Set(['image/jpeg', 'image/png', 'image/webp'])

export function isSupportedImageFile(file: File): boolean {
  const extension = file.name.split('.').pop()?.toLowerCase() ?? ''
  return supportedExtensions.has(extension) || supportedMediaTypes.has(file.type.toLowerCase())
}

export function fileFromUpload(item?: UploadFile): File | undefined {
  if (!item) return undefined
  const candidate = item.originFileObj ?? (item as unknown as File)
  return candidate instanceof File ? candidate : undefined
}
