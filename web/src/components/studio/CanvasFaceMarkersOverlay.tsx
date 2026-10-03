import React, { useState } from 'react';
import { User, Users, Check, X } from 'lucide-react';
import type { DetectedFaceBox, PersonMappingItem, SourcePerson, SourceReference } from '@/types';
import { FACE_MARKER_PADDING, faceBoxToPercentage } from '@/utils/imageViewport';
import { sourceReferenceImageUrl } from '@/utils/sourceReferences';
import { useStudioStore } from '@/stores/useStudioStore';

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

export const CanvasFaceMarkersOverlay: React.FC<CanvasFaceMarkersOverlayProps> = ({
  faces,
  imageWidth,
  imageHeight,
  selectedTargetPersonId,
  onSelectTargetPersonId,
  personMappings,
  sourcePeople,
  sourceGallery,
  distinctGlobalPersons,
  onAssignSourcePerson,
  onReassignFaceGlobalId,
}) => {
  const [editingFaceIndex, setEditingFaceIndex] = useState<number | null>(null);
  const setIsMatrixOpen = useStudioStore((state) => state.setIsMatrixOpen);

  return (
    <div className="absolute inset-0 pointer-events-none z-20">
      {faces.map((face) => {
        const personId = face.global_person_id ?? face.index;
        const isSelected = selectedTargetPersonId === personId;
        const currentMapping = personMappings.find((m) => m.person_index === personId);
        const isEditing = editingFaceIndex === face.index;
        const w = imageWidth || 1;
        const h = imageHeight || 1;
        const box = faceBoxToPercentage(face.bbox, w, h, FACE_MARKER_PADDING);
        if (!box) return null;

        return (
          <div
            role="button"
            tabIndex={0}
            key={face.index}
            onClick={(e) => {
              e.stopPropagation();
              onSelectTargetPersonId(personId);
              setEditingFaceIndex(isEditing ? null : face.index);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onSelectTargetPersonId(personId);
                setEditingFaceIndex(isEditing ? null : face.index);
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
            title={`目标人物 #${personId} (点击选定并配置源人物)`}
          >
            {/* Number Badge with Consistent Global ID */}
            <div className="relative -translate-y-1/2 flex flex-col items-center">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectTargetPersonId(personId);
                  setEditingFaceIndex(isEditing ? null : face.index);
                }}
                className={`px-2.5 py-0.5 rounded-full text-xs font-mono font-bold shadow-md transition-all whitespace-nowrap flex items-center gap-1 cursor-pointer ${
                  isSelected || isEditing
                    ? 'bg-primary text-white shadow-glow-primary'
                    : currentMapping
                      ? 'bg-primary-muted text-primary-soft border border-primary-border'
                      : 'bg-surface-3/95 text-zinc-200 border border-border hover:border-border-md'
                }`}
                title="点击手动指定替换源或修改人物编号"
              >
                <span>
                  {currentMapping
                    ? `#${personId} → ${currentMapping.label || '已映射'}`
                    : isSelected
                      ? `#${personId} 目标`
                      : `#${personId}`}
                </span>
              </button>

              {/* Quick Identity Assignment & ID Reassignment Popover */}
              {isEditing && (
                <div
                  onClick={(e) => e.stopPropagation()}
                  className="absolute top-full mt-2 p-3.5 rounded-2xl bg-surface-2/95 border border-border shadow-elevated backdrop-blur-xl z-50 flex flex-col gap-2.5 min-w-[240px] max-w-[280px] animate-in fade-in zoom-in-95"
                >
                  <div className="text-xs text-zinc-300 font-sans font-semibold flex items-center justify-between pb-2 border-b border-border">
                    <span className="flex items-center gap-1.5 text-primary-soft">
                      <User className="w-3.5 h-3.5" />
                      人脸 #{personId} 快速指派
                    </span>
                    <button
                      type="button"
                      onClick={() => setEditingFaceIndex(null)}
                      className="text-zinc-400 hover:text-white p-0.5 rounded transition-colors"
                      aria-label="关闭指派菜单"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>

                  {/* Assign to Source Person */}
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
                              onClick={() => {
                                onAssignSourcePerson(personId, face.index, person.id);
                                setEditingFaceIndex(null);
                              }}
                              className={`flex items-center gap-2 px-2 py-1.5 rounded-xl text-xs transition-all border text-left cursor-pointer ${
                                isAssigned
                                  ? 'bg-primary/15 text-primary-soft border-primary-border shadow-sm font-medium'
                                  : 'bg-surface-3/60 text-zinc-200 border-border hover:bg-surface-hover hover:border-border-md'
                              }`}
                            >
                              <div className="w-6 h-6 rounded-md overflow-hidden bg-surface-3 border border-border shrink-0 flex items-center justify-center">
                                {coverUrl ? (
                                  <img src={coverUrl} alt={person.name} className="w-full h-full object-cover" />
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
                        onClick={() => {
                          onAssignSourcePerson(personId, face.index, null);
                          setEditingFaceIndex(null);
                        }}
                        className="w-full text-center text-xs font-medium text-zinc-400 hover:text-rose-400 py-1 transition-colors"
                      >
                        保持原样（不换此人）
                      </button>
                    )}
                  </div>

                  {/* Adjust Person Index */}
                  <div className="pt-2 border-t border-border space-y-1.5">
                    <div className="flex items-center justify-between text-2xs text-zinc-400">
                      <span className="font-semibold uppercase tracking-wider">编号归属:</span>
                      <button
                        type="button"
                        onClick={() => {
                          setEditingFaceIndex(null);
                          setIsMatrixOpen(true);
                        }}
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
                          onClick={() => {
                            onReassignFaceGlobalId(face.index, gid);
                            setEditingFaceIndex(null);
                          }}
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
                        onClick={() => {
                          const maxId = Math.max(...distinctGlobalPersons, 0);
                          onReassignFaceGlobalId(face.index, maxId + 1);
                          setEditingFaceIndex(null);
                        }}
                        className="px-2 py-0.5 rounded text-xs font-sans text-primary-soft bg-primary-muted border border-primary-border hover:bg-primary/20 font-medium cursor-pointer"
                        title="分配为全新独立人物"
                      >
                        +新人物
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};
