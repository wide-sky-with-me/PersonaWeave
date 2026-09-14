import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { api } from '@/services/api';
import type { TargetImageItem } from '@/types';
import { useIdentityClustering } from './useIdentityClustering';

vi.mock('@/services/api', () => ({
  api: { detectFaces: vi.fn(), clusterFaces: vi.fn() },
}));

const target = (id: string): TargetImageItem => ({ id, name: id, url: `/${id}.jpg`, status: 'IDLE' });

describe('useIdentityClustering result ownership', () => {
  it('ignores a stale clustering response after the target set changes', async () => {
    let resolveFirst!: (value: any) => void;
    let resolveSecond!: (value: any) => void;
    vi.mocked(api.clusterFaces)
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
      )
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveSecond = resolve;
        }),
      );
    const batchUpdateTargetImages = vi.fn();
    const updateTargetImageById = vi.fn();
    const { rerender } = renderHook(
      ({ targets }) => useIdentityClustering({ targetImages: targets, updateTargetImageById, batchUpdateTargetImages }),
      { initialProps: { targets: [target('a'), target('b')] } },
    );

    rerender({ targets: [target('b'), target('c')] });
    await act(async () => {
      resolveFirst({ images: [{ image_index: 0, faces: [{ index: 1 }], image_width: 10, image_height: 10 }] });
      await Promise.resolve();
    });
    expect(batchUpdateTargetImages).not.toHaveBeenCalled();

    await act(async () => {
      resolveSecond({ images: [{ image_index: 0, faces: [{ index: 2 }], image_width: 20, image_height: 20 }] });
    });
    await waitFor(() => expect(batchUpdateTargetImages).toHaveBeenCalledWith([expect.objectContaining({ id: 'b' })]));
  });

  it('does not synthesize target identity IDs for single-image face detection', async () => {
    vi.mocked(api.detectFaces).mockResolvedValue({
      faces: [{ index: 1, bbox: [0, 0, 10, 10] }],
      image_width: 10,
      image_height: 10,
    } as any);
    const updateTargetImageById = vi.fn();

    renderHook(() =>
      useIdentityClustering({
        targetImages: [target('single')],
        updateTargetImageById,
        batchUpdateTargetImages: vi.fn(),
      }),
    );

    await waitFor(() => expect(updateTargetImageById).toHaveBeenCalled());
    const patch = updateTargetImageById.mock.calls[0][1];
    expect(patch.faces[0]).not.toHaveProperty('global_person_id');
  });
});
