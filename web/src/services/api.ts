import axios from 'axios';
import type {
  AIModelCatalog,
  BatchImageItem,
  BatchTaskSummary,
  PersonMappingItem,
  PortraitLibraryData,
  RefinementTaskData,
  ServerHealth,
  SwapParameters,
  TaskDetailData,
} from '@/types';

export const apiClient = axios.create({
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
    batch_images: BatchImageItem[];
    parameters: SwapParameters;
    ai_refinement?: boolean;
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

  async deleteBatchTask(taskId: string): Promise<void> {
    await apiClient.delete(`/api/v1/batch-tasks/${taskId}`);
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

  async deleteLegacyRefinementTask(taskId: string, expected_revision: number): Promise<void> {
    await apiClient.delete('/api/v1/refinement/tasks/' + encodeURIComponent(taskId), { params: { expected_revision } });
  },
  async listLegacyRefinementTasks(
    limit = 30,
    offset = 0,
  ): Promise<
    Array<{
      task_id: string;
      status: string;
      created_at: string;
      completed_at?: string | null;
      revision: number;
      has_candidate: boolean;
      has_accepted: boolean;
    }>
  > {
    return (await apiClient.get('/api/v1/refinement/legacy-tasks', { params: { limit, offset } })).data.data;
  },
  async getRefinementTask(taskId: string, signal?: AbortSignal): Promise<RefinementTaskData> {
    const res = await apiClient.get(`/api/v1/refinement/tasks/${taskId}`, { signal });
    return res.data.data;
  },

  async getRefinementQualitySummary(signal?: AbortSignal): Promise<import('@/types').RefinementQualitySummary> {
    const res = await apiClient.get('/api/v1/refinement/quality-summary', { signal });
    return res.data.data;
  },

  async getRefinementQuality(taskId: string, signal?: AbortSignal): Promise<import('@/types').RefinementQualityData> {
    const res = await apiClient.get(`/api/v1/refinement/tasks/${taskId}/quality-evaluation`, { signal });
    return res.data.data;
  },

  async saveRefinementQuality(
    taskId: string,
    payload: import('@/types').RefinementQualityEvaluationRequest,
  ): Promise<import('@/types').RefinementQualityEvaluation> {
    const res = await apiClient.put(`/api/v1/refinement/tasks/${taskId}/quality-evaluation`, payload);
    return res.data.data;
  },
};
