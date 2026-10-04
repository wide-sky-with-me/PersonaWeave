import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, User, Users, X } from 'lucide-react';
import type { PersonMappingItem, SourcePerson, SourceReference } from '@/types';
import { sourceReferenceImageUrl } from '@/utils/sourceReferences';

/** Below this viewport width the popover becomes a bottom sheet: a 240px floating card cannot fit a phone canvas. */
export const SHEET_BREAKPOINT = 640;
const VIEWPORT_MARGIN = 8;
const ANCHOR_GAP = 8;

interface Placement {
  sheet: boolean;
  top: number;
  left: number;
  maxHeight: number;
}

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), Math.max(min, max));

/**
 * Place a popover of the given size next to its anchor inside the viewport: below the anchor when it fits,
 * otherwise above it, otherwise pinned to the viewport edge. Pure so the geometry can be tested directly.
 */
export function computePlacement(
  anchor: Pick<DOMRect, 'top' | 'bottom' | 'left' | 'width'>,
  size: { width: number; height: number },
  viewport: { width: number; height: number },
): Placement {
  if (viewport.width < SHEET_BREAKPOINT) {
    return { sheet: true, top: 0, left: 0, maxHeight: Math.round(viewport.height * 0.75) };
  }
  const maxHeight = viewport.height - 2 * VIEWPORT_MARGIN;
  const height = Math.min(size.height, maxHeight);
  const roomBelow = viewport.height - anchor.bottom - ANCHOR_GAP - VIEWPORT_MARGIN;
  const roomAbove = anchor.top - ANCHOR_GAP - VIEWPORT_MARGIN;
  const top =
    height <= roomBelow
      ? anchor.bottom + ANCHOR_GAP
      : height <= roomAbove
        ? anchor.top - ANCHOR_GAP - height
        : clamp(anchor.bottom + ANCHOR_GAP, VIEWPORT_MARGIN, viewport.height - height - VIEWPORT_MARGIN);
  const left = clamp(
    anchor.left + anchor.width / 2 - size.width / 2,
    VIEWPORT_MARGIN,
    viewport.width - size.width - VIEWPORT_MARGIN,
  );
  return { sheet: false, top, left, maxHeight };
}

interface FaceAssignPopoverProps {
  anchorRef: React.RefObject<HTMLElement | null>;
  personId: number;
  faceIndex: number;
  currentMapping: PersonMappingItem | undefined;
  sourcePeople: SourcePerson[];
  sourceGallery: SourceReference[];
  distinctGlobalPersons: number[];
  onAssignSourcePerson: (targetPersonId: number, faceIndex: number, sourcePersonId: number | null) => void;
  onReassignFaceGlobalId: (faceIndex: number, newPersonId: number) => void;
  onOpenMatrix: () => void;
  onClose: () => void;
}

/**
 * Quick identity assignment for one detected face.
 *
 * It is rendered through a portal so the canvas container's overflow clipping cannot hide it, and it is a
 * sibling of the face marker in the React tree (never a child), otherwise keyboard events from its buttons
 * would bubble into the marker's own Enter/Space handler.
 */
