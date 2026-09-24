import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import { useStudioStore } from '@/stores/useStudioStore';
import type { TargetImageItem } from '@/types';
import { sessionApi } from '../sessionApi';
import { validateSessionInput } from '../sessionRecipe';
import { useSessionEvents } from './useSessionEvents';
import { forgetSession } from '../sessionCache';
import { useWorkbenchStore } from '../workbenchStore';
import type { SessionCreate, SessionDetail, TurnMode } from '../sessionTypes';
import { syncAcceptedResult } from '../acceptedResults';
import { apiErrorMessage } from '@/utils/apiError';

export const sessionKey = (id: string | null) => ['refinement-session', id] as const;
export const sessionsKey = ['refinement-sessions'] as const;
/** Server snapshots own progress and acceptance; local choices never overwrite these facts. */
export function useSessionWorkbench(target?: TargetImageItem) {
  const workspace = useWorkbenchStore();
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const creations = useRef<Record<string, SessionCreate>>({});
  const sessions = useQuery({ queryKey: sessionsKey, queryFn: () => sessionApi.list(), refetchInterval: 5000 });
  const detail = useQuery({
    queryKey: sessionKey(workspace.sessionId),
    queryFn: async () => {
      const fresh = await sessionApi.get(workspace.sessionId!);
      const current = client.getQueryData<SessionDetail>(sessionKey(workspace.sessionId));
      return current && current.revision > fresh.revision ? current : fresh;
    },
    enabled: !!workspace.sessionId,
    retry: false,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1500 : false),
  });
  const models = useQuery({
    queryKey: ['ai-models'],
    queryFn: () => api.getAIModels(),
    retry: false,
    staleTime: 30000,
  });
  const session = detail.data;
  useEffect(() => {
    if (session && !detail.isError) syncAcceptedResult(session);
  }, [session, detail.isError]);
  useEffect(() => {
    if (workspace.sessionId && axios.isAxiosError(detail.error) && detail.error.response?.status === 404) {
      void forgetSession(client, workspace.sessionId);
    }
  }, [client, workspace.sessionId, detail.error]);
  useSessionEvents(client, session?.id ?? null);
  const selection = session ? (workspace.selections[session.id] ?? {}) : {};
  const viewVersionId = selection.viewVersionId ?? session?.baseline_version_id;
  const pending = session ? workspace.pendingTurns[session.id] : undefined;
  const baseVersionId = pending?.base_version_id ?? selection.baseVersionId ?? session?.baseline_version_id;
  const diagnosisTurn = session?.turns
    .slice()
    .reverse()
    .find(
      (turn) =>
        turn.base_version_id === baseVersionId &&
        !!turn.diagnosis &&
        turn.mode === 'discuss' &&
        turn.status === 'completed',
    );
  const diagnosisConfirmed = !!diagnosisTurn && selection.confirmedDiagnosisTurnId === diagnosisTurn.id;
  useEffect(() => {
    if (session && pending && session.turns.some((turn) => turn.request_id === pending.request_id)) {
      workspace.pending(session.id);
    }
  }, [session, pending, workspace.pending]);
  useEffect(() => {
    setError(null);
  }, [workspace.sessionId, workspace.targetId]);
  const apply = (data: SessionDetail) => {
    syncAcceptedResult(data);
    client.setQueryData<SessionDetail>(sessionKey(data.id), (current) =>
      current && current.revision > data.revision ? current : data,
    );
    void client.invalidateQueries({ queryKey: sessionsKey });
  };
  const perform = async (action: () => Promise<void>) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      const code = axios.isAxiosError(err) ? err.response?.status : undefined;
      if (code === 409) {
        if (workspace.sessionId) workspace.pending(workspace.sessionId);
        await client.invalidateQueries({ queryKey: sessionKey(workspace.sessionId) });
        setError('会话版本已变化，已重新读取。请确认基图与最新状态后再操作。');
      } else if (code === 503) setError('AI 服务尚未就绪，请检查通用模型配置后重试。');
      else if (code === 422 || code === 400)
        setError(`服务器已拒绝本次操作：${apiErrorMessage(err, '请修改人物、参考或意见后再提交。')}`);
      else if (code === 404) setError('会话或图片已不可用，请刷新会话列表。');
      else setError('请求未能确认。若已提交，请用“重试同一请求”恢复，不会另建付费轮次。');
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };
  const create = () =>
    perform(async () => {
      if (!target?.refinementInput) {
        setError('缺少本次换脸的不可变输入与人物快照，请重新执行本地换脸。');
        return;
      }
      const problem = validateSessionInput(target.refinementInput);
      if (problem) {
        setError(problem);
        return;
      }
      const request = (creations.current[target.id] ??= {
        ...target.refinementInput,
        request_id: crypto.randomUUID(),
        target_id: target.id,
        title: target.name,
      });
      try {
        const result = await sessionApi.create(request);
        apply(result);
        delete creations.current[target.id];
        const current = useStudioStore.getState().targetImages.find((item) => item.id === target.id);
        if (current?.refinementInput?.baseline_image_url === request.baseline_image_url) {
          useStudioStore.getState().updateTargetImageById(target.id, { refinementSessionId: result.id });
        }
        if (useWorkbenchStore.getState().targetId === target.id) workspace.openSession(result.id);
      } catch (err) {
        if (axios.isAxiosError(err) && err.response && err.response.status >= 400 && err.response.status < 500)
          delete creations.current[target.id];
        throw err;
      }
    });
  const submit = (mode: TurnMode, modelAlias?: string) =>
    perform(async () => {
      if (!session || !baseVersionId || session.status === 'running' || session.active_turn_id) return;
      if (mode === 'generate' && !pending && !diagnosisConfirmed) {
        setError('请先确认当前基图的问题分析，再补充意见并生成。');
        return;
      }
      if (selection.message && selection.message.length > 4000) {
        setError('意见超过 4000 字，请明确缩短后提交；内容未截断。');
        return;
      }
      const request = workspace.pendingTurns[session.id] ?? {
        request_id: crypto.randomUUID(),
        expected_revision: session.revision,
        base_version_id: baseVersionId,
        mode,
        message:
          selection.message?.trim() ||
          (mode === 'discuss'
            ? '请检查当前图与原图、人物参考，列出有证据的问题和修改建议；没有明确问题就如实说明。本轮只检查，不生成图片。'
            : mode === 'generate' && diagnosisTurn?.diagnosis?.needs_edit
              ? '请结合当前基图已有的问题分析，按建议的编辑计划生成一个精修版本。'
              : mode === 'auto'
                ? '分析原图、当前结果与人物参考，有明确问题时生成一个精修版本。'
                : ''),
        ...(mode === 'generate' ? { diagnosis_turn_id: diagnosisTurn!.id } : {}),
        ...(modelAlias ? { model_alias: modelAlias } : {}),
      };
      if (!request.message) {
        setError('请先描述要讨论或修改的问题。');
        return;
      }
      workspace.pending(session.id, request);
      try {
        const result = await sessionApi.turn(session.id, request);
        apply(result);
        workspace.pending(session.id);
        // Analysis keeps the user's draft available for the subsequent generation.
        if (request.mode !== 'discuss') workspace.select(session.id, { message: '' });
      } catch (err) {
        if (axios.isAxiosError(err) && err.response && err.response.status >= 400 && err.response.status < 500)
          workspace.pending(session.id);
        throw err;
      }
    });
  const accept = () =>
    perform(async () => {
      const version = session?.versions.find((item) => item.id === viewVersionId);
      if (!session || !version || session.status === 'running' || session.active_turn_id || pending) return;
      apply(await sessionApi.accept(session.id, session.revision, version.id, version.digest));
    });
  const cancel = () =>
    perform(async () => {
      if (session) apply(await sessionApi.cancel(session.id, session.revision));
    });
  return {
    workspace,
    sessions,
    detail,
    session,
    models,
    error,
    busy,
    pending,
    viewVersionId,
    baseVersionId,
    diagnosisTurn,
    diagnosisConfirmed,
    message: pending?.message ?? selection.message ?? '',
    create,
    submit,
    accept,
    cancel,
  };
}
