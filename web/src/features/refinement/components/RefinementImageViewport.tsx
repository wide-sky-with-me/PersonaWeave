import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ComparisonViewer } from '@/components/ui/ComparisonViewer';
import { fitImageWithinViewport, type ViewportSize } from '@/utils/imageViewport';

interface Props {
  imageUrl: string;
  imageLabel: string;
  beforeUrl?: string;
  beforeLabel?: string;
  afterLabel: string;
  zoom: number;
}
/** The measured stage, not the photo's width, defines the initial scale on both axes. */
export function RefinementImageViewport({ imageUrl, imageLabel, beforeUrl, beforeLabel, afterLabel, zoom }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [bounds, setBounds] = useState<ViewportSize>({ width: 1, height: 1 });
  const [image, setImage] = useState<(ViewportSize & { src: string }) | null>(null);
  const [broken, setBroken] = useState<string | null>(null);
  useLayoutEffect(() => {
    const stage = scrollRef.current;
    if (!stage) return;
    const measure = () => {
      const next = { width: Math.max(1, stage.clientWidth - 2), height: Math.max(1, stage.clientHeight - 2) };
      setBounds((current) => (current.width === next.width && current.height === next.height ? current : next));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (zoom <= 1 && scrollRef.current) {
      scrollRef.current.scrollLeft = 0;
      scrollRef.current.scrollTop = 0;
    }
  }, [zoom, imageUrl, beforeUrl]);
  const dimensions = image?.src === imageUrl ? image : { width: 16, height: 9 };
  const fitted = fitImageWithinViewport(dimensions.width, dimensions.height, bounds.width, bounds.height)!;
  return (
    <div data-testid="refinement-image-viewport" className="relative min-h-0 flex-1 bg-canvas">
      <div
        ref={scrollRef}
        data-testid="refinement-image-scroll"
        data-zoom={zoom}
        className={'absolute inset-3 sm:inset-4 ' + (zoom > 1 ? 'overflow-auto' : 'overflow-hidden')}
      >
        <div
          className="grid min-h-full min-w-full place-items-center"
          style={{ width: 'max-content', height: 'max-content' }}
        >
          {beforeUrl ? (
            <ComparisonViewer
              beforeSrc={beforeUrl}
              afterSrc={imageUrl}
              beforeLabel={beforeLabel}
              afterLabel={afterLabel}
              imageLabel={imageLabel}
              viewportSize={bounds}
              zoom={zoom}
            />
          ) : broken === imageUrl ? (
            <p role="alert" className="p-4 text-sm text-amber-200">
              该版本图片加载失败，可能是暂时内存不足或连接中断。记录不会因此删除。
              <button className="btn btn-outline btn-sm ml-3" onClick={() => setBroken(null)}>
                重新加载图片
              </button>
            </p>
          ) : (
            <img
              data-testid="refinement-single-image"
              src={imageUrl}
              alt={'当前查看 ' + afterLabel}
              draggable={false}
              onLoad={(event) =>
                setImage({
                  src: imageUrl,
                  width: event.currentTarget.naturalWidth,
                  height: event.currentTarget.naturalHeight,
                })
              }
              onError={() => setBroken(imageUrl)}
              style={{
                width: fitted.width * zoom,
                height: fitted.height * zoom,
                maxWidth: 'none',
                objectFit: 'contain',
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
