import * as Dialog from '@radix-ui/react-dialog';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, Compass, ImagePlus, Library, Plus, Sparkles, Trash2, Upload, User, Users, X } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { useStudioStore } from '@/stores/useStudioStore';
import { api } from '@/services/api';
import { useToast } from '@/components/ui/ToastProvider';
import { PortraitLibraryModal } from './PortraitLibraryModal';
import { sourceReferenceImageUrl } from '@/utils/sourceReferences';
import { validateImageFiles } from '@/utils/imageFiles';

export const SourceGallery: React.FC = () => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const createFileInputRef = useRef<HTMLInputElement>(null);
  const previewUrlsRef = useRef<string[]>([]);
  const [newPersonName, setNewPersonName] = useState('');
  const [isCreatePersonOpen, setIsCreatePersonOpen] = useState(false);
  const [newPersonFiles, setNewPersonFiles] = useState<File[]>([]);
  const [newPersonPreviews, setNewPersonPreviews] = useState<string[]>([]);
  const [isCreatingPerson, setIsCreatingPerson] = useState(false);
  const [isLibraryOpen, setIsLibraryOpen] = useState(false);
  const [personToDelete, setPersonToDelete] = useState<{ id: number; name: string } | null>(null);
  const [previewReference, setPreviewReference] = useState<import('@/types').SourceReference | null>(null);
  const toast = useToast();
  const store = useStudioStore();
  const { sourcePeople, activeSourcePersonId, sourceGallery, parameters, isProcessing } = store;
  const activePerson = sourcePeople.find((person) => person.id === activeSourcePersonId) ?? null;
  const activeReferences = useMemo(
    () => sourceGallery.filter((reference) => reference.personId === activeSourcePersonId),
    [sourceGallery, activeSourcePersonId],
  );

  const analyzeAndAddFiles = async (files: File[], personId: number) => {
    const results = await Promise.all(
      files.map(
        (file, index) =>
          new Promise<boolean>((resolve) => {
            const reader = new FileReader();
            reader.onload = async () => {
              const base64Data = reader.result as string;
              let yaw = 0;
              let yawLabel = '正脸';
              let qualityScore: number | undefined;
              let qualityTier: 'excellent' | 'usable' | 'low' | undefined;
              let qualityNotes: string[] = [];
              try {
                const result = await api.detectFaces({ image_url: base64Data });
                if (result?.faces?.length) {
                  yaw = Math.round((result.faces[0].yaw || 0) * 45);
                  yawLabel = Math.abs(yaw) < 8 ? '正脸' : yaw > 0 ? '+' + yaw + '° 右侧' : yaw + '° 左侧';
                  qualityScore = result.faces[0].quality_score;
                  qualityTier = result.faces[0].quality_tier;
                  qualityNotes = result.faces[0].quality_notes || [];
                }
              } catch {
                /* Pose analysis is optional. */
              }
              try {
                store.addSourceReference({
                  id: 'src_' + Date.now() + '_' + index + '_' + Math.random().toString(36).slice(2, 7),
                  name: file.name,
                  kind: 'manual',
                  imageData: base64Data,
                  yaw,
                  yawLabel,
                  qualityScore,
                  qualityTier,
                  qualityNotes,
                  personId,
                });
                resolve(true);
              } catch {
                resolve(false);
              }
            };
            reader.onerror = () => resolve(false);
            reader.readAsDataURL(file);
          }),
      ),
    );
    const imported = results.filter(Boolean).length;
    if (imported !== files.length) {
      toast({
        title: imported ? '部分照片导入失败' : '照片导入失败',
        description: `已导入 ${imported} / ${files.length} 张，请重试失败的照片。`,
        kind: 'error',
      });
    }
    return imported;
  };

  const revokePreviews = useCallback(() => {
    previewUrlsRef.current.forEach((preview) => URL.revokeObjectURL(preview));
    previewUrlsRef.current = [];
  }, []);

  useEffect(() => () => revokePreviews(), [revokePreviews]);

  const resetCreatePerson = () => {
    revokePreviews();
    setNewPersonName('');
    setNewPersonFiles([]);
    setNewPersonPreviews([]);
    setIsCreatePersonOpen(false);
    if (createFileInputRef.current) createFileInputRef.current.value = '';
  };

  const createPerson = async () => {
    const name = newPersonName.trim();
    if (!name || !newPersonFiles.length || isCreatingPerson) return;
    setIsCreatingPerson(true);
    try {
      const personId = store.addSourcePerson(name);
      const imported = await analyzeAndAddFiles(newPersonFiles, personId);
      if (imported === 0) {
        store.removeSourcePerson(personId);
        return;
      }
      resetCreatePerson();
    } finally {
      setIsCreatingPerson(false);
    }
  };

  const stagePersonFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    const remaining = Math.max(0, 8 - newPersonFiles.length);
    const { accepted, errors } = await validateImageFiles(Array.from(files).slice(0, remaining));
    const validFiles = accepted.map(({ file }) => file);
    setNewPersonFiles((current) => [...current, ...validFiles]);
    const previews = validFiles.map((file) => URL.createObjectURL(file));
    previewUrlsRef.current.push(...previews);
    setNewPersonPreviews((current) => [...current, ...previews]);
    if (errors.length) toast({ title: '部分图片无法使用', description: errors.join('；'), kind: 'error' });
    if (createFileInputRef.current) createFileInputRef.current.value = '';
  };

  const removeStagedFile = (index: number) => {
    const preview = newPersonPreviews[index];
    URL.revokeObjectURL(preview);
    previewUrlsRef.current = previewUrlsRef.current.filter((url) => url !== preview);
    setNewPersonFiles((current) => current.filter((_, itemIndex) => itemIndex !== index));
    setNewPersonPreviews((current) => current.filter((_, itemIndex) => itemIndex !== index));
  };

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    const personId = activeSourcePersonId;
    if (!files?.length || personId === null) return;
    const { accepted, errors } = await validateImageFiles(files);
    const validFiles = accepted.map(({ file }) => file);
    const imported = await analyzeAndAddFiles(validFiles, personId);
    if (errors.length) toast({ title: '部分图片无法使用', description: errors.join('；'), kind: 'error' });
    if (imported === validFiles.length && imported > 0)
      toast({ title: '照片已导入', description: `成功添加 ${imported} 张参考照片。`, kind: 'success' });
    event.target.value = '';
  };

  const content = (
    <div className="flex h-full flex-col overflow-hidden bg-surface select-none">
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-border px-4">
        <div className="flex items-center gap-2">
          <div className="icon-badge-primary">
            <User className="h-4 w-4" />
          </div>
          <span className="text-xs font-bold text-zinc-100">1. 源人物素材</span>
        </div>
        <span className="val-badge text-xs font-medium">
          {sourcePeople.length} 人 · {sourceGallery.length} 图
        </span>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => setIsLibraryOpen(true)}
            disabled={isProcessing}
            className="group flex items-center justify-center gap-2 rounded-xl border border-primary-border bg-primary-muted px-2.5 py-2.5 text-xs font-semibold text-primary transition-all hover:-translate-y-0.5 hover:bg-primary/20 hover:shadow-primary disabled:opacity-40"
          >
            <Library className="h-4 w-4" />
            素材库选入
          </button>
          <button
            type="button"
            onClick={() => setIsCreatePersonOpen(true)}
            disabled={isProcessing}
            className="group flex items-center justify-center gap-2 rounded-xl border border-border bg-surface-2 px-2.5 py-2.5 text-xs font-semibold text-zinc-200 transition-all hover:-translate-y-0.5 hover:border-primary-border hover:bg-primary-muted hover:text-primary disabled:opacity-40"
          >
            <Plus className="h-4 w-4 transition-transform group-hover:rotate-90" />
            新建人物
          </button>
        </div>
        {sourcePeople.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border py-8 text-center text-zinc-400">
            <User className="mb-2 h-8 w-8 text-zinc-500" />
            <p className="text-xs font-medium text-zinc-300">先新增人物，再上传此人的参考图</p>
            <p className="mt-1 text-2xs text-zinc-400">支持一人上传多张不同角度的照片</p>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-2">
              {sourcePeople.map((person) => {
                const references = sourceGallery.filter((reference) => reference.personId === person.id);
                const cover = references[0];
                const isActive = activeSourcePersonId === person.id;
                return (
                  <button
                    key={person.id}
                    type="button"
                    onClick={() => store.setActiveSourcePersonId(person.id)}
                    className={
                      'group relative overflow-hidden rounded-xl border p-2 text-left transition-all ' +
                      (isActive
                        ? 'border-primary-border bg-primary-muted ring-1 ring-primary/40 shadow-sm'
                        : 'border-border bg-surface-2 hover:border-border-md hover:bg-surface-hover')
                    }
                    aria-pressed={isActive}
                  >
                    <div className="flex items-center gap-2">
                      <div className="h-11 w-11 shrink-0 overflow-hidden rounded-lg border border-border bg-surface-3">
                        {cover ? (
                          <img
                            src={sourceReferenceImageUrl(cover)}
                            alt={person.name}
                            className="h-full w-full object-cover"
                          />
                        ) : (
                          <div className="flex h-full w-full items-center justify-center">
                            <User className="h-5 w-5 text-zinc-500" />
                          </div>
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs font-bold text-zinc-100">{person.name}</div>
                        <div className="mt-1 flex items-center gap-1 text-xs text-zinc-400">
                          <span className="font-mono text-primary font-medium">#{person.id}</span>
                          <span>·</span>
                          <span className="font-medium">{references.length} 张</span>
                        </div>
                      </div>
                    </div>
                    {isActive && (
                      <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-primary shadow-primary" />
                    )}
                  </button>
                );
              })}
            </div>
            {activePerson && (
              <div className="rounded-xl border border-primary-border bg-primary/5 p-2.5">
                <div className="flex items-center gap-2">
                  <span className="shrink-0 font-mono text-xs font-bold text-primary">#{activePerson.id}</span>
                  <input
                    value={activePerson.name}
                    onChange={(event) => store.renameSourcePerson(activePerson.id, event.target.value)}
                    disabled={isProcessing}
                    className="h-8 min-w-0 flex-1 rounded-lg border border-border bg-surface-3 px-2.5 text-xs font-medium text-zinc-100 focus:outline-none focus:border-primary"
                    aria-label="人物名称"
                  />
                  <button
                    type="button"
                    disabled={isProcessing}
                    onClick={() => setPersonToDelete({ id: activePerson.id, name: activePerson.name })}
                    className="btn btn-sm btn-ghost p-1.5 text-zinc-400 hover:text-red-400 hover:bg-red-500/10"
                    aria-label={`删除人物 ${activePerson.name}`}
                    title="删除人物及其素材"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            )}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept="image/*"
              onChange={handleFileUpload}
              className="hidden"
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={isProcessing || !activePerson}
              className="group flex items-center justify-center gap-2 rounded-xl border border-dashed border-white/20 bg-surface-2/50 py-2.5 transition-all hover:border-primary/60 hover:bg-primary-muted disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Upload className="h-4 w-4 text-zinc-400 transition-colors group-hover:text-primary" />
              <span className="text-xs font-medium text-zinc-300 group-hover:text-primary">
                为 {activePerson?.name} 上传多张参考照片
              </span>
            </button>
            <div className="flex min-h-0 flex-1 flex-col gap-1.5">
              <AnimatePresence mode="popLayout">
                {activeReferences.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-border py-6 text-center text-xs font-medium text-zinc-400">
                    该人物还没有参考照片，请点击上方上传
                  </div>
                ) : (
                  activeReferences.map((reference) => {
                    const qualityLabel =
                      reference.qualityTier === 'excellent'
                        ? '优质'
                        : reference.qualityTier === 'usable'
                          ? '可用'
                          : reference.qualityTier === 'low'
                            ? '低质量'
                            : '未评分';
                    const qualityClass =
                      reference.qualityTier === 'excellent'
                        ? 'chip-ok'
                        : reference.qualityTier === 'low'
                          ? 'chip-err'
                          : 'chip-warn';
                    return (
                      <motion.div
                        key={reference.id}
                        initial={{ opacity: 0, y: 4 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.96 }}
                        className="group flex items-center gap-2.5 rounded-xl border border-border bg-surface-2 p-2 transition-all hover:border-border-md hover:bg-surface-hover"
                      >
                        <div
                          role="button"
                          tabIndex={0}
                          onClick={() => setPreviewReference(reference)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              setPreviewReference(reference);
                            }
                          }}
                          className="h-11 w-11 shrink-0 overflow-hidden rounded-lg border border-border bg-surface-3 cursor-zoom-in hover:ring-1 hover:ring-primary/50 transition-all"
                          title="点击查看大图"
                          aria-label={`查看 ${reference.name} 大图`}
                        >
                          <img
                            src={sourceReferenceImageUrl(reference)}
                            alt={reference.name}
                            className="h-full w-full object-cover"
                          />
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-medium text-zinc-200">{reference.name}</p>
                          <div className="mt-1 flex flex-wrap gap-1">
                            <span className="chip chip-primary gap-1 text-xs">
                              <Compass className="h-3 w-3" />
                              {reference.yawLabel || '正脸'}
                            </span>
                            <span className={`chip ${qualityClass} text-xs`} title={reference.qualityNotes?.join(', ')}>
                              {qualityLabel}
                            </span>
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => store.removeSourceReference(reference.id)}
                          className="shrink-0 rounded-lg p-1 text-zinc-400 opacity-0 transition-all hover:bg-err-muted hover:text-red-400 group-hover:opacity-100 focus-visible:opacity-100"
                          title="移除照片"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      </motion.div>
                    );
                  })
                )}
              </AnimatePresence>
            </div>
          </>
        )}
      </div>
      <div className="shrink-0 space-y-2 border-t border-border px-3.5 py-3 bg-surface-1/50">
        <div className="flex items-center justify-between">
          <p className="text-xs font-semibold text-zinc-300">多图特征融合策略</p>
          <span className="chip chip-neutral text-2xs font-mono">
            {parameters.source_mode === 'best-match' ? '姿态优选' : '均值融合'}
          </span>
        </div>
        <div className="seg-track">
          <button
            aria-pressed={parameters.source_mode === 'best-match'}
            disabled={isProcessing}
            onClick={() => store.updateParameters({ source_mode: 'best-match' })}
            className={
              parameters.source_mode === 'best-match'
                ? 'seg-item-primary flex-1 justify-center py-1.5'
                : 'seg-item flex-1 justify-center py-1.5'
            }
          >
            角度优选
          </button>
          <button
            aria-pressed={parameters.source_mode === 'gallery'}
            disabled={isProcessing}
            onClick={() => store.updateParameters({ source_mode: 'gallery' })}
            className={
              parameters.source_mode === 'gallery'
                ? 'seg-item-primary flex-1 justify-center py-1.5'
                : 'seg-item flex-1 justify-center py-1.5'
            }
          >
            多图融合
          </button>
        </div>
        <p className="text-xs text-zinc-400 leading-normal">
          {parameters.source_mode === 'best-match'
            ? '自动分析待换脸的角度，选取最相似姿态的素材进行高保真替换。'
            : '融合该人物所有参考图的特征平均向量，适合素材均匀充足的场景。'}
        </p>
      </div>
    </div>
  );
  return (
    <>
      <PortraitLibraryModal open={isLibraryOpen} onClose={() => setIsLibraryOpen(false)} />
      {content}
      <Dialog.Root
        open={isCreatePersonOpen}
        onOpenChange={(open) => {
          if (!open && !isCreatingPerson) resetCreatePerson();
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[70] bg-black/70 backdrop-blur-md" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[71] flex max-h-[90vh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-surface-2 shadow-elevated">
            <div className="relative shrink-0 overflow-hidden border-b border-border px-5 pb-5 pt-6">
              <div className="pointer-events-none absolute -right-10 -top-16 h-40 w-40 rounded-full bg-primary/15 blur-3xl" />
              <Dialog.Close
                disabled={isCreatingPerson}
                className="absolute right-4 top-4 rounded-lg p-1.5 text-zinc-500 hover:bg-surface-hover hover:text-zinc-200 disabled:opacity-40"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </Dialog.Close>
              <div className="relative flex items-start gap-3.5">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-primary-border bg-primary-muted text-primary shadow-primary">
                  <User className="h-5 w-5" />
                </div>
                <div>
                  <div className="mb-1 flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-widest text-primary">
                    <Sparkles className="h-3 w-3" />
                    新建身份
                  </div>
                  <Dialog.Title className="text-base font-semibold text-zinc-100">添加自定义人物</Dialog.Title>
                  <Dialog.Description className="mt-1 text-xs leading-relaxed text-zinc-500">
                    一次完成人物命名与参考素材准备，创建后可直接用于人物映射。
                  </Dialog.Description>
                </div>
              </div>
            </div>
            <div className="min-h-0 space-y-4 overflow-y-auto p-5">
              <label className="block">
                <span className="mb-2 block text-xs font-medium text-zinc-300">人物名称</span>
                <div className="relative">
                  <Users className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-600" />
                  <input
                    autoFocus
                    value={newPersonName}
                    onChange={(event) => setNewPersonName(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && newPersonFiles.length) void createPerson();
                    }}
                    disabled={isProcessing || isCreatingPerson}
                    placeholder="例如：主角、小林、品牌代言人"
                    className="h-11 w-full rounded-xl border border-border bg-surface-3 pl-10 pr-12 text-sm text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-primary-border focus:ring-2 focus:ring-primary/15"
                    maxLength={40}
                  />
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] font-mono text-zinc-600">
                    {newPersonName.length}/40
                  </span>
                </div>
              </label>
              <div>
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs font-medium text-zinc-300">
                    参考素材 <span className="text-err">*</span>
                  </span>
                  <span className="text-2xs text-zinc-500">{newPersonFiles.length}/8 张</span>
                </div>
                <input
                  ref={createFileInputRef}
                  type="file"
                  multiple
                  accept="image/*"
                  onChange={(event) => stagePersonFiles(event.target.files)}
                  className="hidden"
                />
                {newPersonFiles.length === 0 ? (
                  <button
                    type="button"
                    onClick={() => createFileInputRef.current?.click()}
                    disabled={isCreatingPerson}
                    className="group flex w-full flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-surface-3/60 px-5 py-8 text-center hover:border-primary/60 hover:bg-primary-muted"
                  >
                    <ImagePlus className="mb-3 h-5 w-5 text-zinc-500 group-hover:text-primary" />
                    <span className="text-xs font-semibold text-zinc-300">选择人物参考照片</span>
                    <span className="mt-1.5 text-2xs text-zinc-500">支持 JPG、PNG、WebP，可一次选择多张</span>
                  </button>
                ) : (
                  <div className="space-y-2.5">
                    <div className="grid grid-cols-4 gap-2">
                      {newPersonFiles.map((file, index) => (
                        <div
                          key={file.name + file.lastModified + index}
                          className="group relative aspect-square overflow-hidden rounded-xl border border-border bg-surface-3"
                        >
                          <img src={newPersonPreviews[index]} alt={file.name} className="h-full w-full object-cover" />
                          <button
                            type="button"
                            onClick={() => removeStagedFile(index)}
                            disabled={isCreatingPerson}
                            className="absolute right-1 top-1 rounded-md bg-black/70 p-1 text-zinc-300 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                            aria-label={`移除 ${file.name}`}
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </div>
                      ))}
                    </div>
                    {newPersonFiles.length < 8 && (
                      <button
                        type="button"
                        onClick={() => createFileInputRef.current?.click()}
                        disabled={isCreatingPerson}
                        className="flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-border py-2.5 text-xs text-zinc-500 hover:text-primary"
                      >
                        <Plus className="h-3.5 w-3.5" />
                        继续添加照片
                      </button>
                    )}
                  </div>
                )}
              </div>
              <div className="rounded-xl border border-border bg-surface-3/70 p-3 text-xs text-zinc-500">
                优先选择无遮挡正脸，再补充左右微侧与自然表情。创建时会自动分析每张照片的角度与质量。
              </div>
            </div>
            <footer className="flex shrink-0 items-center justify-between border-t border-border bg-surface/40 px-5 py-4">
              <Dialog.Close disabled={isCreatingPerson} className="btn btn-md btn-ghost text-zinc-400">
                取消
              </Dialog.Close>
              <button
                type="button"
                onClick={() => void createPerson()}
                disabled={isProcessing || isCreatingPerson || !newPersonName.trim() || !newPersonFiles.length}
                className="btn btn-md btn-primary min-w-[158px] justify-center"
              >
                {isCreatingPerson ? (
                  '分析并创建中'
                ) : (
                  <>
                    创建人物 · {newPersonFiles.length} 张 <ArrowRight className="h-3.5 w-3.5" />
                  </>
                )}
              </button>
            </footer>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <Dialog.Root open={personToDelete !== null} onOpenChange={(open) => !open && setPersonToDelete(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[90] bg-black/70 backdrop-blur-sm" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[91] w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-border bg-surface-2 p-5 shadow-elevated">
            <Dialog.Title className="text-base font-semibold text-zinc-100">删除人物？</Dialog.Title>
            <Dialog.Description className="mt-2 text-sm leading-relaxed text-zinc-400">
              将删除“{personToDelete?.name}”及其全部参考图，此操作无法撤销。
            </Dialog.Description>
            <div className="mt-5 flex justify-end gap-2">
              <Dialog.Close className="btn btn-md btn-ghost">取消</Dialog.Close>
              <button
                type="button"
                className="btn btn-md bg-red-600 text-white hover:bg-red-500"
                onClick={() => {
                  if (personToDelete) store.removeSourcePerson(personToDelete.id);
                  setPersonToDelete(null);
                }}
              >
                确认删除
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <Dialog.Root open={previewReference !== null} onOpenChange={(open) => !open && setPreviewReference(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[90] bg-black/80 backdrop-blur-md" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[91] flex max-h-[90vh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-surface-2 shadow-elevated">
            <div className="flex items-center justify-between border-b border-border p-3.5">
              <div className="min-w-0">
                <Dialog.Title className="truncate text-xs font-semibold text-zinc-100">
                  {previewReference?.name}
                </Dialog.Title>
                <div className="mt-1 flex items-center gap-1.5">
                  <span className="chip chip-primary text-[10px]">
                    <Compass className="h-2.5 w-2.5" />
                    {previewReference?.yawLabel || '正脸'}
                  </span>
                  {previewReference?.qualityTier && (
                    <span className="chip chip-neutral text-[10px]">
                      {previewReference.qualityTier === 'excellent'
                        ? '优质质量'
                        : previewReference.qualityTier === 'usable'
                          ? '可用质量'
                          : '低质量'}
                    </span>
                  )}
                </div>
              </div>
              <Dialog.Close className="btn btn-sm btn-ghost p-1.5" aria-label="关闭预览">
                <X className="h-4 w-4" />
              </Dialog.Close>
            </div>
            <div className="flex flex-1 items-center justify-center p-4 bg-black/50 overflow-hidden">
              {previewReference && (
                <img
                  src={sourceReferenceImageUrl(previewReference)}
                  alt={previewReference.name}
                  className="max-h-[65vh] w-auto max-w-full rounded-xl object-contain shadow-2xl border border-border"
                />
              )}
            </div>
            {previewReference?.qualityNotes?.length ? (
              <div className="border-t border-border bg-surface-3/50 px-4 py-2.5 text-2xs text-zinc-400">
                质量诊断：{previewReference.qualityNotes.join('；')}
              </div>
            ) : null}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
};
