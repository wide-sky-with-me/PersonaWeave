import React, { useState } from 'react';
import { User } from 'lucide-react';
import type { DetectedFaceBox, PersonMappingItem, SourcePerson, SourceReference } from '@/types';
import { FACE_MARKER_PADDING, faceBoxToPercentage } from '@/utils/imageViewport';

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

  return (
    <div className="absolute inset-0 pointer-events-none z-20">
      {faces.map((face) => {
        const personId = face.global_person_id ?? face.index;
        const isSelected = selectedTargetPersonId === personId;
        const currentMapping = personMappings.find((m) => m.person_index === personId);
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
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onSelectTargetPersonId(personId);
              }
            }}
            style={{
              left: `${box.left}%`,
              top: `${box.top}%`,
              width: `${box.width}%`,
              height: `${box.height}%`,
            }}
            className={`absolute rounded-xl transition-all pointer-events-auto cursor-pointer flex flex-col items-center justify-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white ${
              isSelected
                ? 'border-2 border-cyan-400 bg-cyan-400/25 shadow-[0_0_25px_rgba(6,182,212,0.6)] ring-4 ring-cyan-400/40 z-30'
                : currentMapping
                  ? 'border-2 border-cyan-400/70 bg-cyan-950/20 hover:border-cyan-300 z-20'
                  : 'border-2 border-dashed border-white/60 bg-black/25 hover:border-indigo-400 hover:bg-indigo-500/20 hover:scale-[1.02] z-10'
            }`}
            aria-label={`选择目标人物 ${personId}，位置人脸 ${face.index}`}
            aria-pressed={isSelected}
            title={`目标人物 #${personId} (点击选定，点击标签可调整全局编号或指定源人物)`}
          >
            {/* Number Badge with Consistent Global ID */}
            <div className="relative -translate-y-1/2 flex flex-col items-center">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setEditingFaceIndex(editingFaceIndex === face.index ? null : face.index);
                }}
                className={`px-2.5 py-0.5 rounded-full text-xs font-mono font-bold shadow-lg transition-all whitespace-nowrap flex items-center gap-1 ${
                  isSelected
                    ? 'bg-cyan-400 text-black border border-cyan-200 shadow-cyan-400/50'
                    : currentMapping
                      ? 'bg-indigo-950 text-cyan-200 border border-cyan-400/60 shadow-md'
                      : 'bg-zinc-900/95 text-zinc-100 border border-white/30 hover:border-cyan-400'
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
              {editingFaceIndex === face.index && (
                <div
                  onClick={(e) => e.stopPropagation()}
                  className="absolute top-full mt-1.5 p-3 rounded-2xl bg-zinc-950/95 border border-cyan-500/50 shadow-2xl backdrop-blur-md z-50 flex flex-col gap-2.5 min-w-[200px] max-w-[260px] animate-in fade-in zoom-in-95"
                >
                  <div className="text-xs text-zinc-300 font-sans font-semibold flex items-center justify-between pb-1.5 border-b border-white/10">
                    <span className="flex items-center gap-1 text-cyan-300">
                      <User className="w-3.5 h-3.5" />
                      人脸 #{personId}
                    </span>
                    <button
                      onClick={() => setEditingFaceIndex(null)}
                      className="text-zinc-500 hover:text-white p-0.5 rounded transition-colors"
                    >
                      ✕
                    </button>
                  </div>

                  {/* Assign to Source Person */}
                  <div className="space-y-1.5">
                    <div className="text-xs font-semibold text-zinc-300">指定替换为:</div>
                    {sourcePeople.length === 0 ? (
                      <p className="text-xs text-zinc-400 py-1">左侧图库尚未添加源人物素材</p>
                    ) : (
                      <div className="flex flex-col gap-1 max-h-36 overflow-y-auto pr-0.5">
                        {sourcePeople.map((person) => {
                          const isAssigned = currentMapping?.label === person.name;
                          const refsCount = sourceGallery.filter((r) => r.personId === person.id).length;
                          return (
                            <button
                              key={person.id}
                              onClick={() => {
                                onAssignSourcePerson(personId, face.index, person.id);
                                setEditingFaceIndex(null);
                              }}
                              className={`flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs transition-all border text-left ${
                                isAssigned
                                  ? 'bg-cyan-500/20 text-cyan-200 border-cyan-400/50 shadow-sm ring-1 ring-cyan-400/30 font-medium'
                                  : 'bg-zinc-900/80 text-zinc-300 border-white/5 hover:bg-white/10 hover:text-white'
                              }`}
                            >
                              <span className="truncate">{person.name}</span>
                              <span className="text-xs text-zinc-400 font-mono ml-2 shrink-0">
                                {isAssigned ? '✓ 已指派' : `${refsCount}张`}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                    {currentMapping && (
                      <button
                        onClick={() => {
                          onAssignSourcePerson(personId, face.index, null);
                          setEditingFaceIndex(null);
                        }}
                        className="w-full text-center text-xs font-medium text-zinc-300 hover:text-rose-400 py-1 transition-colors"
                      >
                        保持原样（不换此人）
                      </button>
                    )}
                  </div>

                  {/* Adjust Person Index */}
                  <div className="pt-1.5 border-t border-white/10 space-y-1">
                    <div className="text-xs font-semibold text-zinc-300">调整归属编号:</div>
                    <div className="flex items-center gap-1 flex-wrap">
                      {distinctGlobalPersons.map((gid) => (
                        <button
                          key={gid}
                          onClick={() => {
                            onReassignFaceGlobalId(face.index, gid);
                            setEditingFaceIndex(null);
                          }}
                          className={`px-2 py-0.5 rounded text-xs font-mono font-bold transition-all ${
                            personId === gid
                              ? 'bg-cyan-500 text-black'
                              : 'bg-zinc-800 text-zinc-300 hover:bg-white/10 hover:text-white'
                          }`}
                        >
                          #{gid}
                        </button>
                      ))}
                      <button
                        onClick={() => {
                          const maxId = Math.max(...distinctGlobalPersons, 0);
                          onReassignFaceGlobalId(face.index, maxId + 1);
                          setEditingFaceIndex(null);
                        }}
                        className="px-2 py-0.5 rounded text-xs font-sans text-indigo-200 bg-indigo-900/40 border border-indigo-500/30 hover:bg-indigo-800/60 font-medium"
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
