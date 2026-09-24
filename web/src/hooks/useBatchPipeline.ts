import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import confetti from 'canvas-confetti';
import { saveAs } from 'file-saver';
import { useStudioStore } from '@/stores/useStudioStore';
import { isActiveJob, newestActiveRecentJob, useJobActivityStore } from '@/stores/useJobActivityStore';
import { useToast } from '@/components/ui/ToastProvider';
import { api } from '@/services/api';
import { buildBatchSwapPayload, resolveExecutionMappings } from '@/utils/swapPayload';
import { snapshotRefinementInput } from '@/features/refinement/sessionRecipe';
import type { SessionInput } from '@/features/refinement/sessionTypes';
import { collectSwapWarnings } from '@/utils/swapWarnings';
import { buildWorkspaceZip, hasWorkspaceResult } from '@/utils/workspaceExport';
import type { TaskDetailData } from '@/types';
import { apiErrorMessage } from '@/utils/apiError';
/** Own batch submission and recovery once at app level, independent of visible sidebars. */
export function useBatchPipeline() {
  const [taskSummary, setTaskSummary] = useState<{
    total: number;
    costSeconds: number;
    avgSpeed: number;
  } | null>(null);

  const {
    targetImages,
    sourceGallery,
    sourcePeople,
    personMappings,
    parameters,
    isProcessing,
    setIsProcessing,
    activeTaskId,
    batchProgress,
    setBatchProgress,
    updateTargetImageById,
    setActiveTaskId,
    setIsMatrixOpen,
    setPersonMappings,
  } = useStudioStore();

  const queryClient = useQueryClient();
  const toast = useToast();
  const { jobs, upsertJob, updateJob } = useJobActivityStore();
  const [queryError, setQueryError] = useState<string | null>(null);
  const [taskDetail, setTaskDetail] = useState<TaskDetailData | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [aiRefinement, setAiRefinement] = useState(false);
  const refinementSnapshots = useRef<Record<string, SessionInput>>({});
  const startedAtRef = useRef<number | null>(null);
  const completedTaskRef = useRef<string | null>(null);
  const reportedQueryError = useRef<string | null>(null);
  const submittedTargetIdsRef = useRef<string[]>([]);
  // Recover only an explicitly linked client job. Falling back to the newest
  // server task makes an untouched fresh workspace look like it just completed old images.
  useEffect(() => {
    const linked = activeTaskId ? jobs.find((job) => job.taskId === activeTaskId) : newestActiveRecentJob(jobs);
    if (
      !linked ||
      !linked.targetIds.length ||
      !linked.targetIds.every((id) => targetImages.some((target) => target.id === id))
    )
      return;
    submittedTargetIdsRef.current = linked.targetIds;
    startedAtRef.current = Date.parse(linked.submittedAt);
    if (!activeTaskId) {
      setActiveTaskId(linked.taskId);
      setIsProcessing(isActiveJob(linked));
    }
  }, [activeTaskId, jobs, setActiveTaskId, setIsProcessing, targetImages]);
  const createBatchMutation = useMutation({
    mutationFn: ({ payload }: { payload: Parameters<typeof api.createBatchTask>[0]; targetIds: string[] }) =>
      api.createBatchTask(payload),
    onSuccess: (response, variables) => {
      const taskId = response.data.task_id;
      startedAtRef.current = Date.now();
      completedTaskRef.current = null;
      submittedTargetIdsRef.current = variables.targetIds;
      setActiveTaskId(taskId);
      setQueryError(null);
      const now = new Date().toISOString();
      upsertJob({
        taskId,
        targetIds: variables.targetIds,
        targetNames: targetImages
          .filter((target) => variables.targetIds.includes(target.id))
          .map((target) => target.name),
        status: 'QUEUED',
        submittedAt: now,
        updatedAt: now,
      });
      variables.targetIds.forEach((id) =>
        updateTargetImageById(id, {
          status: 'PROCESSING',
          refinedUrl: undefined,
          acceptedRefinement: undefined,
          refinementSessionId: undefined,
          refinementInput: undefined,
          pendingRefinement: { taskId, input: refinementSnapshots.current[id] },
          refinementError: undefined,
        }),
      );
    },
    onError: (err: unknown) => {
      setIsProcessing(false);
      const message = apiErrorMessage(err);
      setQueryError(`提交批量任务失败：${message}`);
      toast({ title: '批量任务提交失败', description: message, kind: 'error' });
    },
  });
  const taskQuery = useQuery({
    queryKey: ['batch-task', activeTaskId],
    queryFn: () => api.getTaskStatus(activeTaskId!),
    enabled: Boolean(activeTaskId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'COMPLETED' || status === 'PARTIAL' || status === 'FAILED' ? false : 500;
    },
    retry: 8,
    retryDelay: 500,
  });
  useEffect(() => {
    if (!taskQuery.error || !activeTaskId) return;
    const message = apiErrorMessage(taskQuery.error, '无法查询任务状态');
    const errorKey = `${activeTaskId}:${message}`;
    if (reportedQueryError.current !== errorKey) {
      reportedQueryError.current = errorKey;
      toast({ title: '批量任务状态读取失败', description: message, kind: 'error' });
    }
    if (axios.isAxiosError(taskQuery.error) && taskQuery.error.response?.status === 404) {
      setQueryError('原批量任务已不存在，已清除恢复入口。');
      updateJob(activeTaskId, { status: 'UNKNOWN', error: '任务不存在' });
      setActiveTaskId(null);
      setIsProcessing(false);
      setTaskDetail(null);
      return;
    }
    setQueryError(`无法恢复任务状态：${message}`);
    updateJob(activeTaskId, { status: 'UNKNOWN', error: message });
    setIsProcessing(false);
  }, [activeTaskId, setActiveTaskId, setIsProcessing, taskQuery.error, updateJob, toast]);

  useEffect(() => {
    const detail = taskQuery.data;
    if (!detail || detail.task_id !== activeTaskId) return;
    reportedQueryError.current = null;
    setTaskDetail(detail);
    setQueryError(null);
    updateJob(detail.task_id, {
      status: detail.status,
      error: detail.error,
    });
    setBatchProgress(Math.round(detail.progress * 100));
    detail.results.forEach((result) => {
      const targetId = result.client_target_id;
      if (!submittedTargetIdsRef.current.includes(targetId)) return;
      if (result.status === 'SUCCESS' || result.status === 'PARTIAL') {
        const current = useStudioStore.getState().targetImages.find((target) => target.id === targetId);
        const pending = current?.pendingRefinement;
        // The saved execution input survives a tab refresh; current editor mappings are not its substitute.
        const snapshot =
          refinementSnapshots.current[targetId] ?? (pending?.taskId === detail.task_id ? pending.input : undefined);
        updateTargetImageById(targetId, {
          processedUrl: result.processed_url,
          ...(current?.processedUrl !== result.processed_url
            ? { refinedUrl: undefined, acceptedRefinement: undefined }
            : {}),
          refinementSessionId:
            result.refinement_session_id ||
            (current?.processedUrl === result.processed_url ? current?.refinementSessionId : undefined),
          refinementError: result.refinement_error,
          refinementInput:
            snapshot && result.processed_url
              ? {
                  ...snapshot,
                  original_image_url: result.original_url,
                  baseline_image_url: result.processed_url,
                }
              : current?.processedUrl === result.processed_url
                ? current?.refinementInput
                : undefined,
          pendingRefinement: undefined,
          status: 'SUCCESS',
          costMs: result.cost_ms,
          facesDetected: result.faces_detected,
          facesSwapped: result.faces_swapped,
          swappedPersonIds: result.swapped_person_indices,
          personOutcomes: result.person_outcomes,
          swapWarning: result.warning,
          matchedSimilarities: result.matched_similarities,
        });
      } else if (result.status === 'FAILED') {
        updateTargetImageById(targetId, { status: 'FAILED', error: result.error });
      }
    });
    if (detail.status !== 'COMPLETED' && detail.status !== 'PARTIAL' && detail.status !== 'FAILED') return;
    if (detail.status === 'FAILED') {
      const returnedIds = new Set(detail.results.map((result) => result.client_target_id));
      submittedTargetIdsRef.current.forEach((targetId) => {
        if (!returnedIds.has(targetId))
          updateTargetImageById(targetId, { status: 'FAILED', error: detail.error || '批量任务失败' });
      });
    }
    setIsProcessing(false);
    void queryClient.cancelQueries({ queryKey: ['batch-task', activeTaskId] });
    void queryClient.invalidateQueries({ queryKey: ['batch-task', activeTaskId] });
    if (completedTaskRef.current !== detail.task_id) {
      completedTaskRef.current = detail.task_id;
      const elapsedSec = (Date.now() - (startedAtRef.current ?? Date.now())) / 1000;
      setTaskSummary({
        total: detail.total_images,
        costSeconds: Math.round(elapsedSec * 10) / 10,
        avgSpeed: Math.round((elapsedSec / Math.max(detail.total_images, 1)) * 100) / 100,
      });
      const warnings = collectSwapWarnings(detail.results);
      const failures = detail.results.flatMap((result) => {
        const name =
          useStudioStore.getState().targetImages.find((item) => item.id === result.client_target_id)?.name ||
          result.client_target_id;
        return [
          ...(result.status === 'FAILED' ? [`${name}：${result.error || '换脸处理失败'}`] : []),
          ...(result.refinement_error ? [`${name} · AI 精修：${result.refinement_error}`] : []),
        ];
      });
      if (detail.error) failures.unshift(detail.error);
      if (failures.length || detail.status === 'FAILED') {
        toast({
          title: detail.status === 'FAILED' ? '批量换脸失败' : '批量任务部分处理失败',
          description: [...failures, ...warnings].join('\n\n') || '任务未完成，请检查输入后重试。',
          kind: 'error',
        });
      } else if (warnings.length) {
        toast({
          title: '部分人物未完成换脸',
          description: warnings.join('；'),
          kind: 'warning',
          modal: true,
        });
      } else if (detail.status === 'COMPLETED') {
        confetti({ particleCount: 60, spread: 60, origin: { y: 0.92 }, colors: ['#5b5fef', '#06b6d4', '#22c55e'] });
      }
    }
  }, [
    activeTaskId,
    queryClient,
    setBatchProgress,
    setIsProcessing,
    taskQuery.data,
    toast,
    updateJob,
    updateTargetImageById,
  ]);

  /* Run full batch swapping pipeline */
  const handleStartBatchSwap = async () => {
    // Read the synchronous store guard so repeated clicks and another sidebar cannot submit twice.
    const live = useStudioStore.getState();
    if (
      !targetImages.length ||
      !sourceGallery.length ||
      live.isProcessing ||
      live.targetImages.some((target) => target.status === 'PROCESSING')
    )
      return;

    const built = buildBatchSwapPayload({ sourcePeople, sourceGallery, targetImages, personMappings }, parameters);
    if (!built.ready) {
      setIsMatrixOpen(true);
      return;
    }
    if (built.autoCreated) setPersonMappings(built.mappings);
    const payload = { ...built.payload, ai_refinement: aiRefinement };
    refinementSnapshots.current = Object.fromEntries(
      targetImages.map((target) => [target.id, snapshotRefinementInput(target, built.payload.person_mappings, '')]),
    );
    setQueryError(null);
    setTaskSummary(null);
    setIsProcessing(true);
    setBatchProgress(0);
    const targetIds = targetImages.map((target) => target.id);
    await queryClient.cancelQueries({ queryKey: ['batch-task'] });
    createBatchMutation.mutate({ payload, targetIds });
  };

  /* Download all swapped images as ZIP */
  const handleDownloadAllZip = async () => {
    setIsExporting(true);
    try {
      const { zip, packed, failures } = await buildWorkspaceZip(useStudioStore.getState().targetImages);
      const content = await zip.generateAsync({ type: 'blob' });
      saveAs(content, `PersonaWeave_Batch_Swapped_${Date.now()}.zip`);
      if (failures.length)
        toast({
          title: `已导出 ${packed} 张，${failures.length} 张失败`,
          description: 'ZIP 内含失败清单。',
          kind: 'info',
        });
    } catch (error) {
      toast({ title: 'ZIP 导出失败', description: error instanceof Error ? error.message : '请重试', kind: 'error' });
    } finally {
      setIsExporting(false);
    }
  };

  const submittedIds = new Set(submittedTargetIdsRef.current);
  const completedCount = targetImages.filter(hasWorkspaceResult).length;
  const failedCount = targetImages.filter((item) => item.status === 'FAILED').length;
  const totalCount = activeTaskId && submittedIds.size > 0 ? submittedIds.size : targetImages.length;
  const mappingStatus = resolveExecutionMappings({ sourcePeople, sourceGallery, targetImages, personMappings });

  const retryConnection = () => {
    if (!activeTaskId) return;
    setQueryError(null);
    reportedQueryError.current = null;
    updateJob(activeTaskId, { status: 'PROCESSING', error: undefined });
    setIsProcessing(true);
    void taskQuery.refetch();
  };

  return {
    taskSummary,
    waitingReason: taskDetail?.waiting_reason,
    queryError,
    isExporting,
    aiRefinement,
    setAiRefinement,
    isProcessing,
    batchProgress,
    completedCount,
    failedCount,
    totalCount,
    mappingStatus,
    taskStatus: taskDetail?.status,
    canRetry: Boolean(activeTaskId && queryError),
    startBatch: handleStartBatchSwap,
    downloadZip: handleDownloadAllZip,
    retryConnection,
  };
}

export type BatchPipeline = ReturnType<typeof useBatchPipeline>;
