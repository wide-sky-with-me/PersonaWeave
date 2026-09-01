import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Play, Download, Clock, Zap, CheckCircle2, AlertCircle, Users, RotateCw } from 'lucide-react';
import confetti from 'canvas-confetti';
import JSZip from 'jszip';
import { saveAs } from 'file-saver';
import { useStudioStore } from '../../stores/useStudioStore';
import { isActiveJob, newestActiveRecentJob, useJobActivityStore } from '../../stores/useJobActivityStore';
import { useToast } from '../ui/ToastProvider';
import { api } from '../../services/api';
import { buildBatchSwapPayload, resolveExecutionMappings } from '../../utils/swapPayload';
import { collectSwapWarnings } from '../../utils/swapWarnings';
import type { TaskDetailData } from '../../types';
export const BatchPipelineDrawer: React.FC = () => {
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
  const startedAtRef = useRef<number | null>(null);
  const completedTaskRef = useRef<string | null>(null);
  const submittedTargetIdsRef = useRef<string[]>([]);
  // Recover only an explicitly linked client job. Falling back to the newest
  // server task makes an untouched fresh workspace look like it just completed old images.
  useEffect(() => {
    const linked = activeTaskId
      ? jobs.find((job) => job.taskId === activeTaskId)
      : newestActiveRecentJob(jobs);
    if (!linked) return;
    submittedTargetIdsRef.current = linked.targetIds;
    startedAtRef.current = Date.parse(linked.submittedAt);
    if (!activeTaskId) {
      setActiveTaskId(linked.taskId);
      setIsProcessing(isActiveJob(linked));
    }
  }, [activeTaskId, jobs, setActiveTaskId, setIsProcessing]);
  const createBatchMutation = useMutation({
    mutationFn: ({ payload }: { payload: Parameters<typeof api.createBatchTask>[0]; targetIds: string[] }) => api.createBatchTask(payload),
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
        targetNames: targetImages.filter((target) => variables.targetIds.includes(target.id)).map((target) => target.name),
        status: 'QUEUED',
        submittedAt: now,
        updatedAt: now,
      });
      variables.targetIds.forEach((id) => updateTargetImageById(id, {
        status: 'PROCESSING',
        refinedUrl: undefined,
        refinementWorkspace: undefined,
      }));
    },
    onError: (err: any) => {
      setIsProcessing(false);
      const message = err.response?.data?.message || err.message || '未知错误';
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
    if (axios.isAxiosError(taskQuery.error) && taskQuery.error.response?.status === 404) {
      setQueryError('原批量任务已不存在，已清除恢复入口。');
      updateJob(activeTaskId, { status: 'UNKNOWN', error: '任务不存在' });
      setActiveTaskId(null);
      setIsProcessing(false);
      setTaskDetail(null);
      return;
    }
    const message = taskQuery.error instanceof Error ? taskQuery.error.message : '无法查询任务状态';
    setQueryError(`无法恢复任务状态：${message}`);
    updateJob(activeTaskId, { status: 'UNKNOWN', error: message });
    setIsProcessing(false);
  }, [activeTaskId, setActiveTaskId, setIsProcessing, taskQuery.error, updateJob]);

  useEffect(() => {
    const detail = taskQuery.data;
    if (!detail || detail.task_id !== activeTaskId) return;
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
        updateTargetImageById(targetId, {
          processedUrl: result.processed_url,
          refinedUrl: undefined,
          refinementWorkspace: undefined,
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
        if (!returnedIds.has(targetId)) updateTargetImageById(targetId, { status: 'FAILED', error: detail.error || '批量任务失败' });
      });
    }
    setIsProcessing(false);
    void queryClient.cancelQueries({ queryKey: ['batch-task', activeTaskId] });
    void queryClient.invalidateQueries({ queryKey: ['batch-task', activeTaskId] });
    if ((detail.status === 'COMPLETED' || detail.status === 'PARTIAL') && completedTaskRef.current !== detail.task_id) {
      completedTaskRef.current = detail.task_id;
      const elapsedSec = (Date.now() - (startedAtRef.current ?? Date.now())) / 1000;
      setTaskSummary({
        total: detail.total_images,
        costSeconds: Math.round(elapsedSec * 10) / 10,
        avgSpeed: Math.round((elapsedSec / Math.max(detail.total_images, 1)) * 100) / 100,
      });
      const warnings = collectSwapWarnings(detail.results);
      if (warnings.length) {
        toast({
          title: '部分人物未完成换脸',
          description: warnings.join('；'),
          kind: 'warning',
        });
      } else if (detail.status === 'COMPLETED') {
        confetti({ particleCount: 60, spread: 60, origin: { y: 0.92 }, colors: ['#5b5fef', '#06b6d4', '#22c55e'] });
      }
    }
  }, [activeTaskId, queryClient, setBatchProgress, setIsProcessing, taskQuery.data, toast, updateJob, updateTargetImageById]);

  /* Run full batch swapping pipeline */
  const handleStartBatchSwap = async () => {
    if (targetImages.length === 0 || sourceGallery.length === 0 || isProcessing) return;

    const built = buildBatchSwapPayload({ sourcePeople, sourceGallery, targetImages, personMappings }, parameters);
    if (!built.ready) {
      setIsMatrixOpen(true);
      return;
    }
    if (built.autoCreated) setPersonMappings(built.mappings);
    const payload = built.payload;
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
    const detailResults = taskDetail?.results.filter((result) => (result.status === 'SUCCESS' || result.status === 'PARTIAL') && result.processed_url) ?? [];
    if (detailResults.length === 0) {
      toast({ title: '暂无可下载的已完成图片', kind: 'info' });
      return;
    }
    setIsExporting(true);
    const zip = new JSZip();
    const folder = zip.folder('PersonaWeave_Results');
    let packed = 0;
    const failures: string[] = [];
    const names = jobs.find((job) => job.taskId === taskDetail?.task_id)?.targetNames ?? [];
    const usedNames = new Set<string>();
    await Promise.all(detailResults.map(async (result, index) => {
      const originalName = names[index] || `result_${index + 1}.jpg`;
      const stem = originalName.replace(/\.[^/.]+$/, '').replace(/[\\/:*?"<>|]/g, '_') || `result_${index + 1}`;
      let filename = `${stem}_swapped.jpg`;
      for (let suffix = 2; usedNames.has(filename); suffix += 1) filename = `${stem}_swapped_${suffix}.jpg`;
      usedNames.add(filename);
      try {
        const response = await fetch(result.processed_url!);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const blob = await response.blob();
        if (!blob.size) throw new Error('空文件');
        folder?.file(filename, blob);
        packed += 1;
      } catch (error) {
        failures.push(`${originalName}: ${error instanceof Error ? error.message : '下载失败'}`);
      }
    }));
    try {
      if (!packed) throw new Error('所有结果下载均失败');
      if (failures.length) folder?.file('FAILED_DOWNLOADS.txt', failures.join('\n'));
      const content = await zip.generateAsync({ type: 'blob' });
      saveAs(content, `PersonaWeave_Batch_Swapped_${Date.now()}.zip`);
      if (failures.length) toast({ title: `已导出 ${packed} 张，${failures.length} 张失败`, description: 'ZIP 内含失败清单。', kind: 'info' });
    } catch (error) {
      toast({ title: 'ZIP 导出失败', description: error instanceof Error ? error.message : '请重试', kind: 'error' });
    } finally {
      setIsExporting(false);
    }
  };

  const submittedIds = new Set(submittedTargetIdsRef.current);
  const resultItems = (taskDetail?.results ?? []).filter((item) => submittedIds.has(item.client_target_id));
  const completedCount = resultItems.filter((item) => item.status === 'SUCCESS' || item.status === 'PARTIAL').length;
  const failedCount = resultItems.filter((item) => item.status === 'FAILED').length;
  const totalCount = activeTaskId && submittedIds.size > 0 ? submittedIds.size : targetImages.length;
  const mappingStatus = resolveExecutionMappings({ sourcePeople, sourceGallery, targetImages, personMappings });

  /* Derived progress bar width */
  const progressWidth = `${batchProgress}%`;

  return (
    <div className="shrink-0">
      {queryError && (
        <div role="alert" className="flex items-center justify-between gap-3 border-t border-err/30 bg-err/10 px-4 py-2 text-xs text-err">
          <span className="min-w-0 truncate">{queryError}</span>
          {activeTaskId && (
            <button
              className="btn btn-sm btn-outline shrink-0"
              onClick={() => {
                setQueryError(null);
                updateJob(activeTaskId, { status: 'PROCESSING', error: undefined });
                setIsProcessing(true);
                void taskQuery.refetch();
              }}
            >
              <RotateCw className="h-3 w-3" /> 重试连接
            </button>
          )}
        </div>
      )}
      <footer className="min-h-14 max-h-32 overflow-x-auto glass border-t border-border px-2 py-2 sm:px-4 flex flex-wrap items-center gap-2 sm:gap-4 z-40 select-none shrink-0">
      {/* ── Run button ── */}
      <button
        onClick={() => setIsMatrixOpen(true)}
        disabled={totalCount === 0 || isProcessing}
        className="btn btn-md btn-outline shrink-0 justify-center gap-1.5"
        title="配置整个批次任务的人物身份映射"
      >
        <Users className="w-3.5 h-3.5" />
        <span>选择谁替换谁{personMappings.length ? ` (${personMappings.length})` : ''}</span>
      </button>

      <button
        onClick={handleStartBatchSwap}
        disabled={totalCount === 0 || sourceGallery.length === 0 || isProcessing}
        className="btn btn-md btn-primary shrink-0 min-w-[132px] justify-center"
      >
        {isProcessing ? (
          <>
            <Zap className="w-3.5 h-3.5 animate-spin" />
            <span>处理中 {batchProgress}%</span>
          </>
        ) : (
          <>
            <Play className="w-3.5 h-3.5 fill-white" />
            <span>批量换脸 ({totalCount} 张)</span>
          </>
        )}
      </button>

      {/* ── Progress section ── */}
      <div className="min-w-[180px] flex-1 hidden md:flex flex-col justify-center gap-1.5">
        {/* Status row */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-2xs text-zinc-500">
            {isProcessing && (
              <Clock className="w-3 h-3 text-warn animate-spin shrink-0" />
            )}
            <span>
              {isProcessing
                ? '批量运行中…'
                : completedCount > 0
                ? `已完成 ${completedCount} / ${totalCount}`
                : mappingStatus.ready ? '映射已确认，可启动' : mappingStatus.message}
            </span>
            {completedCount > 0 && !isProcessing && (
              <span className="chip chip-ok">
                <CheckCircle2 className="w-2.5 h-2.5" />
                {completedCount}
              </span>
            )}
            {failedCount > 0 && (
              <span className="chip chip-err">
                <AlertCircle className="w-2.5 h-2.5" />
                {failedCount}
              </span>
            )}
          </div>
          <span className="font-mono text-2xs text-primary font-semibold shrink-0">
            {batchProgress}%
          </span>
        </div>

        {/* Progress bar — 1-pixel height track */}
        <div className="w-full h-1.5 bg-surface-3 rounded-full overflow-hidden border border-border">
          <div
            className="h-full bg-primary rounded-full transition-width"
            style={{ width: progressWidth }}
          />
        </div>
      </div>

      {/* ── Summary & Export ── */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Task completion summary */}
        {taskSummary && (
          <div className="hidden lg:flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-surface-2 border border-border text-2xs font-medium text-zinc-400">
            <span className="text-ok font-semibold">{taskSummary.total} 张</span>
            <span className="text-zinc-700">·</span>
            <span>{taskSummary.costSeconds}s</span>
            <span className="text-zinc-700">·</span>
            <span className="text-accent font-mono">{taskSummary.avgSpeed}s/图</span>
          </div>
        )}

        {/* Download button */}
        <button
          onClick={handleDownloadAllZip}
          disabled={completedCount === 0 || isExporting}
          className="btn btn-sm btn-outline gap-1.5"
          title="将所有已完成图片打包为 ZIP 下载"
        >
          <Download className="w-3.5 h-3.5 text-accent" />
          <span className="hidden sm:inline">导出 ZIP</span>
        </button>
      </div>
      </footer>
    </div>
  );
};
