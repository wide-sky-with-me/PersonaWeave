import {
  CheckCircleFilled,
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
  LoadingOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RetweetOutlined,
  WarningFilled,
} from '@ant-design/icons'
import {
  Alert,
  App as AntApp,
  Avatar,
  Badge,
  Button,
  Card,
  Checkbox,
  Col,
  Empty,
  Image,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Popover,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Steps,
  Tag,
  Typography,
  Upload,
  type UploadFile,
} from 'antd'
import { motion } from 'motion/react'
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

import { apiJson, personaApi, postJson, upload } from '../api'
import { fileFromUpload, IMAGE_FILE_ACCEPT, isSupportedImageFile } from '../imageFiles'
import { requiredManualMaskDigests, type Asset, type Batch, type BatchProgress, type Identity, type Job, type Person } from '../types'
import { JobFailure } from './JobFailure'

const { Text } = Typography

const statusLabels: Record<string, string> = {
  draft: '准备素材',
  analyzing: '分析中',
  awaiting_confirmation: '确认人物',
  ready: '待替换',
  swapping: '替换中',
  validating: '质检中',
  review: '待审核',
  completed: '已完成',
  failed: '失败',
}

const stageLabels: Record<string, string> = {
  analyze: '人物分析',
  swap: '人物编辑',
  validate: '结果质检',
}

const jobStatusLabels: Record<string, string> = {
  queued: '排队中',
  running: '执行中',
  succeeded: '已完成',
  failed: '失败',
  needs_review: '待复核',
}

const phaseLabels: Record<string, string> = {
  queued: '等待执行',
  analyzing: '人物分析',
  planning: '编辑规划',
  generating: '云端生图',
  reviewing: '结果复核',
  materializing: '保存结果',
  validating: '一致性校验',
  completed: '处理完成',
  needs_review: '需要人工检查',
  failed: '处理失败',
}

const reviewReasonLabels: Record<string, string> = {
  reference_insufficiency: '目标参考视角不足',
  mask_uncertain: '授权蒙版边界不确定',
  dual_review_failure: '双模型复核未通过',
}

const attributeLabels: Record<string, string> = {
  face_identity: '人物身份',
  facial_geometry: '面部结构',
  hair: '发型',
  eyewear: '眼镜',
  accessories: '配饰',
  makeup: '妆容',
  expression: '表情',
}

function evidenceLabel(value: string) {
  return reviewReasonLabels[value] ?? value
}

function checkValueLabel(value: boolean | number | string) {
  if (value === true) return '通过'
  if (value === false) return '未通过'
  return String(value)
}

function jobProgressPercent(job: Job) {
  if (['succeeded', 'failed', 'needs_review'].includes(job.status)) return 100
  if (job.phase === 'queued') return 0
  if (job.phase === 'planning') return 8
  if (job.phase === 'materializing') return 95
  if (job.phase === 'analyzing' || job.phase === 'validating') return 50
  const total = Math.max(job.total_iterations || 1, 1)
  const iteration = Math.max(job.current_iteration || 1, 1)
  const roundWidth = 84 / total
  const phaseOffset = job.phase === 'reviewing' ? 0.82 : 0.35
  return Math.min(92, Math.round(8 + (iteration - 1 + phaseOffset) * roundWidth))
}

const stepKeys = ['upload', 'analysis', 'task', 'jobs', 'results'] as const

function statusColor(status: string) {
  if (status === 'completed' || status === 'succeeded') return 'success'
  if (status === 'failed') return 'error'
  if (status === 'needs_review' || status === 'review') return 'warning'
  if (status === 'running' || status === 'analyzing' || status === 'swapping' || status === 'validating') return 'processing'
  return 'default'
}

type BatchWorkspaceProps = {
  batchId?: string
  onChanged: () => Promise<void> | void
}

type ExecutionResult = {
  completed?: boolean
  requires_review?: boolean
}

