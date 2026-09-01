import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { TargetCanvas } from './TargetCanvas';

const mocks = vi.hoisted(() => ({
  store: {
    sourceGallery: [],
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

vi.mock('../../stores/useStudioStore', () => ({ useStudioStore: () => mocks.store }));
vi.mock('../../hooks/useIdentityClustering', () => ({
  useIdentityClustering: () => ({ isClustering: false, clusteringError: null }),
}));
vi.mock('../ui/ToastProvider', () => ({ useToast: () => vi.fn() }));

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
});
