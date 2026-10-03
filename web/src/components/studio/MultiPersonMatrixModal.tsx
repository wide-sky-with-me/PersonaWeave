import React, { useState, useRef, useEffect } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { motion } from 'framer-motion';
import { AlertCircle, Users, X, ArrowRight, CheckCircle2, Layers, Wand2, RotateCcw } from 'lucide-react';
import { useStudioStore } from '@/stores/useStudioStore';
import type { PersonMappingItem } from '@/types';
import { buildFacePositionLabels } from '@/utils/faceTopology';
import { sourceReferenceImageUrl, splitSourceReferences } from '@/utils/sourceReferences';
import { useToast } from '@/components/ui/ToastProvider';
import { hasValidPersonIndices } from '@/utils/swapPayload';

/* ── Face crop thumbnail ─────────────────────────────────────── */
const TargetFaceCrop: React.FC<{
  imageUrl: string;
  bbox: number[];
  personId: number;
}> = ({ imageUrl, bbox, personId }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!canvasRef.current || !imageUrl || !bbox || bbox.length !== 4) return;
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.src = imageUrl;
    img.onload = () => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      const [x1, y1, x2, y2] = bbox;
      const w = Math.max(1, x2 - x1);
      const h = Math.max(1, y2 - y1);
      const marginX = w * 0.25;
      const marginY = h * 0.25;
      const cropX = Math.max(0, x1 - marginX);
      const cropY = Math.max(0, y1 - marginY);
      const cropW = Math.min(img.width - cropX, w + marginX * 2);
      const cropH = Math.min(img.height - cropY, h + marginY * 2);

      canvas.width = 72;
      canvas.height = 72;
      ctx.clearRect(0, 0, 72, 72);
      ctx.drawImage(img, cropX, cropY, cropW, cropH, 0, 0, 72, 72);
    };
  }, [imageUrl, bbox]);

  return (
    <div className="relative shrink-0">
      <canvas ref={canvasRef} className="w-12 h-12 rounded-xl object-cover border border-accent-border bg-surface-3" />
      <div className="absolute -top-1.5 -left-1.5 px-1.5 py-0.5 rounded-md bg-accent text-black text-xs font-mono font-bold shadow">
        #{personId}
      </div>
    </div>
  );
};

