import React from 'react';
import type { PersonSwapOutcome } from '@/types';

const number = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(3) : '未知';
const index = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? String(value) : '未知';

const reasons: Record<string, string> = {
  visible_change_confirmed: '已检测到可见像素变化',
  no_visible_change: '换脸后可见变化不足',
  identity_match_below_threshold: '身份匹配低于阈值',
  assignment_conflict: '一对一人物分配冲突',
  target_reference_unavailable: '目标参考特征不可用',
  source_identity_unavailable: '换入素材特征不可用',
  extreme_pose_without_matching_reference: '缺少匹配姿态的侧脸素材',
};

/** Read-only evidence; absent legacy fields must not imply zero or successful validation. */
export const PersonOutcomeDiagnostics: React.FC<{ outcomes?: PersonSwapOutcome[] | null }> = ({ outcomes }) => {
  if (!outcomes?.length)
    return <p className="text-xs text-zinc-500">暂无逐人诊断（旧记录或处理未完成，不能推断未检测到人脸）。</p>;
  return (
    <section aria-label="逐人诊断" className="space-y-2">
      <p className="text-2xs text-zinc-500">
        相似度、遮罩均值和混合权重是处理证据，不是自然度评分或身份验证。素材仅显示索引及组合，不代表单张照片。
      </p>
      {outcomes.map((outcome, position) => {
        const diagnostics = outcome.diagnostics;
        const matching = diagnostics?.matching;
        const source = diagnostics?.source_selection;
        const mask = diagnostics?.mask;
        return (
          <details key={position} className="rounded-lg border border-border bg-surface-1 p-3">
            <summary className="cursor-pointer text-xs text-zinc-300">
              人物 #{outcome.person_index ?? '未知'} · {outcome.status ?? '未知状态'} ·{' '}
              {reasons[outcome.reason] ?? outcome.reason ?? '原因未知'}
            </summary>
            <div className="mt-2 space-y-2 text-xs text-zinc-400">
              <p>
                原因代码：{outcome.reason ?? '未知'}
                {outcome.model ? ' · 模型：' + outcome.model : ''}
              </p>
              {!diagnostics || (!matching && !source && !mask) ? (
                <p>暂无诊断（旧记录未保存详细证据）。</p>
              ) : (
                <>
                  {matching && (
                    <div className="space-y-1">
                      <p className="text-zinc-200">匹配证据</p>
                      <p>
                        最佳相似度 {number(matching.best_similarity)} · 分配相似度{' '}
                        {number(matching.assigned_similarity)} · 阈值 {number(matching.threshold)}
                      </p>
                      <p>当前图人脸 #{index(matching.face_index)}（从左到右，非跨图人物编号）</p>
                    </div>
                  )}
                  {source && (
                    <div className="space-y-1">
                      <p className="text-zinc-200">素材组合</p>
                      <p>
                        模式 {source.mode ?? '未知'} · 参考数量 {index(source.reference_count)} · 最佳参考 #
                        {index(source.selected_reference_index)}
                      </p>
                      <p>
                        聚合权重 {number(source.gallery_weight)} · 最佳单张系数 {number(source.best_reference_weight)}
                        （非身份相似度）
                      </p>
                      <p className="break-all">
                        聚合内参考权重：
                        {source.gallery_weights_known !== false && source.gallery_reference_weights?.length
                          ? source.gallery_reference_weights
                              .map((weight, i) => '#' + (i + 1) + ' ' + number(weight))
                              .join(' / ')
                          : '未知（未记录或外部聚合输入）'}
                      </p>
                    </div>
                  )}
                  {mask && (
                    <div className="space-y-1">
                      <p className="text-zinc-200">遮罩与融合</p>
                      <p>
                        请求强度 {number(mask.requested_strength)} · 交集均值 {number(mask.intersection_mean)} ·
                        脸框平均换入权重 {number(mask.bbox_alpha_mean)}
                      </p>
                      {mask.components?.length ? (
                        <ul className="space-y-1">
                          {mask.components.map((component, i) => (
                            <li key={i}>
                              {component.name} · {component.status} · 均值 {number(component.mean)}
                              {component.failure_code ? ' · 失败代码 ' + component.failure_code : ''}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p>暂无遮罩组件诊断</p>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </details>
        );
      })}
    </section>
  );
};
