import { renderHook, act } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useStudioShortcuts } from './useStudioShortcuts';

describe('useStudioShortcuts', () => {
  it('switches view mode when pressing digit keys 1-4', () => {
    const setViewMode = vi.fn();
    renderHook(() =>
      useStudioShortcuts({
        viewMode: 'SINGLE',
        setViewMode,
        brushSize: 20,
        setBrushSize: vi.fn(),
        undoManualMask: vi.fn(),
        redoManualMask: vi.fn(),
        canUndoMask: false,
        canRedoMask: false,
      }),
    );

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '2' }));
    });
    expect(setViewMode).toHaveBeenCalledWith('SPLIT');

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '4' }));
    });
    expect(setViewMode).toHaveBeenCalledWith('BRUSH');
  });

  it('triggers brush size adjustments and undo/redo only in BRUSH mode', () => {
    const setBrushSize = vi.fn();
    const undoManualMask = vi.fn();
    renderHook(() =>
      useStudioShortcuts({
        viewMode: 'BRUSH',
        setViewMode: vi.fn(),
        brushSize: 20,
        setBrushSize,
        undoManualMask,
        redoManualMask: vi.fn(),
        canUndoMask: true,
        canRedoMask: false,
      }),
    );

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '[' }));
    });
    expect(setBrushSize).toHaveBeenCalledWith(16);

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true }));
    });
    expect(undoManualMask).toHaveBeenCalled();
  });

  it('handles hold-to-compare with backslash key', () => {
    const onHoldCompareChange = vi.fn();
    renderHook(() =>
      useStudioShortcuts({
        viewMode: 'SINGLE',
        setViewMode: vi.fn(),
        brushSize: 20,
        setBrushSize: vi.fn(),
        undoManualMask: vi.fn(),
        redoManualMask: vi.fn(),
        canUndoMask: false,
        canRedoMask: false,
        onHoldCompareChange,
      }),
    );

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '\\' }));
    });
    expect(onHoldCompareChange).toHaveBeenCalledWith(true);

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keyup', { key: '\\' }));
    });
    expect(onHoldCompareChange).toHaveBeenCalledWith(false);
  });
});

it('ignores shortcuts outside the preparation workspace and while a dialog is open', () => {
  const setViewMode = vi.fn();
  const { rerender } = renderHook(
    ({ enabled }) =>
      useStudioShortcuts({
        enabled,
        viewMode: 'SINGLE',
        setViewMode,
        brushSize: 20,
        setBrushSize: vi.fn(),
        undoManualMask: vi.fn(),
        redoManualMask: vi.fn(),
        canUndoMask: false,
        canRedoMask: false,
      }),
    { initialProps: { enabled: false } },
  );
  act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: '2' })));
  expect(setViewMode).not.toHaveBeenCalled();
  rerender({ enabled: true });
  const dialog = document.createElement('div');
  dialog.setAttribute('role', 'dialog');
  document.body.append(dialog);
  act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: '2' })));
  expect(setViewMode).not.toHaveBeenCalled();
  dialog.remove();
  act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: '2' })));
  expect(setViewMode).toHaveBeenCalledWith('SPLIT');
});

it('restores the image when focus leaves while holding comparison', () => {
  const onHoldCompareChange = vi.fn();
  renderHook(() =>
    useStudioShortcuts({
      viewMode: 'SINGLE',
      setViewMode: vi.fn(),
      brushSize: 20,
      setBrushSize: vi.fn(),
      undoManualMask: vi.fn(),
      redoManualMask: vi.fn(),
      canUndoMask: false,
      canRedoMask: false,
      onHoldCompareChange,
    }),
  );
  act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: '\\' })));
  expect(onHoldCompareChange).toHaveBeenLastCalledWith(true);
  act(() => window.dispatchEvent(new Event('blur')));
  expect(onHoldCompareChange).toHaveBeenLastCalledWith(false);
});
