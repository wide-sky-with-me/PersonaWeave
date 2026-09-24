import { describe, expect, it } from 'vitest';
import { apiErrorMessage } from './apiError';

describe('server error explanations', () => {
  it('shows the actual face-swap refusal instead of the HTTP status', () => {
    expect(
      apiErrorMessage({
        message: 'Request failed with status code 400',
        response: { data: { detail: '人物 #1：缺少同方向侧脸参考' } },
      }),
    ).toBe('人物 #1：缺少同方向侧脸参考');
  });
  it('summarizes validation errors without exposing input payloads', () => {
    expect(
      apiErrorMessage({
        response: {
          data: { detail: [{ loc: ['body', 'person_mappings', 0], msg: '参考图缺失', input: 'secret-image-data' }] },
        },
      }),
    ).toBe('person_mappings.0：参考图缺失');
  });
  it('keeps transport errors and handles missing details', () => {
    expect(apiErrorMessage(new Error('Network Error'))).toBe('Network Error');
    expect(apiErrorMessage(null, '服务不可用')).toBe('服务不可用');
  });
});