export function BatchWorkspace({ batchId, onChanged }: BatchWorkspaceProps) {
  const { message } = AntApp.useApp()
  const [batch, setBatch] = useState<Batch>()
  const [progress, setProgress] = useState<BatchProgress>()
  const [loading, setLoading] = useState(false)
  const [acting, setActing] = useState(false)
  const [sourceFiles, setSourceFiles] = useState<UploadFile[]>([])
  const [removingAssetId, setRemovingAssetId] = useState<string>()
  const [removingIdentityId, setRemovingIdentityId] = useState<string>()
  const [identityLabel, setIdentityLabel] = useState('')
  const [taskInstruction, setTaskInstruction] = useState('')
  const [maxIterations, setMaxIterations] = useState(3)
  const [selectedPersonIds, setSelectedPersonIds] = useState<string[]>([])
  const [manualFaceAsset, setManualFaceAsset] = useState<Asset>()
  const [activeStep, setActiveStep] = useState<(typeof stepKeys)[number]>('upload')
  const [acknowledgedMaskSha256s, setAcknowledgedMaskSha256s] = useState<string[]>([])
  const promptEditorRef = useRef<HTMLDivElement>(null)
  const promptSelectionRef = useRef({ start: 0, end: 0 })
  const onChangedRef = useRef(onChanged)

  useEffect(() => {
    onChangedRef.current = onChanged
  }, [onChanged])

  useEffect(() => {
    setAcknowledgedMaskSha256s([])
  }, [batchId, batch?.contract_hash])

  const refresh = useCallback(async (silent = false) => {
    if (!batchId) return
    if (!silent) setLoading(true)
    try {
      const [nextBatch, nextProgress] = await Promise.all([
        personaApi.batch(batchId),
        personaApi.progress(batchId),
      ])
      setBatch(nextBatch)
      setTaskInstruction(nextBatch.task_instruction ?? '')
      setMaxIterations(nextBatch.max_iterations ?? 3)
      setProgress(nextProgress)
    } catch (error) {
      if (!silent) message.error(error instanceof Error ? error.message : '批次加载失败')
    } finally {
      if (!silent) setLoading(false)
    }
  }, [batchId, message])

  async function waitForExecution<T>(request: Promise<T>): Promise<T> {
    void refresh(true)
    const poller = window.setInterval(() => {
      void Promise.all([refresh(true), Promise.resolve(onChangedRef.current())])
    }, 2000)
    try {
      return await request
    } finally {
      window.clearInterval(poller)
      await Promise.all([refresh(true), Promise.resolve(onChangedRef.current())])
    }
  }

  useEffect(() => {
    setSelectedPersonIds([])
    void refresh()
  }, [refresh])

  useEffect(() => {
    if (!batch || !['analyzing', 'swapping', 'validating'].includes(batch.status)) return
    const poll = window.setInterval(() => {
      void Promise.all([refresh(true), Promise.resolve(onChangedRef.current())])
    }, 2000)
    return () => window.clearInterval(poll)
  }, [batch?.status, refresh])

  useEffect(() => {
    if (!batch) return
    if (batch.status === 'draft') {
      setActiveStep('upload')
    } else if (['analyzing', 'awaiting_confirmation'].includes(batch.status)) {
      setActiveStep('analysis')
    } else if (batch.status === 'ready') {
      setActiveStep('task')
    } else if (batch.assets.some((asset) => asset.kind === 'result')) {
      setActiveStep('results')
    } else {
      setActiveStep('jobs')
    }
  }, [batch?.id, batch?.status])

  const runAction = async (path: string, success: string) => {
    setActing(true)
    try {
      const result = await waitForExecution(postJson<ExecutionResult>(path))
      if (result.requires_review) {
        message.warning('已生成候选结果，请进入结果验收检查')
        setActiveStep('results')
        await Promise.all([refresh(), onChanged()])
        return
      }
      if (result.completed === false) throw new Error('部分任务执行失败，请在执行记录中重试')
      message.success(success)
      await Promise.all([refresh(), onChanged()])
    } catch (error) {
      message.error(error instanceof Error ? error.message : '操作失败')
      await refresh()
    } finally {
      setActing(false)
    }
  }

  const uploadSources = async () => {
    if (!batchId || !sourceFiles.length) return
    const files = sourceFiles.map(fileFromUpload)
    if (files.some((file) => !file)) {
      message.error('部分图片未能读取，请移除后重新选择')
      return
    }
    setActing(true)
    try {
      for (const file of files) {
        await upload(`/batches/${batchId}/images`, 'image', file as File)
      }
      setSourceFiles([])
      message.loading({ content: '素材已上传，正在提取并融合人物…', key: 'upload-analysis', duration: 1.5 })
      const result = await postJson<{ completed?: boolean }>(`/batches/${batchId}/analysis/run`)
      if (result.completed === false) throw new Error('部分素材分析失败，请在执行记录中重试')
      message.success({ content: `已分析 ${files.length} 张素材并完成人物融合`, key: 'upload-analysis' })
      await Promise.all([refresh(), onChanged()])
    } catch (error) {
      message.destroy('upload-analysis')
      message.error(error instanceof Error ? error.message : '上传或人物分析失败')
      await Promise.all([refresh(), onChanged()])
    } finally {
      setActing(false)
    }
  }

  const createIdentity = async () => {
    if (!batchId || !identityLabel.trim()) return
    try {
      await postJson(`/batches/${batchId}/identities`, { label: identityLabel.trim() })
      setIdentityLabel('')
      message.success('目标人物已添加')
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '创建失败')
    }
  }

  const uploadReference = async (identityId: string, file: File) => {
    if (!batchId) return
    setActing(true)
    try {
      await upload(`/batches/${batchId}/identities/${identityId}/references`, 'image', file)
      message.success('参考图已添加')
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '上传失败')
    } finally {
      setActing(false)
    }
  }

  const removeIdentity = async (identity: Identity) => {
    if (!batchId) return
    const mentionedCodes = new Set(
      (taskInstruction.toUpperCase().match(/@[IPTRO]\d+\b/g) ?? []).map((code) => code.slice(1)),
    )
    const ownedCodes = [identity.display_code, ...identity.references.map((reference) => reference.display_code)]
    const referencedCodes = ownedCodes.filter((code) => mentionedCodes.has(code))
    if (referencedCodes.length) {
      message.warning(`任务要求仍引用 ${referencedCodes.map((code) => `@${code}`).join('、')}，请先移除引用`)
      return
    }
    setRemovingIdentityId(identity.id)
    try {
      await apiJson(`/batches/${batchId}/identities/${identity.id}`, { method: 'DELETE' })
      message.success(`已移除 @${identity.display_code} 及其参考图`)
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '目标人物移除失败')
    } finally {
      setRemovingIdentityId(undefined)
    }
  }

  const removeAsset = async (asset: Asset) => {
    if (!batchId) return
    setRemovingAssetId(asset.id)
    try {
      await apiJson(`/batches/${batchId}/assets/${asset.id}`, { method: 'DELETE' })
      message.success(`已移除 ${asset.filename}`)
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '移除失败')
    } finally {
      setRemovingAssetId(undefined)
    }
  }

  const saveTaskSpec = async (showSuccess = true) => {
    if (!batchId) return false
    try {
      await apiJson(`/batches/${batchId}/spec`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_instruction: taskInstruction.trim(), max_iterations: maxIterations }),
      })
      if (showSuccess) message.success('任务要求已保存')
      await Promise.all([refresh(), onChanged()])
      return true
    } catch (error) {
      message.error(error instanceof Error ? error.message : '任务要求保存失败')
      return false
    }
  }

  const compileTaskContract = async () => {
    if (!batchId || !taskInstruction.trim()) {
      message.warning('请先填写本次人物编辑的任务要求')
      return
    }
    setActing(true)
    try {
      if (!await saveTaskSpec(false)) return
      await postJson(`/batches/${batchId}/task/compile`)
      message.success('正式约束已编译，请检查证据并确认')
      await Promise.all([refresh(), onChanged()])
    } catch (error) {
      message.error(error instanceof Error ? error.message : '正式约束编译失败')
      await refresh()
    } finally {
      setActing(false)
    }
  }

  const confirmTaskContract = async () => {
    if (!batchId || !batch?.contract_hash || !batch.compiled_contract) {
      message.warning('请先编译当前任务的正式约束')
      return
    }
    const requiredDigests = requiredManualMaskDigests(batch.compiled_contract)
    const missingDigests = requiredDigests.filter((digest) => !acknowledgedMaskSha256s.includes(digest))
    if (missingDigests.length) {
      message.warning('请逐项确认所有边界不确定的授权蒙版')
      return
    }
    setActing(true)
    try {
      await postJson(`/batches/${batchId}/task/confirm`, {
        contract_hash: batch.contract_hash,
        acknowledged_mask_sha256s: requiredDigests,
      })
      message.success('正式约束已确认，可以开始任务循环')
      await Promise.all([refresh(), onChanged()])
    } catch (error) {
      message.error(error instanceof Error ? error.message : '正式约束确认失败')
      await refresh()
    } finally {
      setActing(false)
    }
  }

  const mergeSelectedPeople = async () => {
    if (!batchId || selectedPersonIds.length < 2) return
    setActing(true)
    try {
      await postJson(`/batches/${batchId}/persons/merge`, { person_ids: selectedPersonIds })
      setSelectedPersonIds([])
      message.success('人物出现记录已合并')
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '人物合并失败')
    } finally {
      setActing(false)
    }
  }

  const fusePeople = async () => {
    if (!batchId) return
    setActing(true)
    try {
      message.loading({ content: '正在跨图融合重复人物…', key: 'person-fusion', duration: 1.5 })
      const result = await postJson<{ before_count: number; after_count: number; merged_count: number }>(`/batches/${batchId}/persons/fuse`)
      setSelectedPersonIds([])
      message.success({
        content: result.merged_count
          ? `融合完成：${result.before_count} 人归并为 ${result.after_count} 人`
          : '融合完成，未发现可确认的重复人物',
        key: 'person-fusion',
      })
      await refresh()
    } catch (error) {
      message.destroy('person-fusion')
      message.error(error instanceof Error ? error.message : '人物融合失败')
      await refresh()
    } finally {
      setActing(false)
    }
  }

  const removePerson = async (person: Person) => {
    if (!batchId) return
    setActing(true)
    try {
      await apiJson(`/batches/${batchId}/persons/${person.id}`, { method: 'DELETE' })
      setSelectedPersonIds((current) => current.filter((id) => id !== person.id))
      message.success(`已移除 @${person.display_code} 的识别记录`)
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '人物移除失败')
    } finally {
      setActing(false)
    }
  }

  const splitOccurrence = async (personId: string, faceId: string) => {
    if (!batchId) return
    setActing(true)
    try {
      await postJson(`/batches/${batchId}/persons/${personId}/split`, { face_ids: [faceId] })
      message.success('该出现记录已拆分为新人物')
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '人物拆分失败')
    } finally {
      setActing(false)
    }
  }

  const rerunAnalysis = async () => {
    if (!batchId) return
    setActing(true)
    try {
      message.loading({ content: '正在重新执行多尺度检测与完整性检查…', key: 'analysis-rerun', duration: 1.5 })
      const result = await waitForExecution(
        postJson<{ completed?: boolean }>(`/batches/${batchId}/analysis/rerun`),
      )
      if (result.completed === false) throw new Error('部分素材重新分析失败，请查看任务记录')
      setSelectedPersonIds([])
      message.success({ content: '人物已重新提取并融合', key: 'analysis-rerun' })
      await Promise.all([refresh(), onChanged()])
    } catch (error) {
      message.destroy('analysis-rerun')
      message.error(error instanceof Error ? error.message : '重新分析失败')
      await refresh()
    } finally {
      setActing(false)
    }
  }

  const addManualFace = async (
    asset: Asset,
    bbox: { x: number; y: number; width: number; height: number },
    personId?: string,
  ) => {
    if (!batchId) return
    setActing(true)
    try {
      await postJson(`/batches/${batchId}/assets/${asset.id}/faces`, {
        bbox,
        person_id: personId || null,
      })
      setManualFaceAsset(undefined)
      message.success(personId ? '缺失脸已加入所选人物' : '缺失脸已创建为新人物')
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '人工补框失败')
    } finally {
      setActing(false)
    }
  }

  const startTaskLoop = async () => {
    if (!batchId || !batch) return
    if (!taskInstruction.trim()) {
      message.warning('请先填写本次人物编辑的任务要求')
      setActiveStep('task')
      return
    }
    if (!batch.contract_hash || !batch.compiled_contract) {
      message.warning('请先在任务设置中编译正式约束')
      setActiveStep('task')
      return
    }
    if (!batch.confirmed_at) {
      message.warning('请检查证据并确认当前正式约束')
      setActiveStep('task')
      return
    }
    if (
      taskInstruction.trim() !== batch.task_instruction
      || maxIterations !== batch.max_iterations
    ) {
      message.warning('任务要求已修改，请重新编译并确认正式约束')
      setActiveStep('task')
      return
    }
    setActing(true)
    try {
      message.loading({ content: '正在执行生成、视觉复核与一致性校验…', key: 'task-loop', duration: 1.5 })
      setActiveStep('jobs')
      const result = await waitForExecution(
        postJson<ExecutionResult>(`/batches/${batchId}/task/run`),
      )
      if (result.requires_review) {
        message.warning({ content: '候选结果需要人工复核', key: 'task-loop' })
        setActiveStep('results')
        await Promise.all([refresh(), onChanged()])
        return
      }
      if (result.completed === false) throw new Error('部分任务执行失败，请在执行记录中重试')
      message.success({ content: '任务循环执行完成，请检查结果', key: 'task-loop' })
      await Promise.all([refresh(), onChanged()])
    } catch (error) {
      message.destroy('task-loop')
      message.error(error instanceof Error ? error.message : '任务循环启动失败')
      await Promise.all([refresh(), onChanged()])
    } finally {
      setActing(false)
    }
  }

  const retryAndExecute = async (jobId: string, stage: string) => {
    if (!batchId) return
    if (stage !== 'analyze' && (!batch?.contract_hash || !batch.confirmed_at)) {
      message.warning('当前正式约束尚未确认，请先返回任务设置编译并确认')
      setActiveStep('task')
      return
    }
    setActing(true)
    try {
      await postJson(`/jobs/${jobId}/retry`)
      message.loading({ content: '任务已重新提交，正在执行…', key: 'job-retry', duration: 1.5 })
      const path = stage === 'analyze'
        ? `/batches/${batchId}/stages/analyze/execute`
        : `/batches/${batchId}/task/run`
      setActiveStep(stage === 'analyze' ? 'analysis' : 'jobs')
      const result = await waitForExecution(postJson<ExecutionResult>(path))
      if (result.requires_review) {
        message.warning({ content: '已生成候选结果，请人工复核', key: 'job-retry' })
        setActiveStep('results')
        await Promise.all([refresh(), onChanged()])
        return
      }
      if (result.completed === false) throw new Error('部分任务仍未完成，请查看任务记录')
      message.success({ content: '重试任务执行完成', key: 'job-retry' })
      await Promise.all([refresh(), onChanged()])
    } catch (error) {
      message.destroy('job-retry')
      message.error(error instanceof Error ? error.message : '任务重试失败')
      await Promise.all([refresh(), onChanged()])
    } finally {
      setActing(false)
    }
  }

  const primaryAction = useMemo(() => {
    if (!batch) return null
    const actions: Record<string, { label: string; path: string; success: string }> = {
      draft: { label: '分析并融合人物', path: `/batches/${batch.id}/analysis/run`, success: '人物提取与跨图融合已完成' },
      analyzing: { label: '继续人物分析', path: `/batches/${batch.id}/stages/analyze/execute`, success: '人物提取与跨图融合已完成' },
      ready: { label: '开始任务循环', path: `/batches/${batch.id}/task/run`, success: '任务循环执行完成' },
      swapping: { label: '继续任务循环', path: `/batches/${batch.id}/task/run`, success: '任务循环执行完成' },
      validating: { label: '继续任务循环', path: `/batches/${batch.id}/task/run`, success: '任务循环执行完成' },
      review: { label: '批准并完成', path: `/batches/${batch.id}/approve`, success: '批次已完成' },
    }
    return actions[batch.status] ?? null
  }, [batch])

  if (!batchId) {
    return <div className="workspace-empty"><Empty description="选择或创建一个人物编辑任务" /></div>
  }

  if (!batch && loading) {
    return <div className="workspace-empty"><Spin size="large" /></div>
  }

  if (!batch) return null

  const sources = batch.assets.filter((asset) => asset.kind === 'source')
  const results = batch.assets.filter((asset) => asset.kind === 'result')
  const assetCodeById = new Map(batch.assets.map((asset) => [asset.id, asset.display_code]))
  const failedJobs = batch.latest_jobs.filter((job) => ['failed', 'needs_review'].includes(job.status))
  const draftOnly = batch.status === 'draft'
  const analysisComplete = !['draft', 'analyzing'].includes(batch.status)
  const targetEditable = batch.status === 'awaiting_confirmation'
  const specEditable = ['awaiting_confirmation', 'ready'].includes(batch.status)
  const referenceAssets = batch.identities.flatMap((identity) => identity.references)
  const compiledContract = batch.compiled_contract
  const contractConfirmed = !!batch.contract_hash && !!batch.confirmed_at
  const taskSpecDirty = (
    taskInstruction.trim() !== batch.task_instruction
    || maxIterations !== batch.max_iterations
  )
  const requiredMaskDigests = requiredManualMaskDigests(compiledContract)
  const allRequiredMasksAcknowledged = requiredMaskDigests.every(
    (digest) => acknowledgedMaskSha256s.includes(digest),
  )
  const sourceByEvidenceKey = new Map(
    sources.flatMap((asset) => [[asset.id, asset], [asset.display_code, asset]] as const),
  )
  const referenceByEvidenceKey = new Map(
    referenceAssets.flatMap((asset) => [
      [asset.display_code, asset],
      [asset.sha256, asset],
    ] as const),
  )
  const fusionVerified = batch.persons.length > 0 && batch.persons.every(
    (person) => person.faces.every((face) => face.identity_confidence != null),
  )
  const analysisAudits = batch.latest_jobs
    .filter((job) => job.stage === 'analyze' && job.output?.detection_audit)
    .map((job) => ({
      asset: sources.find((asset) => asset.id === job.item_key),
      audit: job.output!.detection_audit!,
    }))
  const faceCountByAsset = new Map<string, number>()
  batch.persons.forEach((person) => person.faces.forEach((face) => {
    faceCountByAsset.set(face.asset_id, (faceCountByAsset.get(face.asset_id) ?? 0) + 1)
  }))
  const uncertainAudits = analysisAudits.filter(({ asset, audit }) => (
    audit.requires_manual_review
    && (
      audit.visible_face_count == null
      || !asset
      || (faceCountByAsset.get(asset.id) ?? 0) < audit.visible_face_count
    )
  ))
  const analysisVerified = (
    fusionVerified
    && analysisAudits.length === sources.length
    && uncertainAudits.length === 0
  )
  const mentionCodes = [
    ...sources.map((asset) => ({ code: asset.display_code, label: asset.filename, thumbnail: asset.content_url })),
    ...batch.persons.map((person) => ({ code: person.display_code, label: person.label || '原图人物', thumbnail: person.faces[0]?.crop_url })),
    ...batch.identities.map((identity) => ({ code: identity.display_code, label: identity.label, thumbnail: identity.references[0]?.content_url })),
    ...referenceAssets.map((asset) => ({ code: asset.display_code, label: '参考图', thumbnail: asset.content_url })),
  ]

  const appendMention = (code: string) => {
    const current = taskInstruction
    const start = Math.min(promptSelectionRef.current.start, current.length)
    const end = Math.min(Math.max(promptSelectionRef.current.end, start), current.length)
    const before = current.slice(0, start)
    const after = current.slice(end)
    const leadingSpace = before && !/[\s([{（【，,。；;:：]$/.test(before) ? ' ' : ''
    const trailingSpace = !after || !/^[\s)\]）】，,。；;:：!?！？]/.test(after) ? ' ' : ''
    const inserted = `${leadingSpace}@${code}${trailingSpace}`
    const nextCursor = start + inserted.length

    setTaskInstruction(`${before}${inserted}${after}`)
    promptSelectionRef.current = { start: nextCursor, end: nextCursor }
    requestAnimationFrame(() => {
      const textarea = promptEditorRef.current?.querySelector('textarea')
      textarea?.focus()
      textarea?.setSelectionRange(nextCursor, nextCursor)
    })
  }

  const assetTab = (
    <div className="tab-stack">
      <Card className="subtle-card" title="原始图片" extra={<Tag>{sources.length} 张</Tag>}>
        {draftOnly && (
          <div className="batch-upload-row">
            <Upload.Dragger
              accept={IMAGE_FILE_ACCEPT}
              multiple
              fileList={sourceFiles}
              beforeUpload={(file) => {
                if (!isSupportedImageFile(file)) {
                  message.warning(`${file.name} 不是支持的图片格式`)
                  return Upload.LIST_IGNORE
                }
                return false
              }}
              onChange={({ fileList }) => setSourceFiles(fileList)}
            >
              <CloudUploadOutlined />
              <p>JPG / PNG / WebP，支持多选</p>
            </Upload.Dragger>
            <Button type="primary" icon={<CloudUploadOutlined />} disabled={!sourceFiles.length} loading={acting} onClick={uploadSources}>
              上传并分析 {sourceFiles.length || ''}
            </Button>
          </div>
        )}
        <AssetGallery assets={sources} empty="尚未上传原图" removable={draftOnly} removingId={removingAssetId} onRemove={removeAsset} showCodes />
      </Card>
    </div>
  )

  const identityTab = (
    <div className="tab-stack">
      {targetEditable && (
        <Card className="subtle-card" title="添加目标人物">
          <div className="target-person-create">
            <Input value={identityLabel} onChange={(event) => setIdentityLabel(event.target.value)} placeholder="人物名称" onPressEnter={createIdentity} />
            <Button type="primary" icon={<PlusOutlined />} disabled={!identityLabel.trim()} onClick={createIdentity}>添加</Button>
          </div>
        </Card>
      )}
      <div className="identity-grid">
        {batch.identities.map((identity) => (
          <Card
            key={identity.id}
            className="identity-card"
            title={<Space><Avatar shape="square" size={32} src={identity.references[0]?.content_url} /><Tag color="blue">@{identity.display_code}</Tag><strong>{identity.label}</strong></Space>}
            extra={<Space><Tag>{identity.references.length} 张参考图</Tag>{targetEditable && (<>
              <Upload
                showUploadList={false}
                accept={IMAGE_FILE_ACCEPT}
                beforeUpload={(file) => {
                  if (!isSupportedImageFile(file)) {
                    message.warning(`${file.name} 不是支持的图片格式`)
                    return Upload.LIST_IGNORE
                  }
                  void uploadReference(identity.id, file)
                  return Upload.LIST_IGNORE
                }}
              >
                <Button size="small" icon={<PlusOutlined />} loading={acting}>添加参考图</Button>
              </Upload>
              <Popconfirm
                title={`移除 @${identity.display_code}？`}
                description="将同时移除该目标人物在当前任务内的全部参考图，编号不会复用"
                okText="移除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={() => removeIdentity(identity)}
              >
                <Button
                  type="text"
                  size="small"
                  danger
                  icon={<DeleteOutlined />}
                  loading={removingIdentityId === identity.id}
                  aria-label={`移除 ${identity.display_code}`}
                />
              </Popconfirm>
            </>)}</Space>}
          >
            <AssetGallery assets={identity.references} empty="尚未添加参考图" compact removable={targetEditable} removingId={removingAssetId} onRemove={removeAsset} showCodes />
          </Card>
        ))}
        {!batch.identities.length && <Empty description="没有目标人物" />}
      </div>
    </div>
  )

  const fusionPanel = (
    <div className="tab-stack">
      {batch.status === 'awaiting_confirmation' && (
        <div className="mapping-toolbar">
          <span>检查人物是否完整；重复人物可勾选后融合</span>
          <Space wrap>
            <Popconfirm
              title="重新分析全部原图？"
              description="现有人物识别记录会被替换，已创建的 P 编号不会复用"
              okText="重新分析"
              cancelText="取消"
              onConfirm={rerunAnalysis}
            >
              <Button icon={<ReloadOutlined />} loading={acting}>重新分析</Button>
            </Popconfirm>
            <Button type={fusionVerified ? 'default' : 'primary'} loading={acting} onClick={fusePeople}>{fusionVerified ? '重新自动融合' : '自动融合重复人物'}</Button>
            <Button disabled={selectedPersonIds.length < 2} loading={acting} onClick={mergeSelectedPeople}>融合选中人物</Button>
          </Space>
        </div>
      )}
      {!!uncertainAudits.length && (
        <Alert
          type="warning"
          showIcon
          message={`${uncertainAudits.length} 张图片需要确认是否漏检`}
          description={uncertainAudits.map(({ asset, audit }) => `@${asset?.display_code ?? '图片'}：${audit.summary}`).join('；')}
        />
      )}
      {batch.status === 'awaiting_confirmation' && (
        <div className="manual-face-strip">
          {sources.map((asset) => (
            <button type="button" key={asset.id} className="manual-face-source" onClick={() => setManualFaceAsset(asset)}>
              <img src={asset.content_url} alt={asset.filename} />
              <span>@{asset.display_code} · 补充人物</span>
            </button>
          ))}
        </div>
      )}
      {!batch.persons.length ? <Empty description="尚未提取到人物" /> : (
        <>
          <div className="fusion-grid">
            {batch.persons.map((person, index) => {
              const imageCount = new Set(person.faces.map((face) => face.asset_id)).size
              return (
                <Card
                  key={person.id}
                  className="mapping-card fusion-card"
                  title={<Space>{batch.status === 'awaiting_confirmation' && <Checkbox checked={selectedPersonIds.includes(person.id)} onChange={(event) => setSelectedPersonIds((current) => event.target.checked ? [...current, person.id] : current.filter((id) => id !== person.id))} />}<Tag color="purple">@{person.display_code}</Tag>{person.label || `人物 ${index + 1}`}</Space>}
                  extra={<Space><Tag color={person.faces.length > 1 ? 'success' : 'default'}>{imageCount} 张图 · {person.faces.length} 次</Tag>{batch.status === 'awaiting_confirmation' && <Popconfirm title={`移除 @${person.display_code}？`} description="删除该人物的全部识别记录，原始图片不会改变" okText="移除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => removePerson(person)}><Button type="text" size="small" danger icon={<DeleteOutlined />} aria-label={`移除 ${person.display_code}`} /></Popconfirm>}</Space>}
                >
                  {!!person.faces.length && (
                    <div className="person-occurrences">
                      {person.faces.map((face) => (
                        <div className="person-occurrence" key={face.id}>
                          <Image src={face.crop_url} width={72} height={72} preview={{ mask: '查看' }} />
                          {batch.status === 'awaiting_confirmation' && person.faces.length > 1 && (
                            <Popconfirm title="这张脸不是该人物？" description="将其拆分为新的 P 编号" okText="拆分" cancelText="取消" onConfirm={() => splitOccurrence(person.id, face.id)}>
                              <Button type="link" size="small">拆分</Button>
                            </Popconfirm>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </Card>
              )
            })}
          </div>
        </>
      )}
      <ManualFacePicker
        asset={manualFaceAsset}
        people={batch.persons}
        loading={acting}
        onCancel={() => setManualFaceAsset(undefined)}
        onSubmit={addManualFace}
      />
    </div>
  )

  const analysisStep = (
    <Card className="subtle-card" title="人物提取与融合" extra={<Tag color={analysisVerified ? 'success' : 'warning'}>{batch.persons.length} 人</Tag>}>
      {analysisComplete || batch.persons.length
        ? fusionPanel
        : <Empty description={batch.status === 'analyzing' ? '正在提取并融合人物' : '上传素材后自动进行人物分析'} />}
    </Card>
  )

  const taskSpecPanel = (
    <Card className="task-spec-card" title="任务要求" extra={<Space><span>每张图最多</span><InputNumber min={1} max={10} value={maxIterations} disabled={!specEditable} onChange={(value) => setMaxIterations(value ?? 3)} /><span>轮</span></Space>}>
      <div ref={promptEditorRef} className={`prompt-editor${specEditable ? '' : ' disabled'}`}>
        <Input.TextArea
          className="prompt-editor-input"
          variant="borderless"
          value={taskInstruction}
          disabled={!specEditable}
          autoSize={{ minRows: 3, maxRows: 7 }}
          maxLength={4000}
          placeholder="例如：将 @P01 替换为 @T01，保留 @P02；其他内容保持原样"
          onChange={(event) => {
            setTaskInstruction(event.target.value)
            promptSelectionRef.current = {
              start: event.target.selectionStart,
              end: event.target.selectionEnd,
            }
          }}
          onSelect={(event) => {
            promptSelectionRef.current = {
              start: event.currentTarget.selectionStart,
              end: event.currentTarget.selectionEnd,
            }
          }}
        />
        {!!mentionCodes.length && (
          <div className="prompt-reference-panel">
            <span className="prompt-reference-label">点击引用人物或素材</span>
            <div className="mention-strip">
              {mentionCodes.map((item) => {
                const referenced = new RegExp(`@${item.code}\\b`, 'i').test(taskInstruction)
                return (
                  <Popover
                    key={`${item.code}-${item.label}`}
                    trigger="hover"
                    mouseEnterDelay={0.15}
                    content={item.thumbnail ? (
                      <div className="reference-hover-preview">
                        <img src={item.thumbnail} alt={`@${item.code} ${item.label}`} />
                        <strong>@{item.code} · {item.label}</strong>
                      </div>
                    ) : <span>暂无预览图</span>}
                  >
                    <button
                      type="button"
                      className={`mention-reference${referenced ? ' referenced' : ''}`}
                      disabled={!specEditable}
                      onClick={() => appendMention(item.code)}
                    >
                      <Avatar shape="square" size={42} src={item.thumbnail}>{item.code.slice(0, 1)}</Avatar>
                      <span><strong>@{item.code}</strong><small>{item.label}</small></span>
                    </button>
                  </Popover>
                )
              })}
            </div>
          </div>
        )}
      </div>
      {specEditable && (
        <Space wrap>
          <Button onClick={() => saveTaskSpec()} loading={acting}>保存任务要求</Button>
          <Button
            icon={compiledContract ? <ReloadOutlined /> : <CheckCircleFilled />}
            disabled={!taskInstruction.trim()}
            loading={acting}
            onClick={compileTaskContract}
          >
            {compiledContract ? '重新编译正式约束' : '编译正式约束'}
          </Button>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            disabled={!contractConfirmed || taskSpecDirty}
            loading={acting}
            onClick={startTaskLoop}
          >
            开始任务循环
          </Button>
        </Space>
      )}
    </Card>
  )

  const contractEvidencePanel = (
    <Card
      className="subtle-card contract-evidence-card"
      title="正式约束证据"
      extra={compiledContract ? (
        <Space wrap>
          <Tag bordered={false}>SHA {batch.contract_hash?.slice(0, 12) ?? '待生成'}</Tag>
          <Tag color={contractConfirmed ? 'success' : 'warning'}>
            {contractConfirmed ? '已确认' : '待确认'}
          </Tag>
        </Space>
      ) : <Tag>尚未编译</Tag>}
    >
      {!compiledContract ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="保存任务要求后编译，系统会在这里展示人物、权限、参考覆盖与授权蒙版证据"
        />
      ) : (
        <div className="contract-evidence-stack">
          {taskSpecDirty && (
            <Alert
              type="warning"
              showIcon
              message="任务要求已有未编译修改"
              description="当前证据仍对应上一次编译；请重新编译并确认后再运行。"
            />
          )}
          {compiledContract.requires_manual_review ? (
            <Alert
              type="warning"
              showIcon
              message="正式约束需要人工复核"
              description={(
                <Space wrap>
                  {compiledContract.review_reasons.map((reason) => (
                    <Tag
                      key={reason}
                      color={reason === 'mask_uncertain' ? 'error' : 'warning'}
                      bordered={false}
                    >
                      {evidenceLabel(reason)}
                    </Tag>
                  ))}
                </Space>
              )}
            />
          ) : (
            <Alert type="success" showIcon message="正式约束证据完整" />
          )}

          <section className="contract-evidence-section" aria-labelledby="compiled-operations-heading">
            <div className="contract-section-heading">
              <h3 id="compiled-operations-heading">人物操作与权限</h3>
              <Tag>{compiledContract.operations.length} 项</Tag>
            </div>
            <div className="compiled-operation-grid">
              {compiledContract.operations.map((operation) => (
                <div className="compiled-operation" key={operation.person_id}>
                  <div className="compiled-evidence-heading">
                    <Space wrap>
                      <Tag color="purple">@{operation.person_code}</Tag>
                      <strong>{operation.action === 'replace' ? '替换人物' : operation.action === 'edit' ? '编辑人物' : '保留人物'}</strong>
                      {operation.target_identity_code ? <span>→ @{operation.target_identity_code}</span> : null}
                    </Space>
                    {operation.requires_manual_review ? <Tag color="warning">需复核</Tag> : null}
                  </div>
                  <div className="permission-partition">
                    <div>
                      <span>可编辑</span>
                      <div className="evidence-tag-list">
                        {(operation.permissions?.editable ?? []).map((attribute) => (
                          <Tag color="processing" bordered={false} key={attribute}>
                            {attributeLabels[attribute] ?? attribute}
                          </Tag>
                        ))}
                        {!operation.permissions?.editable?.length ? <Text type="secondary">无</Text> : null}
                      </div>
                    </div>
                    <div>
                      <span>受保护</span>
                      <div className="evidence-tag-list">
                        {(operation.permissions?.protected ?? []).map((attribute) => (
                          <Tag bordered={false} key={attribute}>
                            {attributeLabels[attribute] ?? attribute}
                          </Tag>
                        ))}
                        {!operation.permissions?.protected?.length ? <Text type="secondary">无</Text> : null}
                      </div>
                    </div>
                  </div>
                  {operation.reference_assessment ? (
                    <div className={`reference-assessment${operation.reference_assessment.requires_manual_review ? ' uncertain' : ''}`}>
                      <div className="reference-assessment-summary">
                        <strong>参考覆盖</strong>
                        <Space size="small" wrap>
                          <Tag color={operation.reference_assessment.sufficient ? 'success' : 'warning'}>
                            {operation.reference_assessment.reference_count} 张
                          </Tag>
                          <Text type="secondary">
                            置信度 {Math.round(operation.reference_assessment.confidence * 100)}%
                          </Text>
                        </Space>
                      </div>
                      <Text>{operation.reference_assessment.summary}</Text>
                      {!!operation.reference_assessment.missing_views.length && (
                        <div className="evidence-tag-list">
                          <span>缺少视角</span>
                          {operation.reference_assessment.missing_views.map((view) => (
                            <Tag color="warning" bordered={false} key={view}>{view}</Tag>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : null}
                  {!!operation.source_reference_coverage.length && (
                    <div className="coverage-list">
                      {operation.source_reference_coverage.map((coverage) => {
                        const sourceAsset = sourceByEvidenceKey.get(coverage.source_asset_id)
                          ?? sourceByEvidenceKey.get(coverage.source_asset_code)
                        return (
                          <div
                            className={`coverage-item${coverage.requires_manual_review ? ' uncertain' : ''}`}
                            key={`${coverage.source_asset_id}-${coverage.person_id}`}
                          >
                            {sourceAsset ? (
                              <Image
                                src={sourceAsset.content_url}
                                alt={`来源 @${coverage.source_asset_code}`}
                                width={64}
                                height={48}
                              />
                            ) : null}
                            <div>
                              <Space size="small" wrap>
                                <strong>@{coverage.source_asset_code}</strong>
                                <Tag color={coverage.sufficient ? 'success' : 'warning'} bordered={false}>
                                  {coverage.sufficient ? '覆盖充分' : '参考不足'}
                                </Tag>
                                <Text type="secondary">{coverage.occurrences.length} 处人脸</Text>
                              </Space>
                              <p>{coverage.summary}</p>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>

          {!!compiledContract.identities.length && (
            <section className="contract-evidence-section" aria-labelledby="compiled-identities-heading">
              <div className="contract-section-heading">
                <h3 id="compiled-identities-heading">目标人物证据</h3>
                <Tag>{compiledContract.identities.length} 人</Tag>
              </div>
              <div className="compiled-identity-grid">
                {compiledContract.identities.map((identity, index) => {
                  const profile = identity.profile
                  const assessment = identity.reference_assessment
                  return (
                    <div className="compiled-identity" key={profile?.identity_id ?? index}>
                      <div className="compiled-evidence-heading">
                        <Space wrap>
                          <Tag color="blue">@{profile?.identity_code ?? '目标人物'}</Tag>
                          {assessment ? (
                            <Tag color={assessment.sufficient ? 'success' : 'warning'}>
                              {assessment.reference_count} 张参考图 · {Math.round(assessment.confidence * 100)}%
                            </Tag>
                          ) : null}
                        </Space>
                      </div>
                      <div className="compiled-reference-strip">
                        {identity.reference_assets.map((reference) => {
                          const asset = referenceByEvidenceKey.get(reference.sha256)
                            ?? referenceByEvidenceKey.get(reference.code)
                          return asset ? (
                            <div key={reference.sha256}>
                              <Image src={asset.content_url} alt={`目标参考 @${reference.code}`} />
                              <span>@{reference.code}</span>
                            </div>
                          ) : null
                        })}
                      </div>
                      {profile ? (
                        <dl className="profile-evidence">
                          <div><dt>年龄观感</dt><dd>{profile.apparent_age}</dd></div>
                          <div><dt>面部特征</dt><dd>{profile.face_features}</dd></div>
                          <div><dt>发型</dt><dd>{profile.hair}</dd></div>
                          <div><dt>眼镜</dt><dd>{profile.eyewear}</dd></div>
                          <div><dt>配饰 / 妆容</dt><dd>{profile.accessories} / {profile.makeup}</dd></div>
                          <div>
                            <dt>稳定身份锚点</dt>
                            <dd>{profile.stable_identity_anchors.join('；') || '未提取'}</dd>
                          </div>
                          <div>
                            <dt>可变特征</dt>
                            <dd>{profile.variable_traits.join('；') || '未提取'}</dd>
                          </div>
                          {!!profile.unknown_evidence.length && (
                            <div><dt>未知证据</dt><dd>{profile.unknown_evidence.join('；')}</dd></div>
                          )}
                          {!!profile.unsupported_evidence.length && (
                            <div><dt>不支持的证据</dt><dd>{profile.unsupported_evidence.join('；')}</dd></div>
                          )}
                        </dl>
                      ) : null}
                      {assessment?.requires_manual_review ? (
                        <Alert
                          type="warning"
                          showIcon
                          message="目标参考需要人工复核"
                          description={assessment.missing_views.length
                            ? `缺少视角：${assessment.missing_views.join('、')}`
                            : assessment.summary}
                        />
                      ) : null}
                    </div>
                  )
                })}
              </div>
            </section>
          )}

          <section className="contract-evidence-section" aria-labelledby="compiled-masks-heading">
            <div className="contract-section-heading">
              <h3 id="compiled-masks-heading">来源授权蒙版</h3>
              <Tag>{compiledContract.source_assets.length} 张来源</Tag>
            </div>
            <div className="compiled-source-grid">
              {compiledContract.source_assets.map((source) => {
                const sourceAsset = sourceByEvidenceKey.get(source.asset_id)
                  ?? sourceByEvidenceKey.get(source.code)
                const mask = source.authorization_mask
                const checked = !!mask && acknowledgedMaskSha256s.includes(mask.sha256)
                return (
                  <div className={`compiled-source${mask?.requires_manual_review ? ' uncertain' : ''}`} key={source.asset_id}>
                    <div className="compiled-evidence-heading">
                      <Space wrap>
                        <Tag color="geekblue">@{source.code}</Tag>
                        <strong>{source.editable_face_instance_ids.length} 处可编辑人脸</strong>
                      </Space>
                      {mask ? <Tag color={mask.requires_manual_review ? 'warning' : 'success'}>{mask.status === 'validated' ? '已校验' : mask.status}</Tag> : null}
                    </div>
                    <div className="mask-preview-pair">
                      {sourceAsset ? (
                        <div>
                          <span>来源图</span>
                          <Image src={sourceAsset.content_url} alt={`来源图 @${source.code}`} />
                        </div>
                      ) : null}
                      {mask?.preview_url ? (
                        <div>
                          <span>授权蒙版</span>
                          <Image src={mask.preview_url} alt={`@${source.code} 授权蒙版`} />
                        </div>
                      ) : null}
                    </div>
                    {mask ? (
                      <div className="mask-evidence-meta">
                        <Space wrap size="small">
                          <Text>{mask.width} × {mask.height}</Text>
                          <Text code title={mask.sha256}>SHA {mask.sha256.slice(0, 12)}</Text>
                        </Space>
                        {!!mask.review_reasons.length && (
                          <div className="formal-review-reasons">
                            {mask.review_reasons.map((reason) => (
                              <Tag color="error" bordered={false} key={reason}>{evidenceLabel(reason)}</Tag>
                            ))}
                          </div>
                        )}
                        {mask.requires_manual_review && (
                          <Checkbox
                            checked={checked}
                            onChange={(event) => {
                              setAcknowledgedMaskSha256s((current) => event.target.checked
                                ? [...current, mask.sha256]
                                : current.filter((digest) => digest !== mask.sha256))
                            }}
                          >
                            我已核对该蒙版的精确摘要 <Text code>{mask.sha256}</Text>
                          </Checkbox>
                        )}
                      </div>
                    ) : source.requires_authorization_mask ? (
                      <Alert type="error" showIcon message="授权蒙版证据缺失，无法确认" />
                    ) : (
                      <Text type="secondary">此来源没有可编辑人脸，无需授权蒙版。</Text>
                    )}
                  </div>
                )
              })}
            </div>
          </section>

          <div className="contract-confirm-bar">
            <div>
              <strong>{contractConfirmed ? '当前约束已确认' : '确认后才可开始任务循环'}</strong>
              <Text type="secondary">
                {requiredMaskDigests.length
                  ? `需明确核对 ${requiredMaskDigests.length} 个边界不确定蒙版。`
                  : '当前没有需要摘要确认的不确定蒙版。'}
              </Text>
            </div>
            <Button
              type="primary"
              icon={<CheckCircleFilled />}
              disabled={contractConfirmed || taskSpecDirty || !allRequiredMasksAcknowledged}
              loading={acting}
              onClick={confirmTaskContract}
            >
              {contractConfirmed ? '已确认正式约束' : '确认正式约束'}
            </Button>
          </div>
        </div>
      )}
    </Card>
  )

  const taskTab = (
    <div className="tab-stack">
      {taskSpecPanel}
      {contractEvidencePanel}
      <Card className="subtle-card" title="目标人物（可选）">{identityTab}</Card>
    </div>
  )

  const resultTab = (
    <Card className="subtle-card" title="处理结果" extra={<Tag color={results.length ? 'success' : 'default'}>{results.length} 张</Tag>}>
      {results.length
        ? <ResultComparisonGallery sources={sources} results={results} jobs={batch.latest_jobs} />
        : <Empty description="任务循环完成后在这里查看结果" />}
    </Card>
  )

  const jobTab = (
    <div className="tab-stack">
      <Row gutter={[12, 12]}>
        {Object.entries(progress?.stages ?? {}).map(([stage, counts]) => (
          <Col xs={24} md={8} key={stage}>
            <Card className="stat-card">
              <Statistic title={stageLabels[stage] ?? stage} value={counts.succeeded} suffix={`/ ${counts.total}`} />
              <Progress percent={counts.total ? Math.round((counts.succeeded / counts.total) * 100) : 0} showInfo={false} />
              <Space size="small"><Badge status="processing" text={`${counts.running} 运行`} /><Badge status="warning" text={`${counts.needs_review} 待复核`} /><Badge status="error" text={`${counts.failed} 失败`} /></Space>
            </Card>
          </Col>
        ))}
      </Row>
      <Card className="subtle-card" title="任务记录">
        <List
          dataSource={batch.latest_jobs}
          locale={{ emptyText: '暂无任务' }}
          renderItem={(job) => (
            <List.Item actions={['failed', 'needs_review'].includes(job.status) ? [
              <Button key="retry" size="small" icon={<RetweetOutlined />} loading={acting} onClick={() => retryAndExecute(job.id, job.stage)}>重试并执行</Button>,
            ] : undefined}>
              <List.Item.Meta
                avatar={job.status === 'succeeded'
                  ? <CheckCircleFilled className="success-icon" />
                  : job.status === 'failed'
                    ? <WarningFilled className="error-icon" />
                    : job.status === 'needs_review'
                      ? <WarningFilled style={{ color: '#faad14' }} />
                      : <LoadingOutlined />}
                title={<Space><span>{assetCodeById.get(job.item_key) ? `@${assetCodeById.get(job.item_key)} · ` : ''}{stageLabels[job.stage]}</span><Tag color={statusColor(job.status)}>{jobStatusLabels[job.status] ?? job.status}</Tag></Space>}
                description={(
                  <div className="job-progress-detail">
                    {job.status === 'failed'
                      ? (
                        <JobFailure
                          error_code={job.error_code}
                          error_message={job.error_message}
                          error_retryable={job.error_retryable}
                        />
                      )
                      : <div>{job.error_message || job.progress_message || phaseLabels[job.phase] || `尝试 ${job.attempt}`}</div>}
                    {job.status === 'running' && (
                      <Progress
                        percent={jobProgressPercent(job)}
                        size="small"
                        showInfo={false}
                        status="active"
                      />
                    )}
                    <Text type="secondary">尝试 {job.attempt}{job.current_iteration ? ` · 轮次 ${job.current_iteration}/${job.total_iterations}` : ''}{job.provider ? ` · ${job.provider}` : ''}</Text>
                    {!!job.output?.review_reasons?.length && (
                      <Alert
                        className="formal-review-alert"
                        type="error"
                        showIcon
                        message="正式质量门禁需要人工处理"
                        description={(
                          <div className="formal-review-reasons">
                            {job.output.review_reasons.map((reason) => (
                              <Tag
                                color={['reference_insufficiency', 'mask_uncertain', 'dual_review_failure'].includes(reason) ? 'error' : 'warning'}
                                bordered={false}
                                key={reason}
                              >
                                {evidenceLabel(reason)}
                              </Tag>
                            ))}
                          </div>
                        )}
                      />
                    )}
                    {job.output?.reviewer_verdicts?.some((verdict) => !verdict.passed) && (
                      <Alert
                        className="formal-review-alert"
                        type="error"
                        showIcon
                        message="双模型复核未通过"
                        description="复核员 A 与复核员 B 的结论均需通过；请分别检查下方失败原因。"
                      />
                    )}
                    {!!job.output?.reviewer_verdicts?.length && (
                      <div className="reviewer-verdict-grid" aria-label="双重复核结论">
                        {job.output.reviewer_verdicts.map((verdict) => (
                          <div
                            className={`reviewer-verdict${verdict.passed ? ' passed' : ' failed'}`}
                            key={verdict.reviewer_id}
                          >
                            <div className="reviewer-verdict-heading">
                              <div>
                                <strong>{verdict.reviewer_id === 'reviewer_a' ? '复核员 A' : '复核员 B'}</strong>
                                <span>{verdict.provider}</span>
                              </div>
                              <Tag color={verdict.passed ? 'success' : 'error'} bordered={false}>
                                {verdict.passed ? '通过' : '未通过'}
                              </Tag>
                            </div>
                            <div className="reviewer-checks">
                              {Object.entries(verdict.checks).map(([name, value]) => (
                                <Tag
                                  color={value === true ? 'success' : value === false ? 'error' : 'default'}
                                  bordered={false}
                                  key={name}
                                >
                                  {name} · {checkValueLabel(value)}
                                </Tag>
                              ))}
                            </div>
                            {!!verdict.reasons.length && (
                              <ul className="reviewer-reasons">
                                {verdict.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                              </ul>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              />
            </List.Item>
          )}
        />
      </Card>
    </div>
  )

  const stepContent = {
    upload: assetTab,
    analysis: analysisStep,
    task: taskTab,
    jobs: jobTab,
    results: resultTab,
  }[activeStep]

  return (
    <motion.div className="batch-workspace" key={batch.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
      <div className="batch-header">
        <div>
          <Space><span className="eyebrow">人物编辑任务</span><Tag color={statusColor(batch.status)}>{statusLabels[batch.status] ?? batch.status}</Tag></Space>
          <h1>{batch.name}</h1>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => refresh()}>刷新</Button>
          {primaryAction && <Button type="primary" icon={<PlayCircleOutlined />} loading={acting} disabled={batch.status === 'draft' && !sources.length} onClick={() => primaryAction.path.endsWith('/task/run') ? startTaskLoop() : runAction(primaryAction.path, primaryAction.success)}>{primaryAction.label}</Button>}
        </Space>
      </div>

      <Card className="pipeline-card">
        <Steps
          current={stepKeys.indexOf(activeStep)}
          onChange={(index) => setActiveStep(stepKeys[index])}
          status={batch.status === 'failed' ? 'error' : batch.status === 'completed' ? 'finish' : 'process'}
          items={[
            { title: '上传素材' },
            { title: '人物分析', disabled: !sources.length },
            { title: '任务设置', disabled: !analysisComplete },
            { title: '任务循环', disabled: !batch.latest_jobs.length },
            { title: '结果验收', disabled: !results.length },
          ]}
        />
      </Card>

      {!!failedJobs.length && (
        <Card className="warning-strip" size="small">
          <Space><WarningFilled /> {failedJobs.length} 个任务需要处理</Space>
        </Card>
      )}

      <motion.div key={activeStep} className="workflow-step-content" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
        {stepContent}
      </motion.div>
    </motion.div>
  )
}

type RelativeFaceBox = { x: number; y: number; width: number; height: number }

function ManualFacePicker({ asset, people, loading, onCancel, onSubmit }: {
  asset?: Asset
  people: Person[]
  loading: boolean
  onCancel: () => void
  onSubmit: (
    asset: Asset,
    bbox: { x: number; y: number; width: number; height: number },
    personId?: string,
  ) => Promise<void>
}) {
  const imageRef = useRef<HTMLImageElement>(null)
  const dragStart = useRef<{ x: number; y: number } | undefined>(undefined)
  const [box, setBox] = useState<RelativeFaceBox>()
  const [personId, setPersonId] = useState<string>('new')

  useEffect(() => {
    setBox(undefined)
    setPersonId('new')
  }, [asset?.id])

  const point = (event: ReactPointerEvent<HTMLDivElement>) => {
    const rect = imageRef.current?.getBoundingClientRect()
    if (!rect) return undefined
    return {
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    }
  }

  const begin = (event: ReactPointerEvent<HTMLDivElement>) => {
    const next = point(event)
    if (!next) return
    dragStart.current = next
    event.currentTarget.setPointerCapture(event.pointerId)
    setBox({ x: next.x, y: next.y, width: 0, height: 0 })
  }

  const move = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = dragStart.current
    const next = point(event)
    if (!start || !next) return
    setBox({
      x: Math.min(start.x, next.x),
      y: Math.min(start.y, next.y),
      width: Math.abs(next.x - start.x),
      height: Math.abs(next.y - start.y),
    })
  }

  const finish = (event: ReactPointerEvent<HTMLDivElement>) => {
    move(event)
    dragStart.current = undefined
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  const submit = async () => {
    const image = imageRef.current
    if (!asset || !image || !box || box.width < 0.01 || box.height < 0.01) return
    const bbox = {
      x: Math.max(0, Math.round(box.x * image.naturalWidth)),
      y: Math.max(0, Math.round(box.y * image.naturalHeight)),
      width: Math.max(1, Math.round(box.width * image.naturalWidth)),
      height: Math.max(1, Math.round(box.height * image.naturalHeight)),
    }
    await onSubmit(asset, bbox, personId === 'new' ? undefined : personId)
  }

  return (
    <Modal
      open={!!asset}
      title={asset ? `@${asset.display_code} · 框选缺失人物` : '框选缺失人物'}
      okText="加入人物分析"
      cancelText="取消"
      confirmLoading={loading}
      okButtonProps={{ disabled: !box || box.width < 0.01 || box.height < 0.01 }}
      onCancel={onCancel}
      onOk={submit}
      width={760}
      destroyOnHidden
    >
      <div className="manual-face-controls">
        <Select
          value={personId}
          onChange={setPersonId}
          options={[
            { value: 'new', label: '创建为新人物' },
            ...people.map((person) => ({
              value: person.id,
              label: `加入 @${person.display_code} ${person.label || ''}`.trim(),
            })),
          ]}
        />
        <Text type="secondary">在图片上拖动，只框住缺失人物的脸部</Text>
      </div>
      {asset && (
        <div
          className="manual-face-canvas"
          onPointerDown={begin}
          onPointerMove={move}
          onPointerUp={finish}
          onPointerCancel={finish}
        >
          <img ref={imageRef} src={asset.content_url} alt={asset.filename} draggable={false} />
          {box && (
            <div
              className="manual-face-selection"
              style={{
                left: `${box.x * 100}%`,
                top: `${box.y * 100}%`,
                width: `${box.width * 100}%`,
                height: `${box.height * 100}%`,
              }}
            />
          )}
        </div>
      )}
    </Modal>
  )
}

function AssetGallery({ assets, empty, compact = false, downloadable = false, removable = false, removingId, onRemove, showCodes = false }: {
  assets: Asset[]
  empty?: string
  compact?: boolean
  downloadable?: boolean
  removable?: boolean
  removingId?: string
  onRemove?: (asset: Asset) => Promise<void>
  showCodes?: boolean
}) {
  if (!assets.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={empty ?? '暂无图片'} />
  return (
    <Image.PreviewGroup>
      <div className={`asset-gallery${compact ? ' compact' : ''}`}>
        {assets.map((asset) => (
          <motion.div className="asset-tile" key={asset.id} layout>
            <Image src={asset.content_url} alt={asset.filename} loading="lazy" />
            <div className="asset-caption">
              <div className="asset-caption-name">
                {showCodes && <Tag color="geekblue">@{asset.display_code}</Tag>}
                <Text ellipsis={{ tooltip: asset.filename }}>{asset.filename}</Text>
              </div>
              <Space size={0}>
                {downloadable && <Button type="text" size="small" icon={<DownloadOutlined />} href={asset.content_url} download aria-label={`下载 ${asset.filename}`} />}
                {removable && onRemove && (
                  <Popconfirm title="移除这张图片？" description="仅从当前批次移除" okText="移除" cancelText="取消" onConfirm={() => onRemove(asset)}>
                    <Button type="text" size="small" danger icon={<DeleteOutlined />} loading={removingId === asset.id} aria-label={`移除 ${asset.filename}`} />
                  </Popconfirm>
                )}
              </Space>
            </div>
          </motion.div>
        ))}
      </div>
    </Image.PreviewGroup>
  )
}

function ResultComparisonGallery({ sources, results, jobs }: { sources: Asset[]; results: Asset[]; jobs: Batch['latest_jobs'] }) {
  const sourceById = new Map(sources.map((source) => [source.id, source]))
  const swapBySource = new Map(jobs.filter((job) => job.stage === 'swap').map((job) => [job.item_key, job]))
  const validationByResult = new Map(jobs.filter((job) => job.stage === 'validate').map((job) => [job.item_key, job]))
  const check = (checks: Record<string, boolean | number | string>, name: string) => (
    checks[name] ?? checks[`advisory.${name}`]
  )

  return (
    <div className="result-comparison-grid">
      {results.map((result) => {
        const source = result.parent_asset_id ? sourceById.get(result.parent_asset_id) : undefined
        const swapJob = result.parent_asset_id ? swapBySource.get(result.parent_asset_id) : undefined
        const generationReview = swapJob?.output?.generation_review
        const validation = validationByResult.get(result.id)
        const checks = validation?.output?.checks ?? {}
        const sourcePreserved = check(checks, 'outside_requested_regions_preserved')
        const identityConsistent = check(checks, 'cross_image_identity_consistent')
        const identityMatched = check(checks, 'target_identity_matches_references')
        const formalReviewReasons = validation?.output?.review_reasons?.length
          ? validation.output.review_reasons
          : swapJob?.output?.review_reasons ?? []
        const reviewerVerdicts = validation?.output?.reviewer_verdicts ?? []
        return (
          <Card
            key={result.id}
            className="result-comparison-card"
            title={(
              <Space wrap>
                <Tag color="green">@{result.display_code}</Tag>
                <span>{source?.display_code ? `@${source.display_code}` : result.filename}</span>
                {swapJob && (
                  <Tag color={statusColor(swapJob.status)}>
                    生成 {jobStatusLabels[swapJob.status] ?? swapJob.status}
                  </Tag>
                )}
                <Tag color={validation ? statusColor(validation.status) : 'default'}>
                  验收 {validation ? (jobStatusLabels[validation.status] ?? validation.status) : '待检'}
                </Tag>
              </Space>
            )}
            extra={<Button type="text" size="small" icon={<DownloadOutlined />} href={result.content_url} download aria-label={`下载结果 ${result.filename}`} />}
          >
            <Image.PreviewGroup>
              <div className="comparison-images">
                {source && <div><span>原图</span><Image src={source.content_url} alt={`原图 ${source.filename}`} /></div>}
                <div><span>结果</span><Image src={result.content_url} alt={`结果 ${result.filename}`} /></div>
              </div>
            </Image.PreviewGroup>
            <div className="validation-badges">
              <Tag color={sourcePreserved === true ? 'success' : sourcePreserved === false ? 'error' : 'default'}>
                原图保持 {sourcePreserved === true ? '通过' : sourcePreserved === false ? '异常' : '待检'}
              </Tag>
              <Tag color={identityMatched === true ? 'success' : identityMatched === false ? 'error' : 'default'}>
                目标人物 {identityMatched === true ? '通过' : identityMatched === false ? '异常' : '待检'}
              </Tag>
              <Tag color={identityConsistent === true ? 'success' : identityConsistent === false ? 'error' : 'default'}>
                跨图一致 {identityConsistent === true ? '通过' : identityConsistent === false ? '异常' : '待检'}
              </Tag>
            </div>
            {validation?.status === 'failed' && (
              <Alert
                className="formal-review-alert"
                type="error"
                showIcon
                message="结果验收执行失败"
                description={validation.error_message ?? validation.error_code ?? '未获得可用的验收结论，请重试对应结果。'}
              />
            )}
            {!!formalReviewReasons.length && (
              <Alert
                className="formal-review-alert"
                type="error"
                showIcon
                message="正式质量门禁未通过"
                description={(
                  <div className="formal-review-reasons">
                    {formalReviewReasons.map((reason) => (
                      <Tag
                        color={['reference_insufficiency', 'mask_uncertain', 'dual_review_failure'].includes(reason) ? 'error' : 'warning'}
                        bordered={false}
                        key={reason}
                      >
                        {evidenceLabel(reason)}
                      </Tag>
                    ))}
                  </div>
                )}
              />
            )}
            {reviewerVerdicts.some((verdict) => !verdict.passed) && (
              <Alert
                className="formal-review-alert"
                type="error"
                showIcon
                message="双模型复核未通过"
                description="两位独立复核员的结论未同时通过，结果需要人工处理。"
              />
            )}
            {!!reviewerVerdicts.length && (
              <div className="reviewer-verdict-grid" aria-label="双重复核结论">
                {reviewerVerdicts.map((verdict) => (
                  <div
                    className={`reviewer-verdict${verdict.passed ? ' passed' : ' failed'}`}
                    key={verdict.reviewer_id}
                  >
                    <div className="reviewer-verdict-heading">
                      <div>
                        <strong>{verdict.reviewer_id === 'reviewer_a' ? '复核员 A' : '复核员 B'}</strong>
                        <span>{verdict.provider}</span>
                      </div>
                      <Tag color={verdict.passed ? 'success' : 'error'} bordered={false}>
                        {verdict.passed ? '通过' : '未通过'}
                      </Tag>
                    </div>
                    <div className="reviewer-checks">
                      {Object.entries(verdict.checks).map(([name, value]) => (
                        <Tag
                          color={value === true ? 'success' : value === false ? 'error' : 'default'}
                          bordered={false}
                          key={name}
                        >
                          {name} · {checkValueLabel(value)}
                        </Tag>
                      ))}
                    </div>
                    {!!verdict.reasons.length && (
                      <ul className="reviewer-reasons">
                        {verdict.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            )}
            {generationReview?.summary && (
              <Alert
                type={generationReview.passed ? 'success' : 'warning'}
                showIcon
                message={generationReview.passed ? '生成复核通过' : '生成结果需要复核'}
                description={generationReview.summary}
              />
            )}
            {!!generationReview?.issues?.length && (
              <div className="validation-reasons">
                {generationReview.issues.slice(0, 3).map((issue, index) => (
                  <div key={`${issue.category ?? 'issue'}-${index}`}>• {issue.evidence || issue.correction || '需要人工检查'}</div>
                ))}
              </div>
            )}
            {!!validation?.output?.reasons?.length && (
              <div className="validation-reasons">{validation.output.reasons.slice(0, 3).map((reason) => <div key={reason}>• {reason}</div>)}</div>
            )}
          </Card>
        )
      })}
    </div>
  )
}
