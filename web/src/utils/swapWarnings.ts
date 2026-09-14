import type { ImageSwapResult } from '@/types';

export const collectSwapWarnings = (results: Pick<ImageSwapResult, 'warning'>[]): string[] => [
  ...new Set(results.map((result) => result.warning?.trim()).filter((warning): warning is string => Boolean(warning))),
];
