import {
  CheckCircleFilled,
  DownloadOutlined,
  ExperimentOutlined,
  EyeOutlined,
  LoadingOutlined,
  PlayCircleFilled,
  WarningFilled,
} from '@ant-design/icons'
import {
  Alert,
  App as AntApp,
  Button,
  Collapse,
  Empty,
  Image,
  Input,
  InputNumber,
  Progress,
  Segmented,
  Skeleton,
  Tag,
  Tooltip,
} from 'antd'
import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useMemo, useState } from 'react'

import { createFacePlan, createPreview } from '../api'
import type { PreviewIssue, PreviewReport, ReviewScores } from '../types'
import { FileDrop } from './FileDrop'

const { TextArea } = Input

type ResultMode = 'result' | 'compare' | 'face'

const scoreLabels: Array<[keyof ReviewScores, string]> = [
  ['instruction_fulfillment', '需求完成'],
  ['target_identity_consistency', '人物一致'],
  ['face_naturalness', '面部自然'],
  ['pose_expression_preservation', '姿态表情'],
  ['source_content_preservation', '原图保持'],
  ['text_logo_preservation', '文字 Logo'],
]

function useBlobUrl(blob?: Blob) {
  const [url, setUrl] = useState<string>()
  useEffect(() => {
    if (!blob) {
      setUrl(undefined)
      return
    }
    const next = URL.createObjectURL(blob)
    setUrl(next)
    return () => URL.revokeObjectURL(next)
  }, [blob])
  return url
}

function stopLabel(reason?: string) {
  return {
    review_passed: '复核通过',
    max_iterations_reached: '达到轮次上限',
    review_unavailable: '复核中止',
    review_incomplete: '提示词不完整',
  }[reason ?? ''] ?? '流程结束'
}

