import { describe, expect, it } from 'vitest';
import { collectSwapWarnings } from './swapWarnings';

describe('swap warnings', () => {
  it('collects non-empty warnings without duplicates', () => {
    expect(
      collectSwapWarnings([
        { warning: '人物 #1 缺少同方向侧脸参考' },
        { warning: '人物 #1 缺少同方向侧脸参考' },
        { warning: undefined },
        { warning: '  人物 #3 未找到匹配人脸  ' },
      ]),
    ).toEqual(['人物 #1 缺少同方向侧脸参考', '人物 #3 未找到匹配人脸']);
  });
});