/* ── Main modal ─────────────────────────────────────────────── */
export const MultiPersonMatrixModal: React.FC = () => {
  const {
    isMatrixOpen,
    setIsMatrixOpen,
    targetImages,
    sourceGallery,
    sourcePeople,
    personMappings,
    setPersonMappings,
    parameters,
  } = useStudioStore();

  const batchPeople = Array.from(
    new Map(
      targetImages.flatMap((image, imageIndex) => {
        const positionLabels = buildFacePositionLabels(image.faces || []);
        return (image.faces || []).map((face) => {
          const personId = face.global_person_id ?? face.index;
          return [
            personId,
            { personId, face, image, imageIndex, positionLabel: positionLabels.get(face.index) },
          ] as const;
        });
      }),
    ).values(),
  ).sort((a, b) => a.personId - b.personId);

  const sourcePeopleWithReferences = sourcePeople.map((person) => ({
    person,
    references: sourceGallery.filter((reference) => reference.personId === person.id),
  }));

  const personIdForMapping = (mapping: PersonMappingItem) => {
    if (mapping.source_identity_id) {
      return sourcePeople.find((person) => person.libraryIdentityId === mapping.source_identity_id)?.id;
    }
    return sourcePeopleWithReferences.find(({ references }) => {
      const { source_asset_ids, source_manual_images } = splitSourceReferences(references);
      return (
        source_asset_ids.length === mapping.source_asset_ids.length &&
        source_manual_images.length === mapping.source_manual_images.length &&
        source_asset_ids.every((id) => mapping.source_asset_ids.includes(id)) &&
        source_manual_images.every((image) => mapping.source_manual_images.includes(image))
      );
    })?.person.id;
  };

  const [mappings, setMappings] = useState<Record<number, number>>(() =>
    Object.fromEntries(
      personMappings
        .filter(hasValidPersonIndices)
        .map((mapping) => [mapping.person_index, personIdForMapping(mapping)])
        .filter((entry): entry is [number, number] => entry[1] !== undefined),
    ),
  );
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const toast = useToast();
  const reportError = (message: string) => {
    setErrorMessage(message);
    toast({ title: '人物映射未完成', description: message, kind: 'error' });
  };
  useEffect(() => {
    if (!isMatrixOpen) return;
    setErrorMessage(null);
    setMappings(
      Object.fromEntries(
        personMappings
          .filter(hasValidPersonIndices)
          .map((mapping) => [mapping.person_index, personIdForMapping(mapping)])
          .filter((entry): entry is [number, number] => entry[1] !== undefined),
      ),
    );
  }, [isMatrixOpen, personMappings, sourceGallery, sourcePeople]);
  const handleSelectSourcePerson = (targetPersonId: number, sourcePersonId: number) => {
    setMappings((current) =>
      current[targetPersonId] === sourcePersonId
        ? Object.fromEntries(Object.entries(current).filter(([personId]) => Number(personId) !== targetPersonId))
        : { ...current, [targetPersonId]: sourcePersonId },
    );
  };

  const handleAutoAssign = () => {
    if (sourcePeopleWithReferences.length === 0 || batchPeople.length === 0) return;
    const availablePeople = sourcePeopleWithReferences.filter(({ references }) => references.length > 0);
    setErrorMessage(null);
    setMappings(
      Object.fromEntries(
        batchPeople
          .slice(0, availablePeople.length)
          .map(({ personId }, index) => [personId, availablePeople[index].person.id]),
      ),
    );
  };
  const handleClearAllMappings = () => {
    setErrorMessage(null);
    setMappings({});
  };
  const handleExecuteMultiSwap = async () => {
    let personMappingPayload: PersonMappingItem[];
    try {
      personMappingPayload = Object.entries(mappings).map(([personIdValue, sourcePersonId]) => {
        const personId = Number(personIdValue);
        const anchor = batchPeople.find((person) => person.personId === personId);
        if (!anchor) throw new Error(`合影人物 ${personId} 缺少参考位置`);
        const sourceEntry = sourcePeopleWithReferences.find(({ person }) => person.id === sourcePersonId);
        if (!sourceEntry || !sourceEntry.references.length) throw new Error('已选择的源人物没有可用素材，请重新绑定');
        const { source_asset_ids, source_manual_images } = splitSourceReferences(sourceEntry.references);
        return {
          person_index: personId,
          target_reference_url: anchor.image.base64Data || anchor.image.url,
          target_reference_person_index: anchor.face.index,
          source_identity_id:
            source_asset_ids.length === 0 && source_manual_images.length === 0
              ? sourceEntry.person.libraryIdentityId
              : undefined,
          source_asset_ids,
          source_manual_images,
          source_mode: parameters.source_mode,
          label: sourceEntry.person.name,
        };
      });
    } catch (err) {
      reportError(err instanceof Error ? err.message : '保存映射失败，请检查每个人物的绑定是否仍然有效');
      return;
    }

    if (personMappingPayload.length === 0) {
      reportError('请至少为一位任务人物分配替换源照片。');
      return;
    }
    setPersonMappings(personMappingPayload);
    setIsMatrixOpen(false);
  };

  const configuredCount = Object.keys(mappings).length;
  return (
    <Dialog.Root open={isMatrixOpen} onOpenChange={setIsMatrixOpen}>
      <Dialog.Portal>
        <Dialog.Overlay asChild forceMount>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[90] bg-black/70 backdrop-blur-sm"
          />
        </Dialog.Overlay>
        <Dialog.Content asChild forceMount>
          <motion.div
            // Framer Motion owns the transform property, so centering must live in
            // its x/y values instead of Tailwind translate classes.
            initial={{ opacity: 0, scale: 0.96, x: '-50%', y: 'calc(-50% + 16px)' }}
            animate={{ opacity: 1, scale: 1, x: '-50%', y: '-50%' }}
            exit={{ opacity: 0, scale: 0.96, x: '-50%', y: 'calc(-50% + 16px)' }}
            transition={{ type: 'spring', stiffness: 300, damping: 25 }}
            className="fixed left-1/2 top-1/2 z-[91] w-[calc(100%-2rem)] max-w-4xl max-h-[88dvh] bg-surface-2 border border-border rounded-2xl shadow-elevated flex flex-col overflow-hidden"
          >
            {/* ── Header ── */}
            <div className="px-5 py-3.5 border-b border-border flex items-center justify-between shrink-0">
              <div className="flex items-center gap-3 min-w-0">
                <div className="icon-badge-accent w-8 h-8 rounded-xl shrink-0">
                  <Users className="w-4 h-4" />
                </div>
                <div className="min-w-0">
                  <Dialog.Title className="text-sm font-bold text-zinc-100 flex items-center gap-2 flex-wrap">
                    选择谁替换谁
                    <span className="chip chip-accent text-xs normal-case tracking-normal font-medium">
                      整个批次任务
                    </span>
                  </Dialog.Title>
                  <Dialog.Description className="text-xs text-zinc-400 mt-0.5">
                    按跨图片人物身份配置一次；批量处理时将按这些身份映射每张图片
                  </Dialog.Description>
                </div>
              </div>

              <div className="flex items-center gap-1.5 shrink-0 ml-3">
                {sourceGallery.length > 0 && batchPeople.length > 0 && (
                  <button
                    onClick={handleAutoAssign}
                    className="btn btn-sm btn-outline gap-1.5 text-xs text-zinc-200"
                    title="按当前显示顺序逐一配对，不会进行智能匹配"
                  >
                    <Wand2 className="w-3.5 h-3.5 text-primary-soft" />
                    <span>按顺序绑定</span>
                  </button>
                )}
                {configuredCount > 0 && (
                  <button
                    onClick={handleClearAllMappings}
                    className="btn btn-sm btn-ghost gap-1 text-xs text-zinc-300 hover:text-white"
                    title="清空所有绑定"
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">清空</span>
                  </button>
                )}
                <Dialog.Close asChild>
                  <button className="btn btn-sm btn-ghost p-1.5 rounded-lg" aria-label="关闭人物映射对话框">
                    <X className="w-4 h-4" />
                  </button>
                </Dialog.Close>
              </div>
            </div>
            <p className="px-5 pt-3 text-xs text-zinc-400 leading-normal">
              位置文案仅用于人工核对；实际处理按身份特征做全局最优匹配。“按顺序绑定”只影响源人像的初始配对。
            </p>
            {/* ── Body ── */}
            <div className="flex-1 overflow-y-auto p-4 min-h-0">
              {batchPeople.length === 0 ? (
                <div className="py-16 flex flex-col items-center justify-center gap-3 text-zinc-400">
                  <Users className="w-10 h-10 opacity-30" />
                  <p className="text-sm text-center">当前批次还没有完成跨图片人物识别。</p>
                </div>
              ) : (
                <div className="space-y-2.5">
                  {batchPeople.map(({ personId, face, image, positionLabel }) => {
                    const selectedSourcePersonId = mappings[personId];
                    const selectedSourceEntry = sourcePeopleWithReferences.find(
                      ({ person }) => person.id === selectedSourcePersonId,
                    );
                    return (
                      <div
                        key={personId}
                        className={`p-3.5 rounded-xl border transition-all ${
                          selectedSourcePersonId !== undefined
                            ? 'bg-surface-2 border-accent-border'
                            : 'bg-surface border-border hover:border-border-md'
                        }`}
                      >
                        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
                          {/* Target person */}
                          <div className="flex items-center gap-3 min-w-[200px]">
                            <TargetFaceCrop
                              imageUrl={image.base64Data || image.url}
                              bbox={face.bbox}
                              personId={personId}
                            />
                            <div>
                              <p className="text-xs font-semibold text-zinc-200">
                                合影人物 {personId}
                                <span className="ml-1.5 val-badge font-normal">
                                  {positionLabel || `人脸编号 ${face.index}`}
                                </span>
                              </p>
                              <div className="mt-1">
                                {selectedSourceEntry ? (
                                  <span className="chip chip-ok gap-1">
                                    <CheckCircle2 className="w-2.5 h-2.5" />
                                    已绑定 {selectedSourceEntry.person.name}
                                  </span>
                                ) : (
                                  <span className="chip chip-neutral">保持原貌</span>
                                )}
                              </div>
                            </div>
                          </div>

                          {/* Arrow */}
                          <ArrowRight className="hidden sm:block w-4 h-4 text-zinc-600 shrink-0" />

                          {/* Source picker */}
                          <div className="flex-1 w-full">
                            <div className="flex items-center justify-between mb-1.5">
                              <span className="text-xs font-medium text-zinc-300">选择要换入的源人像:</span>
                              {selectedSourcePersonId !== undefined && (
                                <button
                                  onClick={() =>
                                    setMappings((prev) => {
                                      const next = { ...prev };
                                      delete next[personId];
                                      return next;
                                    })
                                  }
                                  className="text-xs font-medium text-zinc-400 hover:text-red-400 transition-colors"
                                >
                                  取消绑定
                                </button>
                              )}
                            </div>

                            {sourcePeopleWithReferences.every(({ references }) => references.length === 0) ? (
                              <span className="text-xs text-amber-300 font-medium">
                                ⚠ 左侧图库为空，请先添加源人物素材
                              </span>
                            ) : (
                              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                                {sourcePeopleWithReferences
                                  .filter(({ references }) => references.length > 0)
                                  .map(({ person, references }) => {
                                    const isSelected = selectedSourcePersonId === person.id;
                                    const cover = references[0];
                                    return (
                                      <button
                                        key={person.id}
                                        type="button"
                                        onClick={() => handleSelectSourcePerson(personId, person.id)}
                                        className={`group flex min-w-0 items-center gap-2.5 rounded-xl border p-2 text-left transition-all cursor-pointer ${isSelected ? 'border-accent-border bg-accent-muted text-accent ring-1 ring-accent/30' : 'border-border bg-surface-3 text-zinc-200 hover:border-border-md hover:bg-surface-hover'}`}
                                      >
                                        <div className="relative h-10 w-10 shrink-0 overflow-hidden rounded-lg border border-border bg-surface-2">
                                          <img
                                            src={sourceReferenceImageUrl(cover)}
                                            alt={person.name}
                                            className="h-full w-full object-cover"
                                          />
                                          {isSelected && (
                                            <span className="absolute inset-0 flex items-center justify-center bg-accent/30">
                                              <CheckCircle2 className="h-4 w-4 text-white drop-shadow" />
                                            </span>
                                          )}
                                        </div>
                                        <div className="min-w-0 flex-1">
                                          <div className="truncate text-xs font-bold text-zinc-100">{person.name}</div>
                                          <div className="mt-0.5 text-xs text-zinc-400 font-medium">
                                            {references.length} 张素材
                                          </div>
                                        </div>
                                      </button>
                                    );
                                  })}
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
            {errorMessage && (
              <div
                role="alert"
                className="mx-5 mb-3 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300"
              >
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>{errorMessage}</span>
              </div>
            )}
            {/* ── Footer ── */}
            <div className="px-3 py-3.5 border-t border-border flex flex-wrap items-center justify-between gap-3 shrink-0 sm:px-5">
              <div className="flex items-center gap-2 text-xs text-zinc-300 font-medium">
                <Layers className="w-4 h-4 text-accent" />
                <span>
                  已配置 <span className="text-white font-bold font-mono">{configuredCount}</span> /{' '}
                  {batchPeople.length} 位
                </span>
              </div>

              <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
                <Dialog.Close asChild>
                  <button className="btn btn-sm btn-ghost">取消</button>
                </Dialog.Close>
                <button
                  onClick={handleExecuteMultiSwap}
                  disabled={batchPeople.length === 0 || configuredCount === 0}
                  className="btn btn-md btn-primary max-w-full"
                >
                  <>
                    <Layers className="w-3.5 h-3.5" />
                    <span>保存任务映射 ({configuredCount}人)</span>
                  </>
                </button>
              </div>
            </div>
          </motion.div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
};
