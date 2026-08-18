import type {
  Batch,
  BatchProgress,
  BatchSummary,
  Health,
  LibraryAsset,
  LibraryItem,
  PreviewReport,
  PreviewResult,
  RecycleItem,
} from './types'

const API_BASE = '/api/v1'

function errorMessage(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== 'object') return fallback
  const detail = (payload as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string') return message
  }
  return fallback
}

export async function apiJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, options)
  const payload = await response.json().catch(() => null)
  if (!response.ok) throw new Error(errorMessage(payload, `请求失败（${response.status}）`))
  return payload as T
}

export function postJson<T>(path: string, body?: unknown): Promise<T> {
  return apiJson<T>(path, {
    method: 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export async function upload<T>(path: string, field: string, file: File): Promise<T> {
  const form = new FormData()
  form.append(field, file)
  return apiJson<T>(path, { method: 'POST', body: form })
}

export async function createLibraryMaterial(label: string, file: File): Promise<LibraryItem> {
  const form = new FormData()
  form.append('label', label)
  form.append('image', file)
  return apiJson<LibraryItem>('/material-library', { method: 'POST', body: form })
}

export async function addLibraryMaterialImage(itemId: string, file: File): Promise<LibraryAsset> {
  return upload<LibraryAsset>(`/material-library/${itemId}/images`, 'image', file)
}

function decodeReport(value: string | null): PreviewReport | null {
  if (!value) return null
  try {
    const normalized = value.replace(/-/g, '+').replace(/_/g, '/')
    const binary = atob(normalized)
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0))
    return JSON.parse(new TextDecoder().decode(bytes)) as PreviewReport
  } catch {
    return null
  }
}

async function imageResponse(response: Response): Promise<PreviewResult> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new Error(errorMessage(payload, `请求失败（${response.status}）`))
  }
  return {
    blob: await response.blob(),
    report: decodeReport(response.headers.get('X-PersonaWeave-Report')),
    needsReview: response.headers.get('X-PersonaWeave-Needs-Review') === 'true',
  }
}

export async function createPreview(input: {
  source: File
  reference?: File
  description: string
  maxIterations: number
}): Promise<PreviewResult> {
  const form = new FormData()
  form.append('source', input.source)
  if (input.reference) form.append('reference', input.reference)
  form.append('description', input.description)
  form.append('max_iterations', String(input.maxIterations))
  return imageResponse(await fetch(`${API_BASE}/previews/replace-all`, { method: 'POST', body: form }))
}

export async function createFacePlan(source: File): Promise<PreviewResult> {
  const form = new FormData()
  form.append('source', source)
  return imageResponse(await fetch(`${API_BASE}/previews/face-plan`, { method: 'POST', body: form }))
}

export const personaApi = {
  health: () => apiJson<Health>('/health'),
  batches: () => apiJson<BatchSummary[]>('/batches'),
  batch: (id: string) => apiJson<Batch>(`/batches/${id}`),
  progress: (id: string) => apiJson<BatchProgress>(`/batches/${id}/progress`),
  recycleBin: () => apiJson<{ items: RecycleItem[] }>('/recycle-bin'),
  materialLibrary: () => apiJson<LibraryItem[]>('/material-library'),
}
