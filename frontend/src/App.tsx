import {
  CheckCircleFilled,
  CloudOutlined,
  DeleteOutlined,
  FileImageOutlined,
  InboxOutlined,
  MenuOutlined,
  PictureOutlined,
  PlusOutlined,
  ReloadOutlined,
  UndoOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import {
  App as AntApp,
  Button,
  ConfigProvider,
  Drawer,
  Empty,
  Image,
  Input,
  List,
  Modal,
  Popconfirm,
  Space,
  Tag,
  Upload,
  theme,
} from 'antd'
import type { UploadFile } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { lazy, Suspense, useCallback, useEffect, useState } from 'react'

import {
  addLibraryMaterialImage,
  apiJson,
  createLibraryMaterial,
  personaApi,
  postJson,
} from './api'
import type { BatchSummary, Health, LibraryItem, RecycleItem } from './types'

const BatchWorkspace = lazy(() => import('./components/BatchWorkspace').then((module) => ({ default: module.BatchWorkspace })))

const statusLabels: Record<string, string> = {
  draft: '草稿',
  analyzing: '分析中',
  awaiting_confirmation: '待确认',
  ready: '待替换',
  swapping: '替换中',
  validating: '质检中',
  review: '待审核',
  completed: '已完成',
  failed: '失败',
}

function Workbench() {
  const { message } = AntApp.useApp()
  const [health, setHealth] = useState<Health>()
  const [batches, setBatches] = useState<BatchSummary[]>([])
  const [selectedBatch, setSelectedBatch] = useState<string>()
  const [mobileBatches, setMobileBatches] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [batchName, setBatchName] = useState('')
  const [creating, setCreating] = useState(false)
  const [recycleOpen, setRecycleOpen] = useState(false)
  const [recycleItems, setRecycleItems] = useState<RecycleItem[]>([])
  const [recycleLoading, setRecycleLoading] = useState(false)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [libraryItems, setLibraryItems] = useState<LibraryItem[]>([])
  const [libraryLoading, setLibraryLoading] = useState(false)
  const [materialLabel, setMaterialLabel] = useState('')
  const [materialFiles, setMaterialFiles] = useState<UploadFile[]>([])
  const [workspaceRevision, setWorkspaceRevision] = useState(0)

  const refreshBatches = useCallback(async () => {
    try {
      const items = await personaApi.batches()
      setBatches(items)
      setSelectedBatch((current) => items.some((item) => item.id === current) ? current : items[0]?.id)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '批次列表加载失败')
    }
  }, [message])

  useEffect(() => {
    void Promise.all([
      refreshBatches(),
      personaApi.health().then(setHealth).catch(() => undefined),
    ])
  }, [refreshBatches])

  const createBatch = async () => {
    if (!batchName.trim()) return
    setCreating(true)
    try {
      const batch = await postJson<BatchSummary>('/batches', { name: batchName.trim() })
      setBatchName('')
      setCreateOpen(false)
      setSelectedBatch(batch.id)
      await refreshBatches()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '创建失败')
    } finally {
      setCreating(false)
    }
  }

  const deleteBatch = async (batch: BatchSummary) => {
    try {
      await apiJson(`/batches/${batch.id}`, { method: 'DELETE' })
      message.success(`“${batch.name}”已移入回收站`)
      await refreshBatches()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '删除失败')
    }
  }

  const refreshRecycleBin = useCallback(async () => {
    setRecycleLoading(true)
    try {
      setRecycleItems((await personaApi.recycleBin()).items)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '回收站加载失败')
    } finally {
      setRecycleLoading(false)
    }
  }, [message])

  const refreshMaterialLibrary = useCallback(async () => {
    setLibraryLoading(true)
    try {
      setLibraryItems(await personaApi.materialLibrary())
    } catch (error) {
      message.error(error instanceof Error ? error.message : '常用人物加载失败')
    } finally {
      setLibraryLoading(false)
    }
  }, [message])

  const restoreBatch = async (item: RecycleItem) => {
    try {
      const path = item.kind === 'batch'
        ? `/recycle-bin/batches/${item.id}/restore`
        : `/recycle-bin/legacy/${encodeURIComponent(item.id)}/restore`
      const restored = await postJson<BatchSummary>(path)
      message.success(`已恢复“${item.name}”`)
      await Promise.all([refreshRecycleBin(), refreshBatches()])
      setSelectedBatch(restored.id)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '恢复失败')
    }
  }

  const permanentlyDelete = async (item: RecycleItem) => {
    const path = item.kind === 'batch'
      ? `/recycle-bin/batches/${item.id}`
      : `/recycle-bin/legacy/${encodeURIComponent(item.id)}`
    try {
      await apiJson(path, { method: 'DELETE' })
      message.success('已永久删除')
      await refreshRecycleBin()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '永久删除失败')
    }
  }

  const createMaterial = async () => {
    const file = materialFiles[0]?.originFileObj
    if (!materialLabel.trim() || !file) return
    setLibraryLoading(true)
    try {
      await createLibraryMaterial(materialLabel.trim(), file)
      setMaterialLabel('')
      setMaterialFiles([])
      message.success('常用素材已保存')
      await refreshMaterialLibrary()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '素材保存失败')
    } finally {
      setLibraryLoading(false)
    }
  }

  const addMaterialImage = async (item: LibraryItem, file: File) => {
    try {
      await addLibraryMaterialImage(item.id, file)
      message.success(`已为“${item.label}”添加参考图`)
      await refreshMaterialLibrary()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '参考图添加失败')
    }
  }

  const useMaterial = async (item: LibraryItem) => {
    if (!selectedBatch) return
    try {
      await postJson(`/material-library/${item.id}/use`, { batch_id: selectedBatch })
      message.success(`“${item.label}”已加入当前任务`)
      setWorkspaceRevision((value) => value + 1)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '素材加入任务失败')
    }
  }

  const deleteMaterial = async (item: LibraryItem) => {
    try {
      await apiJson(`/material-library/${item.id}`, { method: 'DELETE' })
      message.success(`已删除“${item.label}”`)
      await refreshMaterialLibrary()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '素材删除失败')
    }
  }

  const batchPanel = (
    <aside className="files-panel">
      <div className="panel-heading">
        <div><span className="panel-kicker">Workspace</span><strong>人物编辑任务</strong></div>
        <Button type="text" size="small" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)} />
      </div>
      <div className="batch-file-list">
        {batches.map((batch) => (
          <div className={`batch-file-row${batch.id === selectedBatch ? ' active' : ''}`} key={batch.id}>
            <button
              className="batch-file-item"
              onClick={() => { setSelectedBatch(batch.id); setMobileBatches(false) }}
            >
              <span className="batch-file-icon">{batch.status === 'completed' ? <CheckCircleFilled /> : <FileImageOutlined />}</span>
              <span className="batch-file-copy"><strong>{batch.name}</strong><small>{statusLabels[batch.status] ?? batch.status}</small></span>
              <span className={`status-dot ${batch.status}`} />
            </button>
            <Popconfirm
              title="删除这个任务？"
              description="任务和素材将移入回收站，可随时恢复"
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={() => deleteBatch(batch)}
            >
              <Button className="batch-delete-button" type="text" size="small" danger icon={<DeleteOutlined />} aria-label={`删除 ${batch.name}`} />
            </Popconfirm>
          </div>
        ))}
        {!batches.length && <div className="panel-empty">还没有批次</div>}
      </div>
      <Button className="panel-create-button" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建任务</Button>
    </aside>
  )

  return (
    <div className="figma-shell">
      <header className="editor-topbar">
        <div className="topbar-left">
          <button className="brand-button" aria-label="PersonaWeave"><span>P</span></button>
          <div className="file-title">
            <strong>PersonaWeave</strong>
            <span>{batches.find((batch) => batch.id === selectedBatch)?.name ?? '人物编辑任务'}</span>
          </div>
        </div>
        <div className="topbar-actions">
          <Button className="mobile-batch-trigger" type="text" icon={<MenuOutlined />} onClick={() => setMobileBatches(true)} />
          <Tag className="connection-tag" bordered={false} icon={<CloudOutlined />} color={health?.status === 'ok' ? 'success' : 'default'}>
            {health?.status === 'ok' ? '服务已连接' : '连接中'}
          </Tag>
          <Button icon={<PictureOutlined />} onClick={() => { setLibraryOpen(true); void refreshMaterialLibrary() }}>常用人物</Button>
          <Button icon={<DeleteOutlined />} onClick={() => { setRecycleOpen(true); void refreshRecycleBin() }}>回收站</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建任务</Button>
        </div>
      </header>

      <main className="batch-editor-main">
        {batchPanel}
        <section className="batch-document">
          <Suspense fallback={<div className="workspace-empty">正在加载工作台…</div>}>
            <BatchWorkspace key={`${selectedBatch ?? 'empty'}-${workspaceRevision}`} batchId={selectedBatch} onChanged={refreshBatches} />
          </Suspense>
        </section>
      </main>

      <Drawer className="batch-mobile-drawer" placement="left" width={286} open={mobileBatches} onClose={() => setMobileBatches(false)} styles={{ body: { padding: 0 } }}>
        {batchPanel}
      </Drawer>

      <Drawer
        title="常用人物库"
        width={560}
        open={libraryOpen}
        onClose={() => setLibraryOpen(false)}
        extra={<Button type="text" icon={<ReloadOutlined />} loading={libraryLoading} onClick={() => void refreshMaterialLibrary()} />}
      >
        <div className="library-create-row">
          <Input value={materialLabel} maxLength={120} placeholder="人物名称" onChange={(event) => setMaterialLabel(event.target.value)} />
          <Upload
            accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"
            maxCount={1}
            fileList={materialFiles}
            beforeUpload={() => false}
            onChange={({ fileList }) => setMaterialFiles(fileList)}
          >
            <Button icon={<UploadOutlined />}>选择参考图</Button>
          </Upload>
          <Button type="primary" icon={<PlusOutlined />} disabled={!materialLabel.trim() || !materialFiles.length} loading={libraryLoading} onClick={() => void createMaterial()}>添加人物</Button>
        </div>
        {libraryItems.length ? (
          <div className="library-grid">
            {libraryItems.map((item) => (
              <div className="library-card" key={item.id}>
                <div className="library-card-heading">
                  <Space size={6}><Tag color="purple">@{item.display_code}</Tag><strong>{item.label}</strong></Space>
                  <Popconfirm title="删除这个常用素材？" description="已加入其他任务的副本不会受影响" okText="删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => deleteMaterial(item)}>
                    <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </div>
                <Image.PreviewGroup>
                  <div className="library-thumbnails">
                    {item.assets.map((asset) => <Image key={asset.id} src={asset.content_url} alt={asset.filename} />)}
                  </div>
                </Image.PreviewGroup>
                <div className="library-card-actions">
                  <Upload showUploadList={false} accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" beforeUpload={(file) => { void addMaterialImage(item, file); return Upload.LIST_IGNORE }}>
                    <Button size="small" icon={<PlusOutlined />}>添加参考图</Button>
                  </Upload>
                  <Button size="small" type="primary" disabled={!selectedBatch} onClick={() => void useMaterial(item)}>加入当前任务</Button>
                </div>
              </div>
            ))}
          </div>
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无常用素材" />}
      </Drawer>

      <Drawer
        title="回收站"
        width={460}
        open={recycleOpen}
        onClose={() => setRecycleOpen(false)}
        extra={<Button type="text" icon={<ReloadOutlined />} loading={recycleLoading} onClick={() => void refreshRecycleBin()} />}
      >
        <List
          loading={recycleLoading}
          dataSource={recycleItems}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="回收站为空" /> }}
          renderItem={(item) => (
            <List.Item
              actions={[
                item.restorable ? <Button key="restore" type="link" size="small" icon={<UndoOutlined />} onClick={() => void restoreBatch(item)}>恢复</Button> : null,
                <Popconfirm key="delete" title="永久删除？" description="任务记录和全部图片都将删除，无法恢复" okText="永久删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => permanentlyDelete(item)}>
                  <Button type="link" size="small" danger>永久删除</Button>
                </Popconfirm>,
              ].filter(Boolean)}
            >
              <List.Item.Meta
                avatar={<span className="recycle-icon"><InboxOutlined /></span>}
                title={item.name}
                description={(
                  <div className="recycle-item-detail">
                    <span>{item.source_count ?? 0} 张原图 · {item.result_count ?? 0} 张结果 · {new Date(item.deleted_at).toLocaleString()}</span>
                    {!!item.task_instruction && <small>{item.task_instruction}</small>}
                    {!!item.preview_urls?.length && (
                      <Image.PreviewGroup>
                        <div className="recycle-preview-strip">
                          {item.preview_urls.map((url) => <Image key={url} src={url} />)}
                        </div>
                      </Image.PreviewGroup>
                    )}
                    {!item.restorable && <small>旧版本没有留下任务元数据，只能清理图片文件</small>}
                  </div>
                )}
              />
            </List.Item>
          )}
        />
      </Drawer>

      <Modal
        title="新建人物编辑任务"
        open={createOpen}
        okText="创建"
        cancelText="取消"
        confirmLoading={creating}
        okButtonProps={{ disabled: !batchName.trim() }}
        onOk={createBatch}
        onCancel={() => setCreateOpen(false)}
      >
        <Input autoFocus value={batchName} maxLength={120} placeholder="例如：春季活动照片" onChange={(event) => setBatchName(event.target.value)} onPressEnter={createBatch} />
      </Modal>
    </div>
  )
}

export default function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: '#0d99ff',
          colorInfo: '#0d99ff',
          colorSuccess: '#14ae5c',
          colorWarning: '#f59e0b',
          colorError: '#f24822',
          colorBgLayout: '#e5e5e5',
          colorBgContainer: '#ffffff',
          colorText: '#1e1e1e',
          colorTextSecondary: '#717171',
          borderRadius: 6,
          borderRadiusLG: 10,
          controlHeight: 32,
          fontSize: 13,
          fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, sans-serif',
        },
        components: {
          Button: { primaryShadow: 'none', borderRadius: 6 },
          Card: { boxShadowTertiary: 'none' },
          Input: { activeShadow: '0 0 0 1px #0d99ff' },
          Segmented: { itemSelectedBg: '#ffffff' },
        },
      }}
    >
      <AntApp><Workbench /></AntApp>
    </ConfigProvider>
  )
}
