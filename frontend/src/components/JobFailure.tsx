import { Space, Tag, Typography } from 'antd'

import type { Job } from '../types'

const { Text } = Typography

type JobFailureProps = Pick<Job, 'error_code' | 'error_message' | 'error_retryable'>

export function JobFailure({ error_code, error_message, error_retryable }: JobFailureProps) {
  const retryable = error_retryable === true

  return (
    <Space direction="vertical" size="small">
      <Space size="small" wrap>
        <Text>{error_message || '任务执行失败'}</Text>
        <Tag color={retryable ? 'warning' : 'error'} bordered={false}>
          {retryable ? '可重试' : '需先修改'}
        </Tag>
      </Space>
      <Space size="small" wrap>
        <Text type="secondary">
          {retryable
            ? '当前失败可以重试；若仍未成功，请稍后再试。'
            : '请先修改素材、任务要求或配置，再重试。'}
        </Text>
        {error_code ? <Text type="secondary" code>{error_code}</Text> : null}
      </Space>
    </Space>
  )
}
