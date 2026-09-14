export interface SessionIssue {
  category: 'identity' | 'anatomy' | 'occlusion' | 'blending' | 'lighting' | 'texture' | 'composition';
  person_id: number | null;
  region: string;
  description: string;
  evidence: string;
  suggestion: string;
  severity: 'low' | 'medium' | 'high';
  confidence: 'low' | 'medium' | 'high';
  uncertainty: string;
}
export interface PersonAssessment {
  person_id: number;
  person_locator: string;
  mapping_confident: boolean;
  identity_status: 'match' | 'mismatch' | 'uncertain' | 'unavailable';
  evidence: string;
  uncertainty: string;
}

export type TurnMode = 'discuss' | 'generate' | 'auto';
export interface ReferenceRecipe {
  person_id: number;
  face_index: number;
  person_locator: string;
  source_identity_id?: string;
  source_asset_ids?: string[];
  source_manual_images?: string[];
}
export interface SessionCreate {
  request_id: string;
  target_id: string;
  title: string;
  original_image_url: string;
  baseline_image_url: string;
  people: ReferenceRecipe[];
}
export type SessionInput = Omit<SessionCreate, 'request_id' | 'target_id' | 'title'>;
export interface SessionPerson {
  person_id: number;
  face_index: number;
  person_locator: string;
  reference_urls: string[];
  reference_status: 'available' | 'missing';
}
export interface ImageVersion {
  id: string;
  parent_version_id: string | null;
  digest: string;
  image_url: string;
  kind: 'baseline' | 'candidate';
  created_at: string;
  model?: string;
  task_id?: string;
}
export interface EditPlan {
  changes: string[];
  preserve: string[];
  instructions: string;
}
export interface SessionDiagnosis {
  assistant_message: string;
  needs_edit: boolean;
  mapping_uncertain: boolean;
  issues: SessionIssue[];
  people: PersonAssessment[];
  edit_plan: EditPlan;
  uncertainties: string[];
}
export interface SessionReview {
  summary: string;
  improved: string[];
  remaining: SessionIssue[];
  new_issues: SessionIssue[];
  uncertainties: string[];
  people: PersonAssessment[];
}
export interface SessionTurn {
  id: string;
  request_id: string;
  base_version_id: string;
  mode: TurnMode;
  diagnosis_turn_id?: string | null;
  message: string;
  status:
    | 'queued'
    | 'analyzing'
    | 'generating'
    | 'reviewing'
    | 'review_required'
    | 'completed'
    | 'failed'
    | 'cancel_requested'
    | 'cancelled';
  diagnosis?: SessionDiagnosis | null;
  plan?: EditPlan | null;
  assistant_message?: string | null;
  candidate_version_id?: string | null;
  generation_metadata?: {
    provider_output_size?: [number, number];
    target_canvas_size?: [number, number];
  } | null;
  review?: SessionReview | null;
  error?: string | null;
  review_error?: string | null;
  created_at: string;
  updated_at: string;
}
export interface SessionSummary {
  id: string;
  target_id: string;
  title: string;
  revision: number;
  status: 'idle' | 'running';
  accepted_version_id?: string | null;
  active_turn_id?: string | null;
  created_at: string;
  updated_at: string;
}
export interface SessionDetail extends SessionSummary {
  event_cursor?: number;
  original_url: string;
  baseline_version_id: string;
  accepted_image_url?: string | null;
  people: SessionPerson[];
  versions: ImageVersion[];
  turns: SessionTurn[];
}
export interface TurnRequest {
  request_id: string;
  expected_revision: number;
  base_version_id: string;
  mode: TurnMode;
  diagnosis_turn_id?: string | null;
  message: string;
  model_alias?: string;
}
