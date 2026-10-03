export type TargetPersonId = number;
export type SourcePersonId = number;

export type SourceMode = 'best-match' | 'gallery';
export type ColorTransferMode = 'off' | 'boundary';
export type SwapperModel = 'auto' | 'inswapper_128' | 'hyperswap_1c_256' | 'ghost_1_256';
export type ViewMode = 'SINGLE' | 'SPLIT' | 'MAGNIFIER' | 'BRUSH';

export interface PersonMappingItem {
  person_index: number; // Stable global person ID across the complete batch task
  target_reference_url: string;
  target_reference_person_index: number; // 1-based face position in the representative image
  source_identity_id?: string;
  source_asset_ids: string[];
  source_manual_images: string[];
  source_mode?: SourceMode;
  label?: string;
}

export interface SourcePerson {
  id: SourcePersonId;
  name: string;
  libraryIdentityId?: string;
}

interface SourceReferenceBase {
  id: string;
  name: string;
  facePreview?: string;
  yaw?: number;
  yawLabel?: string;
  qualityScore?: number;
  qualityTier?: 'excellent' | 'usable' | 'low';
  qualityNotes?: string[];
  personId: SourcePersonId;
}

// A reference backed by an opaque managed-library asset ID; never a raw URL.
export interface ManagedSourceReference extends SourceReferenceBase {
  kind: 'managed';
  assetId: string;
}

// An ad-hoc reference uploaded/supplied outside the managed library, as a data URI/URL/local path.
export interface ManualSourceReference extends SourceReferenceBase {
  kind: 'manual';
  imageData: string;
}

export type SourceReference = ManagedSourceReference | ManualSourceReference;

export interface PortraitAssetItem {
  category: 'angle' | 'expression' | 'contact_sheet';
  label: string;
  name: string;
  asset_id: string;
}

export interface PortraitLibraryIdentity {
  id: string;
  code: string;
  gender: string;
  age: number;
  age_group: string;
  display_name: string;
  cover_asset_id: string;
  contact_sheet_asset_id?: string | null;
  asset_count: number;
  assets: PortraitAssetItem[];
}

export interface PortraitLibraryData {
  available: boolean;
  total_identities: number;
  total_assets: number;
  identities: PortraitLibraryIdentity[];
}
export interface DetectedFaceBox {
  index: number; // 1-based spatial index on this image
  global_person_id?: number | null; // Single-image detection has no cross-image identity yet.
  bbox: [number, number, number, number]; // [x1, y1, x2, y2]
  score?: number;
  yaw?: number;
  isSelected?: boolean;
}

export interface ManualMaskState {
  dataUrl: string | null;
  undoStack: (string | null)[];
  redoStack: (string | null)[];
}

export interface TargetImageItem {
  id: string;
  name: string;
  url: string;
  base64Data?: string;
  imageWidth?: number;
  imageHeight?: number;
  faces?: DetectedFaceBox[];
  selectedTargetPersonId?: number;
  processedUrl?: string;
  processedBase64?: string;
  refinedUrl?: string;
  acceptedRefinement?: { sessionId: string; versionId: string; revision: number };
  /** Persist absence so reopening a project cannot silently export its baseline as the final result. */
  refinementResultUnavailable?: boolean;
  status: 'PENDING' | 'IDLE' | 'PROCESSING' | 'SUCCESS' | 'FAILED';
  costMs?: number;
  facesDetected?: number;
  facesSwapped?: number;
  swappedPersonIds?: number[];
  matchedSimilarities?: number[];
  modelRouting?: ModelRoutingDecision[];
  swapWarning?: string;
  personOutcomes?: PersonSwapOutcome[];
  error?: string;
  manualMask?: ManualMaskState;
  refinementSessionId?: string;
  refinementError?: string;
  refinementInput?: import('@/features/refinement/sessionTypes').SessionInput;
  pendingRefinement?: { taskId: string; input: import('@/features/refinement/sessionTypes').SessionInput };
}

export interface SwapParameters {
  similarity_threshold: number;
  identity_blend_strength: number;
  enable_color_transfer: boolean;
  color_transfer_mode: ColorTransferMode;
  enable_dense_mask: boolean;
  manual_mask?: string;
  source_mode: SourceMode;
  swapper_model: SwapperModel;
  allow_research_models: boolean;
}

export interface ModelRoutingDecision {
  requested: SwapperModel;
  selected: Exclude<SwapperModel, 'auto'>;
  reason: string;
  fallback: boolean;
}
/** Optional evidence from newer tasks; absent fields are unknown, never inferred. */
export interface PersonOutcomeDiagnosticsData {
  matching?: {
    best_similarity?: number | null;
    assigned_similarity?: number | null;
    threshold?: number | null;
    face_index?: number | null;
  } | null;
  source_selection?: {
    mode?: string | null;
    reference_count?: number | null;
    selected_reference_index?: number | null;
    gallery_weight?: number | null;
    best_reference_weight?: number | null;
    gallery_reference_weights?: number[] | null;
    gallery_weights_known?: boolean;
  } | null;
  mask?: {
    components?: { name: string; status: string; mean?: number | null; failure_code?: string | null }[] | null;
    intersection_mean?: number | null;
    bbox_alpha_mean?: number | null;
    requested_strength?: number | null;
  } | null;
}

