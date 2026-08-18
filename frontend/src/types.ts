export type ProviderState = { configured: boolean }

export type Health = {
  status: string
  providers: Record<string, ProviderState>
}

export type BatchSummary = {
  id: string
  name: string
  status: string
  task_instruction: string
  max_iterations: number
  created_at: string
  updated_at: string
  deleted_at?: string | null
}

export type RecycleItem = {
  kind: 'batch' | 'legacy'
  id: string
  name: string
  status: string
  deleted_at: string
  restorable: boolean
  task_instruction?: string
  source_count?: number
  result_count?: number
  preview_urls?: string[]
}

export type LibraryAsset = {
  id: string
  library_item_id: string
  filename: string
  media_type: string
  sha256: string
  created_at: string
  content_url: string
}

export type LibraryItem = {
  id: string
  display_code: string
  label: string
  created_at: string
  updated_at: string
  assets: LibraryAsset[]
}

export type Asset = {
  id: string
  batch_id: string
  identity_id?: string | null
  parent_asset_id?: string | null
  kind: 'source' | 'reference' | 'result'
  display_code: string
  filename: string
  media_type: string
  sha256: string
  created_at: string
  content_url: string
}

export type Identity = {
  id: string
  batch_id: string
  display_code: string
  label: string
  created_at: string
  references: Asset[]
}

export type PersonMapping = {
  action: 'keep' | 'replace'
  target_identity_id?: string | null
  instruction?: string
}

export type FaceOccurrence = {
  id: string
  asset_id: string
  bbox: { x: number; y: number; width: number; height: number }
  detection_confidence: number
  identity_confidence?: number | null
  needs_confirmation: boolean | number
  appearance: {
    apparent_age?: string
    face_features?: string
    hair?: string
    eyewear?: string
    accessories?: string
    body?: string
    clothing?: string
    pose?: string
    lighting_occlusion?: string
  }
  crop_url: string
}

export type Person = {
  id: string
  batch_id: string
  cluster_key: string
  display_code: string
  label?: string | null
  confirmed: boolean | number
  faces: FaceOccurrence[]
  mapping?: PersonMapping | null
}

export type TargetProfile = {
  identity_id: string
  identity_code: string
  apparent_age: string
  face_features: string
  hair: string
  eyewear: string
  accessories: string
  makeup: string
  stable_identity_anchors: string[]
  variable_traits: string[]
  unknown_evidence: string[]
  unsupported_evidence: string[]
  reference_board_codes: string[]
  reference_board_digest: string
}

export type ReferenceSufficiency = {
  identity_id: string
  identity_code: string
  reference_count: number
  visible_views: string[]
  missing_views: string[]
  confidence: number
  sufficient: boolean
  requires_manual_review: boolean
  summary: string
}

export type CompiledOperation = {
  person_id: string
  person_code: string
  action: 'keep' | 'replace' | 'edit'
  target_identity_id?: string | null
  target_identity_code?: string | null
  requires_manual_review: boolean
  review_reasons: string[]
  permissions?: {
    editable: string[]
    protected: string[]
  } | null
  target_profile?: TargetProfile | null
  reference_assessment?: ReferenceSufficiency | null
  reference_assets: Array<{ code: string; sha256: string }>
  source_reference_coverage: Array<{
    source_asset_id: string
    source_asset_code: string
    person_id: string
    person_code: string
    sufficient: boolean
    requires_manual_review: boolean
    summary: string
    occurrences: Array<{
      face_instance_id: string
      bbox: { x: number; y: number; width: number; height: number }
      face_area_pixels: number
      pose: string
      lighting_occlusion_blur: string
    }>
  }>
}

export type CompiledSource = {
  asset_id: string
  code: string
  sha256: string
  editable_face_instance_ids: string[]
  requires_authorization_mask: boolean
  authorization_mask?: {
    sha256: string
    width: number
    height: number
    review_reasons: string[]
    status: string
    requires_manual_review: boolean
    preview_url?: string
  } | null
}

export type CompiledContract = {
  version: string
  max_iterations: number
  review_reasons: string[]
  requires_manual_review: boolean
  operations: CompiledOperation[]
  identities: Array<{
    profile?: TargetProfile | null
    reference_assessment?: ReferenceSufficiency | null
    reference_assets: Array<{ code: string; sha256: string }>
  }>
  source_assets: CompiledSource[]
}

export function requiredManualMaskDigests(contract: CompiledContract | null | undefined): string[] {
  return [...new Set(contract?.source_assets.flatMap((source) => {
    const mask = source.authorization_mask
    return mask?.requires_manual_review ? [mask.sha256] : []
  }) ?? [])]
}

export type ReviewerVerdict = {
  reviewer_id: 'reviewer_a' | 'reviewer_b'
  provider: string
  passed: boolean
  checks: Record<string, boolean | number | string>
  reasons: string[]
}

export type Job = {
  id: string
  batch_id: string
  stage: 'analyze' | 'swap' | 'validate'
  item_key: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'needs_review'
  phase: 'queued' | 'analyzing' | 'planning' | 'generating' | 'reviewing' | 'materializing' | 'validating' | 'completed' | 'needs_review' | 'failed'
  current_iteration: number
  total_iterations: number
  progress_message?: string | null
  phase_started_at?: string | null
  attempt: number
  provider?: string | null
  error_code?: string | null
  error_message?: string | null
  error_retryable?: boolean
  created_at: string
  updated_at: string
  output?: {
    review_reasons?: string[]
    requires_manual_review?: boolean
    reviewer_verdicts?: ReviewerVerdict[]
    result_asset_id?: string
    generation_review?: {
      passed?: boolean
      summary?: string
      stop_reason?: string
      scores?: Record<string, number>
      issues?: Array<{
        category?: string
        severity?: string
        evidence?: string
        correction?: string
      }>
    }
    passed?: boolean
    checks?: Record<string, boolean | number | string>
    reasons?: string[]
    faces?: Array<{
      external_face_id: string
      bbox: { x: number; y: number; width: number; height: number }
      detection_confidence: number
    }>
    detection_audit?: {
      available: boolean
      complete: boolean
      detected_face_count: number
      visible_face_count?: number | null
      requires_manual_review: boolean
      summary: string
    } | null
  }
}

export type Batch = BatchSummary & {
  assets: Asset[]
  identities: Identity[]
  persons: Person[]
  jobs: Job[]
  latest_jobs: Job[]
  contract_hash: string | null
  confirmed_at: string | null
  requires_manual_review: boolean
  compiled_contract: CompiledContract | null
}

export type StageCounts = {
  total: number
  queued: number
  running: number
  succeeded: number
  failed: number
  needs_review: number
}

export type BatchProgress = {
  batch_id: string
  stages: Record<string, StageCounts>
}

export type ReviewScores = {
  instruction_fulfillment?: number
  target_identity_consistency?: number
  face_naturalness?: number
  pose_expression_preservation?: number
  source_content_preservation?: number
  text_logo_preservation?: number
}

export type PreviewIssue = {
  category: string
  severity: 'critical' | 'major' | 'minor'
  evidence: string
  correction: string
}

export type PreviewReport = {
  strategy?: string
  model?: string
  max_iterations?: number
  completed_iterations?: number
  stopped_reason?: string
  multimodal_review?: {
    passed?: boolean
    scores?: ReviewScores
    issues?: PreviewIssue[]
    summary?: string
  }
  detected_face_count?: number
}

export type PreviewResult = {
  blob: Blob
  report: PreviewReport | null
  needsReview: boolean
}
