import type { ServerHealth } from '@/types';

/** Explain the shared local slot, including a cancelled request still draining native work. */
export function localInferenceStatus(health: ServerHealth | null) {
  const activity = health?.local_inference?.active[0];
  if (!activity) return null;
  if (activity.phase === 'waiting_memory') return activity.waiting_reason || '本地任务正在等待可用内存';
  if (activity.phase === 'finishing_cancelled_request')
    return `上次请求已取消，仍在等待本地运算结束（${Math.floor(activity.elapsed_seconds)} 秒），后续图片暂时排队`;
  const labels: Record<string, string> = {
    face_detection: '检测人脸',
    face_clustering: '分析跨图人物',
    cluster_snapshot: '读取人物分析图片',
    source_preparation: '分析人物参考素材',
    batch_swap: '处理当前批量图片',
    single_swap: '执行单图换脸',
    refinement_snapshot: '保存精修图片',
    refinement_thumbnail: '生成精修缩略图',
    refinement_turn: '处理 AI 精修，换脸将在其结束后继续',
  };
  return `本地引擎正在${labels[activity.operation] || '运算'}（${Math.floor(activity.elapsed_seconds)} 秒）${health?.local_inference?.queued ? `，另有 ${health.local_inference.queued} 项等待` : ''}`;
}
