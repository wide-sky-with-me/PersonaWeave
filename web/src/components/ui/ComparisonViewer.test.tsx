import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ComparisonViewer } from './ComparisonViewer';

const mockPointerCapture = (viewport: HTMLElement) => {
  let captured: number | null = null;
  Object.defineProperties(viewport, {
    setPointerCapture: {
      value: vi.fn((id: number) => {
        captured = id;
      }),
    },
    hasPointerCapture: { value: vi.fn((id: number) => captured === id) },
    releasePointerCapture: {
      value: vi.fn((id: number) => {
        if (captured === id) captured = null;
      }),
    },
    getBoundingClientRect: {
      value: vi.fn(() => ({
        left: 100,
        right: 500,
        top: 0,
        bottom: 225,
        width: 400,
        height: 225,
        x: 100,
        y: 0,
        toJSON: () => ({}),
      })),
    },
  });
};

const loadImage = (image: HTMLImageElement, width: number, height: number) => {
  Object.defineProperties(image, {
    naturalWidth: { configurable: true, value: width },
    naturalHeight: { configurable: true, value: height },
  });
  fireEvent.load(image);
};

const loadMatchingImages = () => {
  loadImage(screen.getByAltText('图片处理结果：处理前') as HTMLImageElement, 1600, 900);
  loadImage(document.querySelector('img[alt=""]') as HTMLImageElement, 1600, 900);
};

describe('ComparisonViewer', () => {
  it('uses one semantic slider for keyboard, divider and clipping state', () => {
    render(<ComparisonViewer beforeSrc="before.png" afterSrc="after.png" />);
    loadMatchingImages();
    const slider = screen.getByRole('slider', { name: '处理前与处理后对比位置' });

    expect(slider).toHaveAttribute('aria-valuenow', '50');
    fireEvent.keyDown(slider, { key: 'ArrowRight' });
    expect(slider).toHaveAttribute('aria-valuenow', '52');
    fireEvent.keyDown(slider, { key: 'End' });
    expect(slider).toHaveAttribute('aria-valuenow', '100');
    expect(screen.getByTestId('comparison-divider')).toHaveStyle({ left: '100%' });
    expect(screen.getByTestId('comparison-after-layer')).toHaveStyle({ clipPath: 'inset(0 0% 0 0)' });
    fireEvent.keyDown(slider, { key: 'Home' });
    expect(slider).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getByTestId('comparison-after-layer')).toHaveStyle({ clipPath: 'inset(0 100% 0 0)' });
  });

  it('maps pointer coordinates directly onto the same viewport and clamps both edges', () => {
    render(<ComparisonViewer beforeSrc="before.png" afterSrc="after.png" />);
    loadMatchingImages();
    const viewport = screen.getByTestId('comparison-viewport');
    mockPointerCapture(viewport);

    fireEvent.pointerDown(viewport, { pointerId: 7, pointerType: 'mouse', button: 0, clientX: 200 });
    expect(viewport).toHaveAttribute('aria-valuenow', '25');
    fireEvent.pointerMove(viewport, { pointerId: 7, pointerType: 'mouse', clientX: 460 });
    expect(viewport).toHaveAttribute('aria-valuenow', '90');
    fireEvent.pointerMove(viewport, { pointerId: 7, pointerType: 'mouse', clientX: 700 });
    expect(viewport).toHaveAttribute('aria-valuenow', '100');
    fireEvent.pointerMove(viewport, { pointerId: 7, pointerType: 'mouse', clientX: 0 });
    expect(viewport).toHaveAttribute('aria-valuenow', '0');
    fireEvent.pointerUp(viewport, { pointerId: 7, pointerType: 'mouse', clientX: 0 });
  });

  it('uses the exact baseline dimensions as its viewport ratio', () => {
    render(<ComparisonViewer beforeSrc="before.png" afterSrc="after.png" />);
    loadImage(screen.getByAltText('图片处理结果：处理前') as HTMLImageElement, 1200, 1800);
    loadImage(document.querySelector('img[alt=""]') as HTMLImageElement, 1200, 1800);

    expect(screen.getByTestId('comparison-viewport')).toHaveStyle({ aspectRatio: '1200 / 1800' });
    expect(screen.getByRole('slider')).toHaveAttribute('aria-disabled', 'false');
  });

  it('pins both layers to the same top-left fill geometry', () => {
    render(<ComparisonViewer beforeSrc="before.png" afterSrc="after.png" />);
    loadMatchingImages();

    expect(screen.getByAltText('图片处理结果：处理前')).toHaveClass('object-fill', 'object-left-top');
    expect(screen.getByTestId('comparison-after-layer')).toHaveClass('object-fill', 'object-left-top');
  });

  it('never overlays different canvas dimensions and falls back to side-by-side', () => {
    render(<ComparisonViewer beforeSrc="before.png" afterSrc="after.png" />);
    loadImage(screen.getByAltText('图片处理结果：处理前') as HTMLImageElement, 1600, 900);
    loadImage(document.querySelector('img[alt=""]') as HTMLImageElement, 1599, 900);

    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('1600×900 / 1599×900');
    expect(screen.getByAltText('图片处理结果：处理后')).toBeInTheDocument();
  });

  it('associates load state with each exact source and resets position for a new revision', () => {
    const { rerender } = render(<ComparisonViewer beforeSrc="before.png" afterSrc="after.png" />);
    loadMatchingImages();
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'End' });

    rerender(<ComparisonViewer beforeSrc="next-before.png" afterSrc="next-after.png" />);
    const loadingSlider = screen.getByRole('slider');
    expect(loadingSlider).toHaveAttribute('aria-disabled', 'true');
    expect(loadingSlider).toHaveAttribute('aria-valuenow', '50');
    expect(screen.getByRole('status')).toHaveTextContent('正在加载');
  });

  it('reports which comparison side failed to load', () => {
    render(<ComparisonViewer beforeSrc="before.png" afterSrc="after.png" beforeLabel="原图" afterLabel="候选" />);
    fireEvent.error(screen.getByAltText('图片处理结果：原图'));
    expect(screen.getByRole('status')).toHaveTextContent('原图');
  });
});

it('lets an image-only retry recover after a temporary load failure', () => {
  render(<ComparisonViewer beforeSrc="/before-retry.png" afterSrc="/after-retry.png" />);
  const before = document.querySelector('img[src="/before-retry.png"]')!;
  fireEvent.error(before);
  fireEvent.click(screen.getByRole('button', { name: '重新加载对比图片' }));
  expect(document.querySelector('img[src="/before-retry.png"]')).not.toBe(before);
  expect(screen.queryByRole('button', { name: '重新加载对比图片' })).not.toBeInTheDocument();
});
