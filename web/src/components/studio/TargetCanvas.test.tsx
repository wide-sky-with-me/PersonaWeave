import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { TargetCanvas } from './TargetCanvas';

const mocks = vi.hoisted(() => ({
  store: {
    sourceGallery: [],
    sourcePeople: [],
    personMappings: [],
    setPersonMappings: vi.fn(),
    parameters: { source_mode: 'best-match' as const },
    targetImages: [
      {
        id: 'target-1',
        name: 'group.jpg',
        url: 'before.jpg',
        imageWidth: 1600,
        imageHeight: 900,
        faces: [],
        processedUrl: 'after.jpg',
        status: 'SUCCESS',
      },
    ],
    activeTargetIndex: 0,
    setActiveTargetIndex: vi.fn(),
    addTargetImages: vi.fn(),
    removeTargetImage: vi.fn(),
    updateTargetImageById: vi.fn(),
    batchUpdateTargetImages: vi.fn(),
    selectedTargetPersonId: null,
    setSelectedTargetPersonId: vi.fn(),
    viewMode: 'SPLIT',
    setViewMode: vi.fn(),
    splitPosition: 50,
    setSplitPosition: vi.fn(),
    showFaceMarkers: true,
    setShowFaceMarkers: vi.fn(),
    isProcessing: false,
    setManualMaskDataUrl: vi.fn(),
    undoManualMask: vi.fn(),
    redoManualMask: vi.fn(),
    brushSize: 24,
    setBrushSize: vi.fn(),
    isEraser: false,
    setIsEraser: vi.fn(),
  },
}));

vi.mock('@/stores/useStudioStore', () => ({ useStudioStore: () => mocks.store }));
vi.mock('@/hooks/useIdentityClustering', () => ({
  useIdentityClustering: () => ({ isClustering: false, clusteringError: null }),
}));
vi.mock('@/components/ui/ToastProvider', () => ({ useToast: () => vi.fn() }));

describe('TargetCanvas comparison layers', () => {
  it('renders before and after as direct children of one top-left fill viewport', () => {
    render(<TargetCanvas />);

    const viewport = screen.getByTestId('target-image-viewport');
    const before = screen.getByTestId('target-comparison-before-layer');
    const after = screen.getByTestId('target-comparison-after-layer');

    expect(before.parentElement).toBe(viewport);
    expect(after.parentElement).toBe(viewport);
    expect(viewport).not.toHaveClass('max-h-full');
    expect(before).toHaveClass('object-fill', 'object-left-top');
    expect(after).toHaveClass('object-fill', 'object-left-top');
    expect(after).toHaveStyle({ clipPath: 'inset(0 50% 0 0)' });
  });

  it('temporarily switches to original image and displays indicator banner when holding backslash', () => {
    render(<TargetCanvas />);

    // Initially in SPLIT mode, after layer is present
    expect(screen.getByTestId('target-comparison-after-layer')).toBeInTheDocument();
    expect(screen.queryByText(/正在临时对比原图/)).not.toBeInTheDocument();

    // Press and hold '\'
    fireEvent.keyDown(window, { key: '\\' });

    // In holding state, the indicator banner appears and after layer is hidden
    expect(screen.getByText(/正在临时对比原图/)).toBeInTheDocument();
    expect(screen.queryByTestId('target-comparison-after-layer')).not.toBeInTheDocument();

    // Release '\'
    fireEvent.keyUp(window, { key: '\\' });

    // Returns to normal
    expect(screen.queryByText(/正在临时对比原图/)).not.toBeInTheDocument();
    expect(screen.getByTestId('target-comparison-after-layer')).toBeInTheDocument();
  });

  it('can open and close the keyboard shortcuts guide popover', () => {
    render(<TargetCanvas />);

    const shortcutBtn = screen.getByRole('button', { name: '快捷键指南' });
    expect(screen.queryByRole('dialog', { name: '专业快捷键指南' })).not.toBeInTheDocument();

    fireEvent.click(shortcutBtn);
    expect(screen.getByRole('dialog', { name: '专业快捷键指南' })).toBeInTheDocument();
    expect(screen.getByText('专业快捷键指南', { exact: false })).toBeInTheDocument();

    const closeBtn = screen.getByRole('button', { name: '关闭快捷键指南' });
    fireEvent.click(closeBtn);
    expect(screen.queryByRole('dialog', { name: '专业快捷键指南' })).not.toBeInTheDocument();
  });
});

it('has one current-result refinement action and one comparison control', () => {
  render(<TargetCanvas />);
  expect(screen.getAllByRole('button', { name: 'AI 精修当前图' })).toHaveLength(1);
  expect(screen.getAllByRole('button', { name: '对比' })).toHaveLength(1);
  expect(screen.getByRole('button', { name: '下载当前结果' })).toBeEnabled();
  const actionBar = screen.getByTestId('target-result-action-bar');
  expect(screen.getByTestId('target-image-viewport').contains(actionBar)).toBe(false);
});

it('prevents removing an image while the batch is running', () => {
  mocks.store.isProcessing = true;
  render(<TargetCanvas />);
  expect(screen.getByRole('button', { name: '移除图片 1：group.jpg' })).toBeDisabled();
  mocks.store.isProcessing = false;
});
