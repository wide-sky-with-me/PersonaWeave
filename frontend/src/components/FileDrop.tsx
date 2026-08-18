import { DeleteOutlined, InboxOutlined, SwapOutlined } from '@ant-design/icons'
import { Button, Image, Upload, type UploadProps } from 'antd'
import { useEffect, useMemo, useState } from 'react'

import { fileFromUpload, IMAGE_FILE_ACCEPT, isSupportedImageFile } from '../imageFiles'

type FileDropProps = {
  title: string
  hint: string
  file?: File
  onChange: (file?: File) => void
  optional?: boolean
  disabled?: boolean
}

export function FileDrop({ title, hint, file, onChange, optional = false, disabled = false }: FileDropProps) {
  const [previewUrl, setPreviewUrl] = useState<string>()

  useEffect(() => {
    if (!file) {
      setPreviewUrl(undefined)
      return
    }
    const url = URL.createObjectURL(file)
    setPreviewUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  const uploadProps = useMemo<UploadProps>(
    () => ({
      accept: IMAGE_FILE_ACCEPT,
      disabled,
      multiple: false,
      showUploadList: false,
      beforeUpload: (nextFile) => isSupportedImageFile(nextFile) ? false : Upload.LIST_IGNORE,
      onChange: ({ file: nextFile }) => {
        const value = fileFromUpload(nextFile)
        if (value) onChange(value)
      },
    }),
    [disabled, onChange],
  )

  return (
    <div className="file-drop-shell">
      <div className="field-heading">
        <span>{title}</span>
        {optional && <span className="field-optional">可选</span>}
      </div>
      {file && previewUrl ? (
        <div className="file-preview-card">
          <Image src={previewUrl} alt={file.name} preview={{ mask: '查看大图' }} />
          <div className="file-preview-meta">
            <div className="file-name" title={file.name}>{file.name}</div>
            <div className="file-size">{(file.size / 1024 / 1024).toFixed(2)} MB</div>
          </div>
          <div className="file-preview-actions">
            <Upload {...uploadProps}>
              <Button size="small" icon={<SwapOutlined />} disabled={disabled}>更换</Button>
            </Upload>
            <Button size="small" danger type="text" icon={<DeleteOutlined />} disabled={disabled} onClick={() => onChange()}>
              移除
            </Button>
          </div>
        </div>
      ) : (
        <Upload.Dragger {...uploadProps} className="image-dragger">
          <InboxOutlined className="drop-icon" />
          <div className="drop-title">选择或拖入图片</div>
          <div className="drop-hint">{hint}</div>
        </Upload.Dragger>
      )}
    </div>
  )
}
