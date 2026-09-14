import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useRecordDeletion } from './useRecordDeletion';

afterEach(() => vi.restoreAllMocks());
it('retains failed selections, cleans only confirmed deletions and uses the displayed revision', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const remove = vi
    .fn()
    .mockResolvedValueOnce(undefined)
    .mockRejectedValueOnce({ isAxiosError: true, response: { status: 409 } });
  const onDeleted = vi.fn();
  const refresh = vi.fn();
  const { result } = renderHook(() =>
    useRecordDeletion({
      records: [
        { id: 'a', revision: 2 },
        { id: 'b', revision: 3 },
      ],
      description: '记录',
      remove,
      onDeleted,
      refresh,
    }),
  );
  act(() => result.current.toggle(['a', 'b'], true));
  await act(() => result.current.request(['a', 'b']));
  expect(remove).toHaveBeenNthCalledWith(1, { id: 'a', revision: 2 });
  expect(remove).toHaveBeenNthCalledWith(2, { id: 'b', revision: 3 });
  expect(onDeleted).toHaveBeenCalledExactlyOnceWith('a');
  expect([...result.current.selected]).toEqual(['b']);
  expect(result.current.error).toContain('1 条记录未删除');
  expect(refresh).toHaveBeenCalledOnce();
});
it('honors cancelled confirmation and removes vanished records after an explicit retry', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  const remove = vi.fn().mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
  const onDeleted = vi.fn();
  const { result } = renderHook(() =>
    useRecordDeletion({
      records: [{ id: 'a', revision: 1 }],
      description: '记录',
      remove,
      onDeleted,
      refresh: vi.fn(),
    }),
  );
  await act(() => result.current.request(['a']));
  expect(remove).not.toHaveBeenCalled();
  confirm.mockReturnValue(true);
  await act(() => result.current.request(['a']));
  expect(onDeleted).toHaveBeenCalledWith('a');
  expect(result.current.error).toBeNull();
});
it('drops selected IDs when records become active or disappear', () => {
  const { result, rerender } = renderHook(
    ({ records }) =>
      useRecordDeletion({ records, description: '记录', remove: vi.fn(), onDeleted: vi.fn(), refresh: vi.fn() }),
    { initialProps: { records: [{ id: 'a', revision: 1 }] } },
  );
  act(() => result.current.toggle(['a'], true));
  rerender({ records: [] });
  expect(result.current.selected.size).toBe(0);
});
