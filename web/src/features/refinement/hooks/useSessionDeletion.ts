import { useQueryClient } from '@tanstack/react-query';
import { useRecordDeletion } from '@/hooks/useRecordDeletion';
import { sessionApi } from '../sessionApi';
import { forgetSession } from '../sessionCache';
import type { SessionSummary } from '../sessionTypes';
import { sessionKeys, sessionsKey } from '../queryKeys';

/** Shared deletion behavior for the history list and the open workbench. */
export function useSessionDeletion(sessions: SessionSummary[]) {
  const client = useQueryClient();
  return useRecordDeletion({
    records: sessions
      .filter((item) => item.status === 'idle' && !item.active_turn_id)
      .map((item) => ({ id: item.id, revision: item.revision })),
    description: '精修会话及其全部版本、参考快照和已接受文件（保留原批任务、素材库及已保存的工程副本）',
    remove: ({ id, revision }) => sessionApi.delete(id, revision),
    onDeleted: (id) => forgetSession(client, id),
    refresh: () => {
      void client.invalidateQueries({ queryKey: sessionsKey });
      void client.invalidateQueries({ queryKey: sessionKeys });
    },
  });
}