export interface PersonSwapOutcome {
  person_index: number;
  status: 'SWAPPED' | 'SKIPPED';
  reason: string;
  model?: string;
  target_yaw?: number;
  closest_source_yaw_gap?: number | null;
  diagnostics?: PersonOutcomeDiagnosticsData | null;
}
export interface BatchImageItem {
  client_target_id: string;
  image_url: string;
  manual_mask?: string;
}

export interface ImageSwapResult {
  client_target_id: string;
  original_url: string;
  processed_url?: string;
  status: string;
  faces_detected: number;
  faces_swapped: number;
  swapped_person_indices: number[];
  requested_person_indices: number[];
  person_outcomes: PersonSwapOutcome[];
  matched_similarities: number[];
  model_routing: ModelRoutingDecision[];
  cost_ms: number;
  warning?: string;
  error?: string;
  refinement_session_id?: string;
  refinement_error?: string;
}

export type BatchTaskStatus = 'QUEUED' | 'PROCESSING' | 'COMPLETED' | 'PARTIAL' | 'FAILED';

export interface BatchTaskSummary {
  task_id: string;
  status: BatchTaskStatus;
  progress: number;
  total_images: number;
  processed_images: number;
  success_count: number;
  failure_count: number;
  has_partial_failure: boolean;
  viewable_results: number;
  created_at: string;
  completed_at?: string;
  error?: string;
}

export interface TaskDetailData {
  task_id: string;
  status: BatchTaskStatus;
  progress: number;
  total_images: number;
  processed_images: number;
  success_count: number;
  failure_count: number;
  has_partial_failure: boolean;
  created_at: string;
  completed_at?: string;
  results: ImageSwapResult[];
  error?: string;
  waiting_reason?: string | null;
}

export interface ServerHealth {
  status: string;
  app_name: string;
  version: string;
  pipeline_ready: boolean;
  providers: string[];
  gpu_available: boolean;
  model_providers?: Record<string, string[]>;
  local_inference?: {
    queued: number;
    active: { operation: string; phase: string; elapsed_seconds: number; waiting_reason: string | null }[];
  } | null;
}

export type RefinementTaskStatus =
  | 'QUEUED'
  | 'LOCALIZING'
  | 'REFINING'
  | 'REVIEWING'
  | 'REVIEW_REQUIRED'
  | 'ACCEPTING'
  | 'ACCEPTED'
  | 'CANCEL_REQUESTED'
  | 'CANCELLED'
  | 'FAILED';

export interface RefinementPersonProgress {
  person_id: number;
  face_index: number;
  status: string;
  progress: number;
  attempt: number;
  report?: Record<string, unknown>;
  audit?: Record<string, unknown>;
  error?: string;
}
export interface RefinementFinalPersonAssessment {
  person_id: number;
  status: 'resolved' | 'improved' | 'unchanged' | 'worsened' | 'unverifiable';
  evidence: string;
}
export interface RefinementFinalReview {
  overall_score: number;
  coherence_score: number;
  naturalness_score: number;
  people: RefinementFinalPersonAssessment[];
  unrequested_changes_detected: boolean;
  new_high_severity_issue: boolean;
  recommendation: 'accept' | 'review' | 'regenerate';
  summary: string;
  issues: string[];
  strengths: string[];
  model_used: string;
  review_error?: string;
}

export interface RefinementTaskData {
  task_id: string;
  status: RefinementTaskStatus;
  revision: number;
  progress: number;
  created_at: string;
  completed_at?: string;
  people: RefinementPersonProgress[];
  final_review?: RefinementFinalReview;
  candidate_url?: string;
  candidate_digest?: string;
  accepted_url?: string;
  error?: string;
}

export interface RefinementQualityEvaluationRequest {
  expected_revision: number;
  candidate_digest: string;
  verdict: 'ready' | 'needs_work' | 'unusable';
  failure_stages: Array<'identity' | 'local' | 'refinement'>;
  people: Array<{ person_id: number; identity_satisfied: boolean | null; naturalness_satisfied: boolean | null }>;
  unrelated_changes: boolean | null;
  notes: string;
}
export interface RefinementQualityEvaluation extends RefinementQualityEvaluationRequest {
  task_id: string;
  updated_at: string;
}
export interface RefinementQualitySummary {
  total_samples: number;
  evaluated_samples: number;
  unevaluated_samples: number;
  generation_count_unknown_samples: number;
  lineage_unknown_samples: number;
  not_generated_samples: number;
  generation_failed_total: number;
  eligible_total: number;
  first_pass_unevaluated: number;
  first_pass_ready_count: number;
  first_pass_denominator: number;
  first_pass_acceptance_rate: number | null;
  rate_definition: string;
  identity_satisfied: { true: number; false: number; unknown: number; rate_known_only: number | null };
  naturalness_satisfied: { true: number; false: number; unknown: number; rate_known_only: number | null };
}
export interface RefinementQualityData {
  provenance?: { lineage: string; batch_task_id?: string; client_target_id?: string } | null;
  task_id: string;
  generation_count: number;
  current_candidate_digest: string | null;
  evaluations: RefinementQualityEvaluation[];
  candidates: Array<{
    candidate_digest: string;
    generation_count: number;
    original_url: string | null;
    initial_local_url: string | null;
    baseline_url: string | null;
    candidate_url: string | null;
    model_alias: string;
    hashes: Record<string, string | null>;
    audit: Record<string, unknown>;
  }>;
}

/** Response from GET /api/v1/ai/models. */
export interface AIModelCatalog {
  default_alias: string;
  models: Array<{
    alias: string;
    provider: string;
    model: string;
    display_name: string;
    description: string;
    is_default: boolean;
  }>;
}