function severityColor(severity: PreviewIssue['severity']) {
  return severity === 'critical' ? 'error' : severity === 'major' ? 'warning' : 'default'
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

export function QuickEdit() {
  const { message } = AntApp.useApp()
  const [source, setSource] = useState<File>()
  const [reference, setReference] = useState<File>()
  const [description, setDescription] = useState('同一位30岁东亚女性，短发，自然微笑，真实摄影质感')
  const [maxIterations, setMaxIterations] = useState(3)
  const [resultBlob, setResultBlob] = useState<Blob>()
  const [facePlanBlob, setFacePlanBlob] = useState<Blob>()
  const [report, setReport] = useState<PreviewReport | null>(null)
  const [needsReview, setNeedsReview] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [planning, setPlanning] = useState(false)
  const [mode, setMode] = useState<ResultMode>('result')

  const resultUrl = useBlobUrl(resultBlob)
  const facePlanUrl = useBlobUrl(facePlanBlob)
  const sourceUrl = useBlobUrl(source)
  const review = report?.multimodal_review
  const scores = review?.scores

  useEffect(() => {
    setResultBlob(undefined)
    setFacePlanBlob(undefined)
    setReport(null)
    setNeedsReview(false)
    setMode('result')
  }, [source, reference])

  const runPreview = async () => {
    if (!source) {
      message.warning('请先选择原图')
      return
    }
    if (!description.trim()) {
      message.warning('请输入替换要求')
      return
    }
    setProcessing(true)
    setResultBlob(undefined)
    setReport(null)
    try {
      const result = await createPreview({
        source,
        reference,
        description: description.trim(),
        maxIterations,
      })
      setResultBlob(result.blob)
      setReport(result.report)
      setNeedsReview(result.needsReview)
      setMode('result')
      message.success(result.needsReview ? '处理完成，请检查结果' : '处理完成')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '处理失败')
    } finally {
      setProcessing(false)
    }
  }

  const runFacePlan = async () => {
    if (!source) return
    setPlanning(true)
    try {
      const result = await createFacePlan(source)
      setFacePlanBlob(result.blob)
      setMode('face')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '区域分析失败')
    } finally {
      setPlanning(false)
    }
  }

  const resultOptions = useMemo<Array<{ label: string; value: ResultMode; disabled: boolean }>>(
    () => [
      { label: '结果', value: 'result', disabled: !resultUrl },
      { label: '对比', value: 'compare', disabled: !resultUrl || !sourceUrl },
      { label: '面部区域', value: 'face', disabled: !facePlanUrl },
    ],
    [facePlanUrl, resultUrl, sourceUrl],
  )

  return (
    <motion.div className="quick-editor" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
      <aside className="assets-panel">
        <div className="panel-heading">
          <div><span className="panel-kicker">Assets</span><strong>输入素材</strong></div>
          <Tag bordered={false}>2</Tag>
        </div>
        <div className="panel-tab-strip"><button className="active">素材</button><button>记录</button></div>
        <div className="asset-panel-scroll">
          <FileDrop title="原图" hint="拖入待编辑图片" file={source} onChange={setSource} disabled={processing} />
          <FileDrop title="人物参考图" hint="用于保持人物外观一致" file={reference} onChange={setReference} optional disabled={processing} />
          <div className="asset-panel-section">
            <div className="inspector-heading"><span>分析</span></div>
            <Tooltip title="查看本地识别的面部范围，不参与默认编辑流程">
              <Button block icon={<ExperimentOutlined />} disabled={!source || processing} loading={planning} onClick={runFacePlan}>
                生成面部区域预览
              </Button>
            </Tooltip>
          </div>
        </div>
      </aside>

      <section className="canvas-section">
        <div className="canvas-header">
          <div className="canvas-breadcrumb"><span>页面 1</span><b>/</b><strong>{resultUrl ? '处理结果' : source ? source.name : '未命名画布'}</strong></div>
          <div className="canvas-meta">{report?.model ?? 'GPT Image 2'} · {report?.completed_iterations ?? 0} 轮</div>
        </div>
        <div className="design-canvas">
          <div className="canvas-grid" />
          {processing ? (
            <motion.div className="processing-stage floating-process" initial={{ opacity: 0, scale: .96 }} animate={{ opacity: 1, scale: 1 }}>
              <div className="processing-orbit"><LoadingOutlined /></div>
              <strong>正在生成与复核</strong>
              <span>达到标准后会提前停止</span>
              <Skeleton active paragraph={{ rows: 3 }} />
            </motion.div>
          ) : !sourceUrl ? (
            <div className="blank-artboard"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="从左侧添加原图" /></div>
          ) : (
            <AnimatePresence mode="wait">
              {mode === 'result' && (
                <motion.div key={resultUrl ? 'result' : 'source'} className="canvas-artboard" initial={{ opacity: 0, scale: .97 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}>
                  <span className="artboard-name">{resultUrl ? 'Result' : 'Source'}</span>
                  <Image src={resultUrl ?? sourceUrl} alt={resultUrl ? '处理结果' : '原图'} preview={{ mask: <><EyeOutlined /> 查看大图</> }} />
                </motion.div>
              )}
              {mode === 'compare' && resultUrl && (
                <motion.div key="compare" className="canvas-compare" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                  <Image.PreviewGroup>
                    <div className="canvas-artboard"><span className="artboard-name">Source</span><Image src={sourceUrl} alt="原图" /></div>
                    <div className="canvas-artboard"><span className="artboard-name">Result</span><Image src={resultUrl} alt="处理结果" /></div>
                  </Image.PreviewGroup>
                </motion.div>
              )}
              {mode === 'face' && facePlanUrl && (
                <motion.div key="face" className="canvas-artboard" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                  <span className="artboard-name">Face regions</span>
                  <Image src={facePlanUrl} alt="面部区域预览" />
                </motion.div>
              )}
            </AnimatePresence>
          )}

          <div className="floating-canvas-toolbar">
            <Segmented<ResultMode> options={resultOptions} value={mode} onChange={setMode} />
            <span className="toolbar-divider" />
            <Button type="text" icon={<DownloadOutlined />} disabled={!resultBlob} onClick={() => resultBlob && downloadBlob(resultBlob, `personaweave-${Date.now()}.png`)}>
              导出
            </Button>
          </div>

          <AnimatePresence>
            {report && (
              <motion.div className="canvas-status" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                {needsReview ? <WarningFilled /> : <CheckCircleFilled />}
                <span>{report.completed_iterations ?? 1} 轮 · {stopLabel(report.stopped_reason)}</span>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </section>

      <aside className="properties-panel">
        <div className="properties-tabs"><button className="active">编辑</button><button>复核</button><button>导出</button></div>
        <div className="properties-scroll">
          <div className="property-section">
            <div className="inspector-heading"><span>模型</span><Tag bordered={false} color="processing">GPT Image 2</Tag></div>
            <div className="property-row"><span>模式</span><strong>需求锚定闭环</strong></div>
          </div>

          <div className="property-section">
            <div className="inspector-heading"><span>替换要求</span><small>{description.length}/2000</small></div>
            <TextArea
              value={description}
              disabled={processing}
              maxLength={2000}
              autoSize={{ minRows: 6, maxRows: 12 }}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="描述要替换的对象和目标人物特征"
            />
          </div>

          <div className="property-section">
            <div className="inspector-heading"><span>生成</span></div>
            <div className="property-control-row">
              <span>最大循环</span>
              <InputNumber min={1} max={10} value={maxIterations} disabled={processing} addonAfter="轮" onChange={(value) => setMaxIterations(value ?? 3)} />
            </div>
            <div className="property-row"><span>停止条件</span><strong>复核通过或达到上限</strong></div>
          </div>

          {report && (
            <div className="property-section">
              <Alert
                showIcon
                type={needsReview ? 'warning' : 'success'}
                icon={needsReview ? <WarningFilled /> : <CheckCircleFilled />}
                message={`${report.completed_iterations ?? 1} 轮完成`}
                description={review?.summary}
              />
            </div>
          )}

          {scores && (
            <div className="property-section review-property-section">
              <div className="inspector-heading"><span>复核评分</span><Tag color={review?.passed ? 'success' : 'warning'}>{review?.passed ? '通过' : '待检查'}</Tag></div>
              <div className="inspector-score-list">
                {scoreLabels.map(([key, label]) => (
                  <div className="score-item" key={key}>
                    <div><span>{label}</span><strong>{scores[key] ?? '—'}</strong></div>
                    <Progress percent={scores[key] ?? 0} showInfo={false} size="small" strokeColor={(scores[key] ?? 0) >= 85 ? '#14ae5c' : '#f59e0b'} />
                  </div>
                ))}
              </div>
              {!!review?.issues?.length && (
                <Collapse ghost size="small" items={[{
                  key: 'issues',
                  label: `${review.issues.length} 项待检查`,
                  children: <div className="issue-list">{review.issues.map((issue, index) => (
                    <div className="issue-item" key={`${issue.category}-${index}`}><Tag color={severityColor(issue.severity)}>{issue.category}</Tag><span>{issue.evidence}</span></div>
                  ))}</div>,
                }]} />
              )}
            </div>
          )}
        </div>
        <div className="property-footer">
          <Button className="primary-run-button" type="primary" icon={processing ? <LoadingOutlined /> : <PlayCircleFilled />} loading={processing} disabled={!source} onClick={runPreview}>
            {processing ? `处理中 · 最多 ${maxIterations} 轮` : '开始编辑'}
          </Button>
          <Button icon={<DownloadOutlined />} disabled={!resultBlob} onClick={() => resultBlob && downloadBlob(resultBlob, `personaweave-${Date.now()}.png`)} />
        </div>
      </aside>
    </motion.div>
  )
}
