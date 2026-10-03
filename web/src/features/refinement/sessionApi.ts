import { apiClient } from '@/services/api';
import type { SessionCreate, SessionDetail, SessionSummary, TurnRequest } from './sessionTypes';

const root = '/api/v1/refinement/sessions';
const sessionPath = (id: string) => root + '/' + encodeURIComponent(id);
export const sessionApi = {
  async delete(id: string, expected_revision: number): Promise<void> {
    await apiClient.delete(sessionPath(id), { params: { expected_revision } });
  },
  async list(limit = 50, offset = 0): Promise<SessionSummary[]> {
    return (await apiClient.get(root, { params: { limit, offset } })).data.data;
  },
  async get(id: string): Promise<SessionDetail> {
    return (await apiClient.get(sessionPath(id))).data.data;
  },
  async create(payload: SessionCreate): Promise<SessionDetail> {
    return (await apiClient.post(root, payload)).data.data;
  },
  async turn(id: string, payload: TurnRequest): Promise<SessionDetail> {
    return (await apiClient.post(sessionPath(id) + '/turns', payload)).data.data;
  },
  async cancel(id: string, expected_revision: number): Promise<SessionDetail> {
    return (await apiClient.post(sessionPath(id) + '/cancel', { expected_revision })).data.data;
  },
  async accept(
    id: string,
    expected_revision: number,
    version_id: string,
    candidate_digest: string,
  ): Promise<SessionDetail> {
    return (await apiClient.post(sessionPath(id) + '/accept', { expected_revision, version_id, candidate_digest })).data
      .data;
  },
};
