import React, { useLayoutEffect, useRef, useState } from 'react';
import type { DetectedFaceBox, PersonMappingItem, SourcePerson, SourceReference } from '@/types';
import { FACE_MARKER_PADDING, faceBoxToPercentage } from '@/utils/imageViewport';
import { useStudioStore } from '@/stores/useStudioStore';
import { FaceAssignPopover } from './FaceAssignPopover';

interface CanvasFaceMarkersOverlayProps {
  faces: DetectedFaceBox[];
  imageWidth: number;
  imageHeight: number;
  selectedTargetPersonId: number | null;
  onSelectTargetPersonId: (id: number | null) => void;
  personMappings: PersonMappingItem[];
  sourcePeople: SourcePerson[];
  sourceGallery: SourceReference[];
  distinctGlobalPersons: number[];
  onAssignSourcePerson: (targetPersonId: number, faceIndex: number, sourcePersonId: number | null) => void;
  onReassignFaceGlobalId: (faceIndex: number, newPersonId: number) => void;
}

/** Keep this much of the image edge free around a badge. */
export const BADGE_EDGE_MARGIN = 4;

/**
 * Offset that moves `badge` inside `container` (both in viewport pixels). Overflow on the leading edge wins when the
 * badge is larger than the container, so its start stays readable.
 */
export function keepInside(
  badge: Pick<DOMRect, 'left' | 'right' | 'top' | 'bottom'>,
  container: Pick<DOMRect, 'left' | 'right' | 'top' | 'bottom'>,
  margin = BADGE_EDGE_MARGIN,
) {
  const shift = (low: number, high: number, min: number, max: number) =>
    low < min + margin ? min + margin - low : high > max - margin ? max - margin - high : 0;
  return {
    x: shift(badge.left, badge.right, container.left, container.right),
    y: shift(badge.top, badge.bottom, container.top, container.bottom),
  };
}

interface FaceMarkerProps extends Omit<CanvasFaceMarkersOverlayProps, 'faces' | 'imageWidth' | 'imageHeight'> {
  face: DetectedFaceBox;
  box: { left: number; top: number; width: number; height: number };
  isEditing: boolean;
  onToggle: () => void;
  onClose: () => void;
}

/**
 * One face box. It is the only interactive element (`role="button"`): its badge is plain text, and the
 * assignment popover is rendered as a sibling, never as a child, so interactive content is not nested
 * inside a button and the popover's key presses cannot reach this element's own key handler.
 */
