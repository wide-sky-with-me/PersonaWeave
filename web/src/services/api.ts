import axios from 'axios';
import type {
  AIModelCatalog,
  BatchTaskSummary,
  PersonMappingItem,
  RefinementRetryRequest,
  PortraitLibraryData,
  RefinementReviewRequest,
  RefinementTaskCreateRequest,
  RefinementTaskData,
  ServerHealth,
  SwapParameters,
  TaskDetailData,
} from '../types';

const apiClient = axios.create({
  baseURL: '',
  timeout: 240000,
});

export const api = {
  async getHealth(): Promise<ServerHealth> {
    const res = await apiClient.get('/api/v1/health');
    return res.data;
  },
  /** GET /api/v1/ai/models returns configured non-secret image model metadata. */
  async getAIModels(): Promise<AIModelCatalog> {
    const res = await apiClient.get('/api/v1/ai/models');
    return res.data;
  },

  async swapSync(payload: {
    source_identity_id?: string;
    source_asset_ids?: string[];
    source_manual_images?: string[];
    person_mappings?: PersonMappingItem[];
    target_image_url: string;
    target_reference_url?: string;
    person_index?: number;
    parameters: SwapParameters;
    return_base64?: boolean;
  }) {
    const res = await apiClient.post('/api/v1/swap-sync', payload);
    return res.data;
  },

  async getPortraitLibrary(): Promise<PortraitLibraryData> {
    const res = await apiClient.get('/api/v1/portrait-library');
    return res.data.data;
  },
  async createBatchTask(payload: {
    source_identity_id?: string;
    source_asset_ids?: string[];
    source_manual_images?: string[];
    person_mappings?: PersonMappingItem[];
    target_reference_url?: string;
    person_index?: number;
    batch_images: { client_target_id: string; image_url: string; manual_mask?: string }[];
    parameters: SwapParameters;
    callback_url?: string;
  }) {
    const res = await apiClient.post('/api/v1/batch-tasks', payload);
    return res.data;
  },

  async listBatchTaskHistory(limit = 50): Promise<BatchTaskSummary[]> {
    const res = await apiClient.get('/api/v1/task-history/batch-tasks', { params: { limit } });
    return res.data.data;
  },

  async getTaskStatus(taskId: string): Promise<TaskDetailData> {
    const res = await apiClient.get(`/api/v1/batch-tasks/${taskId}`);
    return res.data.data;
  },

  async detectFaces(payload: { image_url: string; confidence_threshold?: number }, signal?: AbortSignal) {
    const res = await apiClient.post('/api/v1/detect-faces', payload, { signal });
    return res.data.data;
  },

  async clusterFaces(payload: { image_urls: string[]; similarity_threshold?: number }, signal?: AbortSignal) {
    const res = await apiClient.post('/api/v1/cluster-faces', payload, { signal });
    return res.data.data;
  },

  async arbitrateIdentity(payload: {
    reference_image_url: string;
    reference_person_index?: number;
    target_image_url: string;
  }) {
    const res = await apiClient.post('/api/v1/vlm/arbitrate', payload);
    return res.data.data;
  },

  async reviewRefinement(
    payload: RefinementReviewRequest,
    signal?: AbortSignal,
  ): Promise<Record<number, import('../types').RefinementReview>> {
    const res = await apiClient.post('/api/v1/refinement/review', payload, { signal, timeout: 125000 });
    return res.data.data;
  },

  async createRefinementTask(payload: RefinementTaskCreateRequest): Promise<RefinementTaskData> {
    const res = await apiClient.post('/api/v1/refinement/tasks', payload);
    return res.data.data;
  },

  async getRefinementTask(taskId: string, signal?: AbortSignal): Promise<RefinementTaskData> {
    const res = await apiClient.get(`/api/v1/refinement/tasks/${taskId}`, { signal });
    return res.data.data;
  },

  async cancelRefinementTask(taskId: string): Promise<RefinementTaskData> {
    const res = await apiClient.post(`/api/v1/refinement/tasks/${taskId}/cancel`);
    return res.data.data;
  },

  async acceptRefinementTask(
    taskId: string,
    payload: { expected_revision: number; candidate_digest: string },
  ): Promise<RefinementTaskData> {
    const res = await apiClient.post(`/api/v1/refinement/tasks/${taskId}/accept`, payload);
    return res.data.data;
  },

  async retryRefinementPerson(
    taskId: string,
    personId: number,
    payload: RefinementRetryRequest,
  ): Promise<RefinementTaskData> {
    const res = await apiClient.post(
      `/api/v1/refinement/tasks/${taskId}/people/${personId}/retry`,
      payload,
    );
    return res.data.data;
  },

  getRefinementCandidateUrl(taskId: string): string {
    return `/api/v1/refinement/tasks/${taskId}/candidate`;
  },
};