export const FaceAssignPopover: React.FC<FaceAssignPopoverProps> = ({
  anchorRef,
  personId,
  faceIndex,
  currentMapping,
  sourcePeople,
  sourceGallery,
  distinctGlobalPersons,
  onAssignSourcePerson,
  onReassignFaceGlobalId,
  onOpenMatrix,
  onClose,
}) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState<Placement | null>(null);

  useLayoutEffect(() => {
    const update = () => {
      const anchor = anchorRef.current?.getBoundingClientRect();
      const dialog = dialogRef.current?.getBoundingClientRect();
      if (!anchor || !dialog) return;
      setPlacement(
        computePlacement(
          anchor,
          { width: dialog.width, height: dialog.height },
          { width: window.innerWidth, height: window.innerHeight },
        ),
      );
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [anchorRef]);

  // Move focus into the dialog on open; give it back to the marker when the dialog goes away while it holds focus.
  useEffect(() => {
    const dialog = dialogRef.current;
    const anchor = anchorRef.current;
    dialog?.focus({ preventScroll: true });
    return () => {
      const active = document.activeElement;
      if (!active || active === document.body || dialog?.contains(active)) anchor?.focus({ preventScroll: true });
    };
  }, [anchorRef]);

  // A pointer press outside both the dialog and its marker dismisses it.
  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      const target = event.target as Node;
      if (dialogRef.current?.contains(target) || anchorRef.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [anchorRef, onClose]);

  const finish = (action: () => void) => () => {
    action();
    onClose();
  };

  const positionClass = placement?.sheet ? 'fixed inset-x-0 bottom-0 rounded-t-2xl' : 'fixed w-[260px] rounded-2xl';
  const style: React.CSSProperties = placement?.sheet
    ? { maxHeight: placement.maxHeight }
    : {
        top: placement?.top ?? 0,
        left: placement?.left ?? 0,
        maxHeight: placement?.maxHeight,
        // Measure at the final width before the first placement to avoid a visible jump.
        visibility: placement ? 'visible' : 'hidden',
      };

  return createPortal(
    <div
      ref={dialogRef}
      role="dialog"
      aria-label={`人脸 #${personId} 快速指派`}
      tabIndex={-1}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          onClose();
        }
      }}
      style={style}
      className={`${positionClass} z-[60] overflow-y-auto p-3.5 bg-surface-2/95 border border-border shadow-elevated backdrop-blur-xl flex flex-col gap-2.5 outline-none animate-in fade-in`}
    >
      <div className="text-xs text-zinc-300 font-sans font-semibold flex items-center justify-between pb-2 border-b border-border">
        <span className="flex items-center gap-1.5 text-primary-soft">
          <User className="w-3.5 h-3.5" />
          人脸 #{personId} 快速指派
        </span>
        <button
          type="button"
          onClick={onClose}
          className="text-zinc-400 hover:text-white p-1 rounded transition-colors"
          aria-label="关闭指派菜单"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="space-y-1.5">
        <div className="text-2xs font-semibold text-zinc-400 uppercase tracking-wider">选择源人物:</div>
        {sourcePeople.length === 0 ? (
          <p className="text-xs text-zinc-400 py-1">左侧图库尚未添加源人物素材</p>
        ) : (
          <div className="flex flex-col gap-1 max-h-40 overflow-y-auto pr-0.5">
            {sourcePeople.map((person) => {
              const isAssigned = currentMapping?.label === person.name;
              const references = sourceGallery.filter((r) => r.personId === person.id);
              const cover = references[0];
              const coverUrl = cover ? sourceReferenceImageUrl(cover) : null;
              return (
                <button
                  key={person.id}
                  type="button"
                  onClick={finish(() => onAssignSourcePerson(personId, faceIndex, person.id))}
                  className={`flex items-center gap-2 px-2 py-1.5 rounded-xl text-xs transition-all border text-left cursor-pointer ${
                    isAssigned
                      ? 'bg-primary/15 text-primary-soft border-primary-border shadow-sm font-medium'
                      : 'bg-surface-3/60 text-zinc-200 border-border hover:bg-surface-hover hover:border-border-md'
                  }`}
                >
                  <div className="w-6 h-6 rounded-md overflow-hidden bg-surface-3 border border-border shrink-0 flex items-center justify-center">
                    {coverUrl ? (
                      <img src={coverUrl} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <User className="w-3 h-3 text-zinc-400" />
                    )}
                  </div>
                  <span className="truncate flex-1 font-medium">{person.name}</span>
                  <span className="text-2xs text-zinc-400 font-mono shrink-0">
                    {isAssigned ? (
                      <span className="flex items-center gap-0.5 text-primary-soft">
                        <Check className="w-3 h-3" />
                        映射
                      </span>
                    ) : (
                      `${references.length}张`
                    )}
                  </span>
                </button>
              );
            })}
          </div>
        )}
        {currentMapping && (
          <button
            type="button"
            onClick={finish(() => onAssignSourcePerson(personId, faceIndex, null))}
            className="w-full text-center text-xs font-medium text-zinc-400 hover:text-rose-400 py-1 transition-colors"
          >
            保持原样（不换此人）
          </button>
        )}
      </div>

      <div className="pt-2 border-t border-border space-y-1.5">
        <div className="flex items-center justify-between text-2xs text-zinc-400">
          <span className="font-semibold uppercase tracking-wider">编号归属:</span>
          <button
            type="button"
            onClick={finish(onOpenMatrix)}
            className="text-primary-soft hover:underline flex items-center gap-1 font-sans"
          >
            <Users className="w-3 h-3" />
            矩阵大表
          </button>
        </div>
        <div className="flex items-center gap-1 flex-wrap">
          {distinctGlobalPersons.map((gid) => (
            <button
              key={gid}
              type="button"
              aria-label={`归属人物 #${gid}`}
              onClick={finish(() => onReassignFaceGlobalId(faceIndex, gid))}
              className={`px-2 py-0.5 rounded text-xs font-mono font-bold transition-all cursor-pointer ${
                personId === gid
                  ? 'bg-primary text-white shadow-sm'
                  : 'bg-surface-3 text-zinc-300 hover:bg-surface-hover hover:text-white border border-border'
              }`}
            >
              #{gid}
            </button>
          ))}
          <button
            type="button"
            aria-label="分配为全新独立人物"
            onClick={finish(() => onReassignFaceGlobalId(faceIndex, Math.max(...distinctGlobalPersons, 0) + 1))}
            className="px-2 py-0.5 rounded text-xs font-sans text-primary-soft bg-primary-muted border border-primary-border hover:bg-primary/20 font-medium cursor-pointer"
            title="分配为全新独立人物"
          >
            +新人物
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
};