const FaceMarker: React.FC<FaceMarkerProps> = ({
  face,
  box,
  isEditing,
  onToggle,
  onClose,
  selectedTargetPersonId,
  onSelectTargetPersonId,
  personMappings,
  sourcePeople,
  sourceGallery,
  distinctGlobalPersons,
  onAssignSourcePerson,
  onReassignFaceGlobalId,
}) => {
  const markerRef = useRef<HTMLDivElement>(null);
  const badgeAnchorRef = useRef<HTMLDivElement>(null);
  const [badgeShift, setBadgeShift] = useState({ x: 0, y: 0 });
  const setIsMatrixOpen = useStudioStore((state) => state.setIsMatrixOpen);
  const personId = face.global_person_id ?? face.index;
  const isSelected = selectedTargetPersonId === personId;
  const currentMapping = personMappings.find((m) => m.person_index === personId);
  const badgeText = currentMapping
    ? `#${personId} → ${currentMapping.label || '已映射'}`
    : isSelected
      ? `#${personId} 目标`
      : `#${personId}`;

  // The image container clips overflow, so a badge centred on a face near an edge would be cut off. The anchor is
  // never shifted (its rect is the natural position); only the badge inside it moves, back into the image.
  useLayoutEffect(() => {
    const container = markerRef.current?.parentElement;
    const update = () => {
      const anchor = badgeAnchorRef.current?.getBoundingClientRect();
      const bounds = container?.getBoundingClientRect();
      if (!anchor || !bounds) return;
      // A transformed ancestor would scale the offset again, so convert it back to local pixels.
      const scale = container && container.offsetWidth ? bounds.width / container.offsetWidth || 1 : 1;
      const { x, y } = keepInside(anchor, bounds);
      setBadgeShift((previous) =>
        previous.x === x / scale && previous.y === y / scale ? previous : { x: x / scale, y: y / scale },
      );
    };
    update();
    window.addEventListener('resize', update);
    const observer = typeof ResizeObserver === 'undefined' || !container ? null : new ResizeObserver(update);
    if (container) observer?.observe(container);
    return () => {
      window.removeEventListener('resize', update);
      observer?.disconnect();
    };
  }, [badgeText, box.left, box.top, box.width, box.height]);

  const activate = () => {
    onSelectTargetPersonId(personId);
    onToggle();
  };

  return (
    <>
      <div
        ref={markerRef}
        role="button"
        tabIndex={0}
        onClick={(e) => {
          e.stopPropagation();
          activate();
        }}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            activate();
          } else if (e.key === 'Escape' && isEditing) {
            e.preventDefault();
            e.stopPropagation();
            onClose();
          }
        }}
        style={{
          left: `${box.left}%`,
          top: `${box.top}%`,
          width: `${box.width}%`,
          height: `${box.height}%`,
        }}
        className={`absolute rounded-xl transition-all pointer-events-auto cursor-pointer flex flex-col items-center justify-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white ${
          isSelected || isEditing
            ? 'border-2 border-primary bg-primary/20 shadow-md shadow-indigo-600/30 ring-2 ring-primary/40 z-30'
            : currentMapping
              ? 'border-2 border-primary-border bg-primary/10 hover:border-primary z-20'
              : 'border-2 border-dashed border-white/50 bg-black/25 hover:border-primary-border hover:bg-primary/10 hover:scale-[1.01] z-10'
        }`}
        aria-label={`选择目标人物 ${personId}，位置人脸 ${face.index}`}
        aria-pressed={isSelected}
        aria-haspopup="dialog"
        aria-expanded={isEditing}
        title={`目标人物 #${personId} (点击选定并配置源人物)`}
      >
        <div
          ref={badgeAnchorRef}
          data-testid="face-badge-anchor"
          className="relative -translate-y-1/2 flex flex-col items-center"
        >
          <div
            data-testid="face-badge"
            style={{ transform: `translate(${badgeShift.x}px, ${badgeShift.y}px)` }}
            className={`px-2.5 py-0.5 rounded-full text-xs font-mono font-bold shadow-md transition-all whitespace-nowrap flex items-center gap-1 ${
              isSelected || isEditing
                ? 'bg-primary text-white shadow-glow-primary'
                : currentMapping
                  ? 'bg-primary-muted text-primary-soft border border-primary-border'
                  : 'bg-surface-3/95 text-zinc-200 border border-border'
            }`}
          >
            <span>{badgeText}</span>
          </div>
        </div>
      </div>
      {isEditing && (
        <FaceAssignPopover
          anchorRef={markerRef}
          personId={personId}
          faceIndex={face.index}
          currentMapping={currentMapping}
          sourcePeople={sourcePeople}
          sourceGallery={sourceGallery}
          distinctGlobalPersons={distinctGlobalPersons}
          onAssignSourcePerson={onAssignSourcePerson}
          onReassignFaceGlobalId={onReassignFaceGlobalId}
          onOpenMatrix={() => setIsMatrixOpen(true)}
          onClose={onClose}
        />
      )}
    </>
  );
};

export const CanvasFaceMarkersOverlay: React.FC<CanvasFaceMarkersOverlayProps> = ({
  faces,
  imageWidth,
  imageHeight,
  ...markerProps
}) => {
  const [editingFaceIndex, setEditingFaceIndex] = useState<number | null>(null);

  return (
    <div data-testid="face-markers-overlay" className="absolute inset-0 pointer-events-none z-20">
      {faces.map((face) => {
        const box = faceBoxToPercentage(face.bbox, imageWidth || 1, imageHeight || 1, FACE_MARKER_PADDING);
        if (!box) return null;
        const isEditing = editingFaceIndex === face.index;
        return (
          <FaceMarker
            key={face.index}
            face={face}
            box={box}
            isEditing={isEditing}
            onToggle={() => setEditingFaceIndex(isEditing ? null : face.index)}
            onClose={() => setEditingFaceIndex(null)}
            {...markerProps}
          />
        );
      })}
    </div>
  );
};
