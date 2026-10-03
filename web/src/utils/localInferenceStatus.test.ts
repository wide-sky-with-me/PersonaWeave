import { expect, it } from 'vitest';
import { localInferenceStatus } from './localInferenceStatus';
import type { ServerHealth } from '@/types';

const health = (phase: string, operation = 'source_preparation') =>
  ({
    local_inference: {
      queued: 2,
      active: [{ phase, operation, elapsed_seconds: 8.9, waiting_reason: '等待可用内存：当前 300 MiB' }],
    },
  }) as ServerHealth;
it('distinguishes memory waits, source analysis and cancelled native work', () => {
  expect(localInferenceStatus(health('waiting_memory'))).toContain('当前 300 MiB');
  expect(localInferenceStatus(health('running'))).toContain('分析人物参考素材（8 秒），另有 2 项等待');
  expect(localInferenceStatus(health('finishing_cancelled_request'))).toContain('上次请求已取消');
  expect(localInferenceStatus(null)).toBeNull();
});
