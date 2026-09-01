import React, { useEffect, useRef } from 'react';
import type { DetectedFaceBox, RefinementPersonProgress, RefinementTaskData } from '../../types';

const scoreLabel = (key: string) => ({
  overall_score: '综合质量',
  coherence_score: '整体协调',
  naturalness_score: '自然度',
}[key] ?? key);

export const RefinementFaceCrop: React.FC<{ imageUrl: string; face: DetectedFaceBox }> = ({ imageUrl, face }) => {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const image = new Image();
    image.crossOrigin = 'anonymous';
    image.onload = () => {
      const context = canvas.getContext('2d');
      if (!context) return;
      const [x1, y1, x2, y2] = face.bbox;
      const width = Math.max(1, x2 - x1);
      const height = Math.max(1, y2 - y1);
      const margin = Math.max(width, height) * 0.28;
      const sourceX = Math.max(0, x1 - margin);
      const sourceY = Math.max(0, y1 - margin);
      canvas.width = 72;
      canvas.height = 72;
      context.drawImage(
        image,
        sourceX,
        sourceY,
        Math.min(image.width - sourceX, width + margin * 2),
        Math.min(image.height - sourceY, height + margin * 2),
        0,
        0,
        72,
        72,
      );
    };
    image.src = imageUrl;
  }, [imageUrl, face]);

  return <canvas ref={ref} className="h-12 w-12 rounded-xl border border-cyan-500/30 bg-black" />;
};

export const RefinementPersonReport: React.FC<{ value?: RefinementPersonProgress }> = ({ value }) => {
  if (!value) return null;
  const report = value.report ?? {};
  const scores = Object.entries(report).filter(([key, item]) => key.endsWith('_score') && typeof item === 'number');
  const issues = Array.isArray(report.issues) ? report.issues.map(String) : [];

  return <div className="mt-2 space-y-2 text-xs text-zinc-400">
    {scores.length > 0 && <div className="grid grid-cols-2 gap-1.5">{scores.map(([key, score]) => <div key={key} className="flex items-center justify-between rounded-lg border border-border bg-surface-3 px-2 py-1.5"><span>{scoreLabel(key)}</span><span className="font-mono font-semibold text-zinc-200">{String(score)}</span></div>)}</div>}
    {issues.length > 0 && <div className="rounded-lg border border-warn-border bg-warn-muted p-2"><div className="mb-1 font-medium text-warn">仍需关注</div><ul className="space-y-1 text-zinc-300">{issues.map((issue, index) => <li key={`${issue}-${index}`}>· {issue}</li>)}</ul></div>}
    {value.error && <p role="alert" className="text-err">{value.error}</p>}
    {value.report && <details className="rounded-lg border border-border bg-black/20 p-2"><summary className="cursor-pointer text-zinc-500">技术详情</summary><pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap text-2xs text-zinc-500">{JSON.stringify(value.report, null, 2)}</pre></details>}
  </div>;
};

export const RefinementFinalReview: React.FC<{ task: RefinementTaskData | null }> = ({ task }) => {
  const review = task?.final_review;
  if (!review) return null;
  const scores: Array<[string, number]> = [
    ['overall_score', review.overall_score],
    ['coherence_score', review.coherence_score],
    ['naturalness_score', review.naturalness_score],
  ];
  const recommendation = {
    accept: '建议接受',
    review: '建议人工仔细检查',
    regenerate: '建议重新生成',
  }[review.recommendation];

  return <div className="mt-3 rounded-xl border border-primary-border bg-primary-muted p-3 text-xs"><div className="flex items-center justify-between gap-2"><div className="font-semibold text-primary">整图最终复评（建议）</div><span className="chip chip-neutral">{recommendation}</span></div><p className="mt-2 leading-relaxed text-zinc-300">{review.summary}</p>{review.review_error && <div className="mt-2 rounded-lg border border-warn-border bg-warn-muted p-2 text-warn">VLM 复评暂不可用：{review.review_error}。候选仍可由你直接检查和决定。</div>}<div className="mt-2 grid grid-cols-2 gap-1.5">{scores.map(([key, score]) => <div key={key} className="flex justify-between rounded-lg border border-border bg-black/15 px-2 py-1.5"><span className="text-zinc-400">{scoreLabel(key)}</span><span className="font-mono text-zinc-100">{score}</span></div>)}</div><div className="mt-2 flex flex-wrap gap-1.5">{review.unrequested_changes_detected && <span className="chip chip-warn">检测到非请求变化</span>}{review.new_high_severity_issue && <span className="chip chip-err">检测到新严重问题</span>}</div>{review.issues.length > 0 && <div className="mt-2 rounded-lg border border-warn-border bg-warn-muted p-2"><div className="font-medium text-warn">复评关注项</div><ul className="mt-1 space-y-1 text-zinc-300">{review.issues.map((issue, index) => <li key={index}>· {issue}</li>)}</ul></div>}<p className="mt-2 text-2xs text-zinc-400">该报告仅供参考；请以整图对比结果作最终决定。</p></div>;
};
