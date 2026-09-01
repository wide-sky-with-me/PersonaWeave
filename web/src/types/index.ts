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
  global_person_id?: number; // Global consistent person ID across entire batch
  bbox: [number, number, number, number]; // [x1, y1, x2, y2]
  score?: number;
  yaw?: number;
  isSelected?: boolean;
}

export interface RefinementDraftState {
  selected: boolean;
  instructions: string;
}

export interface RefinementWorkspaceState {
  drafts: Record<number, RefinementDraftState>;
  globalInstructions: string;
  modelAlias?: string;
  diagnoses: Record<number, RefinementReview>;
  diagnosisErrors: Record<number, string>;
  task: RefinementTaskData | null;
  candidateUrl: string | null;
  error: string | null;
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
  refinementWorkspace?: RefinementWorkspaceState;
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
export interface PersonSwapOutcome {
  person_index: number;
  status: 'SWAPPED' | 'SKIPPED';
  reason: string;
  model?: string;
  target_yaw?: number;
  closest_source_yaw_gap?: number | null;
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
}

export interface ServerHealth {
  status: string;
  app_name: string;
  version: string;
  pipeline_ready: boolean;
  providers: string[];
  gpu_available: boolean;
}

export interface RefinementIssue {
  category: string;
  severity: 'low' | 'medium' | 'high';
  region: string;
  description: string;
  suggestion: string;
}

export interface RefinementReview {
  overall_score: number;
  anatomy_score: number;
  coherence_score: number;
  naturalness_score: number;
  lighting_score: number;
  person_locator: string;
  issues: RefinementIssue[];
  strengths: string[];
  recommended_prompt: string;
  should_refine: boolean;
  model_used: string;
}

export interface RefinementReviewPersonInput {
  person_id: number;
  face_index: number;
  person_locator?: string;
  user_focus?: string;
}

export interface RefinementReviewRequest {
  swapped_image_url: string;
  original_image_url?: string;
  people: RefinementReviewPersonInput[];
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


export interface RefinementPersonRequest {
  person_id: number;
  face_index: number;
  person_locator: string;
  review_prompt: string;
  user_instructions: string;
  issues?: RefinementIssue[];
}

export interface RefinementTaskCreateRequest {
  swapped_image_url: string;
  original_image_url?: string;
  people: RefinementPersonRequest[];
  global_instructions: string;
  model_alias?: string;
}

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
export interface RefinementRetryRequest {
  user_instructions?: string;
  model_alias?: string;
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
