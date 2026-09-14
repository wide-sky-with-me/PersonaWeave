import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RefinementImageViewport } from './RefinementImageViewport';
import { ComparisonViewer } from '@/components/ui/ComparisonViewer';

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1000);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(650);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function load(image: HTMLElement, width = 900, height = 1450) {
  Object.defineProperties(image, {
    naturalWidth: { configurable: true, value: width },
    naturalHeight: { configurable: true, value: height },
  });
  fireEvent.load(image);
}
it('fits an entire tall portrait in both axes, only scrolls after zoom, and resets pan on fit', () => {
  const props = { imageUrl: '/portrait', imageLabel: '合影', afterLabel: 'V1', zoom: 1 };
  const view = render(<RefinementImageViewport {...props} />);
  const image = screen.getByTestId('refinement-single-image');
  load(image);
  const width = parseFloat(image.style.width),
    height = parseFloat(image.style.height);
  expect(height).toBeLessThanOrEqual(650);
  expect(width).toBeLessThanOrEqual(1000);
  expect(width / height).toBeCloseTo(900 / 1450);
  expect(image).toHaveStyle({ objectFit: 'contain' });
  const scroll = screen.getByTestId('refinement-image-scroll');
  expect(scroll).toHaveClass('overflow-hidden');
  view.rerender(<RefinementImageViewport {...props} zoom={2} />);
  expect(parseFloat(image.style.height)).toBeCloseTo(height * 2);
  expect(scroll).toHaveClass('overflow-auto');
  scroll.scrollTop = 300;
  scroll.scrollLeft = 50;
  view.rerender(<RefinementImageViewport {...props} />);
  expect(scroll.scrollTop).toBe(0);
  expect(scroll.scrollLeft).toBe(0);
});
it('gives comparison wipe exactly the same portrait fit geometry rather than fit-width cropping', () => {
  render(
    <RefinementImageViewport imageUrl="/candidate" beforeUrl="/original" imageLabel="合影" afterLabel="V1" zoom={1} />,
  );
  load(screen.getByTestId('comparison-before-layer'));
  load(screen.getByTestId('comparison-after-layer'));
  const viewport = screen.getByTestId('comparison-viewport');
  const frame = viewport.closest('figure')!;
  const imageWidth = parseFloat(frame.style.width) - 2;
  expect(imageWidth).toBeLessThan(1000);
  expect(imageWidth / (900 / 1450)).toBeLessThanOrEqual(650);
  expect(viewport).toHaveStyle({ aspectRatio: '900 / 1450' });
});
it('bounds unequal-size side-by-side inspection and never stretches either image', () => {
  render(<ComparisonViewer beforeSrc="/a" afterSrc="/b" viewportSize={{ width: 360, height: 500 }} />);
  load(screen.getByTestId('comparison-before-layer'));
  load(screen.getByTestId('comparison-after-layer'), 1600, 900);
  expect(screen.getByRole('alert')).toHaveTextContent('尺寸不一致');
  const images = screen.getAllByRole('img');
  images.forEach((image) => expect(image).toHaveClass('object-contain'));
  expect(images[0].closest('figure')).toHaveStyle({ width: '360px', height: '500px' });
});
