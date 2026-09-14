import React, { useState, useRef, useEffect, useMemo } from 'react';
import { Columns, Upload, Zap, Eye } from 'lucide-react';
import { useStudioStore } from '@/stores/useStudioStore';
import { api } from '@/services/api';
import { useWorkbenchStore } from '@/features/refinement/workbenchStore';
import { relabelFaceSafely } from '@/utils/faceTopology';
import type { PersonMappingItem, TargetImageItem } from '@/types';
import { useIdentityClustering } from '@/hooks/useIdentityClustering';
import { validateImageFiles } from '@/utils/imageFiles';
import { splitSourceReferences } from '@/utils/sourceReferences';
import { comparisonPositionFromPointer, fitImageWithinViewport, type ViewportSize } from '@/utils/imageViewport';
import { useToast } from '@/components/ui/ToastProvider';
import { useManualProtectionBrush } from '@/hooks/useManualProtectionBrush';
import { useStudioShortcuts } from '@/hooks/useStudioShortcuts';
import { CanvasTopToolbar } from './CanvasTopToolbar';
import { CanvasResultActionBar } from './CanvasResultActionBar';
import { CanvasFaceMarkersOverlay } from './CanvasFaceMarkersOverlay';
import { CanvasBatchFilmstrip } from './CanvasBatchFilmstrip';
import { CanvasBrushToolbar } from './CanvasBrushToolbar';
export const TargetCanvas: React.FC = () => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageStageRef = useRef<HTMLDivElement>(null);
  const imageContainerRef = useRef<HTMLDivElement>(null);
  const [imageViewportSize, setImageViewportSize] = useState<ViewportSize | null>(null);
  const [magnifierData, setMagnifierData] = useState<{
    x: number;
    y: number;
    imgWidth: number;
    imgHeight: number;
  } | null>(null);
  const [isArbitrating, setIsArbitrating] = useState(false);
  const [vlmReasoning, setVlmReasoning] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const workspaceView = useWorkbenchStore((state) => state.view);
  const openRefinement = useWorkbenchStore((state) => state.openTarget);
  const toast = useToast();
  const {
    sourceGallery,
    sourcePeople,
    personMappings,
    setPersonMappings,
    parameters,
    targetImages,
    activeTargetIndex,
    setActiveTargetIndex,
    addTargetImages,
    removeTargetImage,
    updateTargetImageById,
    batchUpdateTargetImages,
    selectedTargetPersonId,
    setSelectedTargetPersonId,
    viewMode,
    setViewMode,
    splitPosition,
    setSplitPosition,
    showFaceMarkers,
    setShowFaceMarkers,
    isProcessing,
    setManualMaskDataUrl,
    undoManualMask,
    redoManualMask,
    brushSize,
    setBrushSize,
    isEraser,
    setIsEraser,
  } = useStudioStore();

  const currentImage = targetImages[activeTargetIndex] || null;
  const manualMaskDataUrl = currentImage?.manualMask?.dataUrl ?? null;
  const canUndoMask = Boolean(currentImage?.manualMask?.undoStack.length);
  const canRedoMask = Boolean(currentImage?.manualMask?.redoStack.length);
  const {
    canvasRef: paintCanvasRef,
    clear: handleClearMask,
    handlePointerDown: handleBrushPointerDown,
    handlePointerEnd: handleBrushMouseUp,
    handlePointerMove: handleBrushPointerMove,
  } = useManualProtectionBrush({
    activeTargetIndex,
    brushSize,
    imageContainerRef,
    imageViewportHeight: imageViewportSize?.height,
    imageViewportWidth: imageViewportSize?.width,
    isEraser,
    maskDataUrl: manualMaskDataUrl,
    setMaskDataUrl: setManualMaskDataUrl,
    viewMode,
  });

  const [isHoldingOriginal, setIsHoldingOriginal] = useState(false);

  useStudioShortcuts({
    enabled: workspaceView === 'prepare' && Boolean(currentImage),
    viewMode,
    setViewMode,
    brushSize,
    setBrushSize,
    undoManualMask,
    redoManualMask,
    canUndoMask,
    canRedoMask,
    onHoldCompareChange: setIsHoldingOriginal,
  });

  const { isClustering, clusteringError } = useIdentityClustering({
    targetImages,
    updateTargetImageById,
    batchUpdateTargetImages,
  });

  useEffect(() => {
    const stage = imageStageRef.current;
    const imageWidth = currentImage?.imageWidth;
    const imageHeight = currentImage?.imageHeight;
    if (!stage || !imageWidth || !imageHeight) {
      setImageViewportSize(null);
      return;
    }

    const updateSize = () => {
      const styles = window.getComputedStyle(stage);
      const horizontalPadding =
        (Number.parseFloat(styles.paddingLeft) || 0) + (Number.parseFloat(styles.paddingRight) || 0);
      const verticalPadding =
        (Number.parseFloat(styles.paddingTop) || 0) + (Number.parseFloat(styles.paddingBottom) || 0);
      const next = fitImageWithinViewport(
        imageWidth,
        imageHeight,
        stage.clientWidth - horizontalPadding,
        stage.clientHeight - verticalPadding,
      );
      setImageViewportSize((current) =>
        current && next && Math.abs(current.width - next.width) < 0.5 && Math.abs(current.height - next.height) < 0.5
          ? current
          : next,
      );
    };

    updateSize();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(updateSize);
    observer.observe(stage);
    return () => observer.disconnect();
  }, [currentImage?.id, currentImage?.imageWidth, currentImage?.imageHeight]);

  // Extract all distinct global person IDs discovered across the entire batch
  const distinctGlobalPersons = useMemo(() => {
    const ids = new Set<number>();
    targetImages.forEach((t) => {
      t.faces?.forEach((f) => {
        const gid = f.global_person_id ?? f.index;
        if (gid) ids.add(gid);
      });
    });
    const sorted = Array.from(ids).sort((a, b) => a - b);
    return sorted;
  }, [targetImages]);

  // Handle batch images upload via input or drag-and-drop
  const processAndAddFiles = async (files: FileList | File[]) => {
    if (!files?.length) return;
    const { accepted, errors } = await validateImageFiles(files);
    const reads = accepted.map(
      ({ file, width, height }, idx) =>
        new Promise<TargetImageItem>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => {
            const base64Str = reader.result as string;
            resolve({
              id: `tgt_${Date.now()}_${idx}_${Math.random().toString(36).slice(2, 7)}`,
              name: file.name,
              url: base64Str,
              base64Data: base64Str,
              imageWidth: width,
              imageHeight: height,
              status: 'IDLE',
            });
          };
          reader.onerror = () => reject(new Error(`无法读取 ${file.name}`));
          reader.readAsDataURL(file);
        }),
    );
    const settled = await Promise.allSettled(reads);
    const newItems = settled.flatMap((result) => (result.status === 'fulfilled' ? [result.value] : []));
    const readErrors = settled.flatMap((result) =>
      result.status === 'rejected' ? [String(result.reason?.message ?? result.reason)] : [],
    );
    if (newItems.length) addTargetImages(newItems);
    if (errors.length || readErrors.length)
      toast({ title: '部分图片无法使用', description: [...errors, ...readErrors].join('；'), kind: 'error' });
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) await processAndAddFiles(e.target.files);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isDragOver) setIsDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setIsDragOver(false);
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
    if (e.dataTransfer?.files?.length) {
      await processAndAddFiles(e.dataTransfer.files);
    }
  };

  // The divider lives inside the image, so all pointer math must use that same viewport.
  const updateSplitFromPointer = (element: HTMLElement, clientX: number) => {
    const rect = element.getBoundingClientRect();
    const position = comparisonPositionFromPointer(clientX, rect.left, rect.width);
    if (position !== null) setSplitPosition(position);
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (viewMode === 'MAGNIFIER' && imageContainerRef.current) {
      const rect = imageContainerRef.current.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      if (x >= 0 && x <= rect.width && y >= 0 && y <= rect.height) {
        setMagnifierData({ x, y, imgWidth: rect.width, imgHeight: rect.height });
      } else {
        setMagnifierData(null);
      }
    }
  };

  // Reassign a specific face's Global Person ID manually without creating an impossible topology.
  const handleReassignFaceGlobalId = (faceIndex: number, newGid: number) => {
    if (!currentImage?.faces) return;
    const relabeled = relabelFaceSafely(currentImage.faces, faceIndex, newGid);
    if (!relabeled.ok) {
      setVlmReasoning(`⚠️ ${relabeled.message}`);
      setTimeout(() => setVlmReasoning(null), 8000);
      return;
    }
    updateTargetImageById(currentImage.id, { faces: relabeled.faces });
    setSelectedTargetPersonId(newGid);
    setVlmReasoning(`✨ 已手动将 ${faceIndex} 号位置人脸调整归属为全局目标人物 #${newGid}`);
    setTimeout(() => setVlmReasoning(null), 4000);
  };

  const handleVlmArbitrate = async () => {
    if (!currentImage || !currentImage.faces || currentImage.faces.length === 0) return;

    let refImageUrl = '';
    let refPersonIndex = 1;
    let targetGlobalId: number | null = selectedTargetPersonId;

    // 1. Identify which person we want to locate/correct in the current photo
    if (selectedTargetPersonId !== null) {
      // Find another photo in the batch that has this selectedTargetPersonId
      const otherImgWithPerson = targetImages.find(
        (t, idx) =>
          idx !== activeTargetIndex && t.faces?.some((f) => (f.global_person_id ?? f.index) === selectedTargetPersonId),
      );

      if (otherImgWithPerson && otherImgWithPerson.faces) {
        const refFace = otherImgWithPerson.faces.find(
          (f) => (f.global_person_id ?? f.index) === selectedTargetPersonId,
        );
        if (refFace) {
          refImageUrl = otherImgWithPerson.base64Data || otherImgWithPerson.url;
          refPersonIndex = refFace.index;
          targetGlobalId = selectedTargetPersonId;
        }
      }
    }

    // 2. If no reference found yet, look for orphan/isolated faces in current image
    if (!refImageUrl && targetImages.length > 1) {
      // Count frequency of each global ID across batch
      const idCounts = new Map<number, number>();
      targetImages.forEach((t) => {
        t.faces?.forEach((f) => {
          const gid = f.global_person_id ?? f.index;
          idCounts.set(gid, (idCounts.get(gid) || 0) + 1);
        });
      });

      // Find an orphan face in current image (frequency == 1)
      const orphanFace = currentImage.faces.find((f) => {
        const gid = f.global_person_id ?? f.index;
        return (idCounts.get(gid) || 0) <= 1;
      });

      // Find a global ID that is present in other images but missing from current image
      const currentGids = new Set(currentImage.faces.map((f) => f.global_person_id ?? f.index));
      let missingGid: number | null = null;
      for (const [gid, count] of idCounts.entries()) {
        if (count > 1 && !currentGids.has(gid)) {
          missingGid = gid;
          break;
        }
      }

      if (orphanFace && missingGid !== null) {
        // Find reference image containing missingGid
        const refImg = targetImages.find(
          (t, idx) => idx !== activeTargetIndex && t.faces?.some((f) => (f.global_person_id ?? f.index) === missingGid),
        );
        if (refImg && refImg.faces) {
          const refFace = refImg.faces.find((f) => (f.global_person_id ?? f.index) === missingGid);
          if (refFace) {
            refImageUrl = refImg.base64Data || refImg.url;
            refPersonIndex = refFace.index;
            targetGlobalId = missingGid;
          }
        }
      }
    }

    // 3. VLM identity references must be anchored by another target-batch face.
    if (!refImageUrl) {
      setVlmReasoning(
        '⚠️ 当前目标批次中没有可确认的同人物锚点。请重新运行身份聚类，或手动解决人物归属；不会使用源素材作为目标身份参考。',
      );
      setTimeout(() => setVlmReasoning(null), 8000);
      return;
    }
    if (targetGlobalId === null) {
      setVlmReasoning('⚠️ 请先选择要纠正的目标人物，再运行视觉仲裁');
      setTimeout(() => setVlmReasoning(null), 6000);
      return;
    }

    setIsArbitrating(true);
    try {
      const res = await api.arbitrateIdentity({
        reference_image_url: refImageUrl,
        reference_person_index: refPersonIndex,
        target_image_url: currentImage.base64Data || currentImage.url,
      });

      if (res) {
        if (res.is_success && res.matched_person_index) {
          const submittedTargetId = currentImage.id;
          const relabeled = relabelFaceSafely(currentImage.faces, res.matched_person_index, targetGlobalId);
          if (!relabeled.ok) {
            setVlmReasoning(`⚠️ ${relabeled.message}`);
            setTimeout(() => setVlmReasoning(null), 8000);
            return;
          }
          updateTargetImageById(submittedTargetId, { faces: relabeled.faces });
          setSelectedTargetPersonId(targetGlobalId);
          setVlmReasoning(
            `🤖 VLM 智能纠错成功：已将 #${res.matched_person_index} 号位置人脸精准归属为目标人物 #${targetGlobalId} (${Math.round(
              res.confidence * 100,
            )}% 置信度) — ${res.reasoning}`,
          );
        } else {
          setVlmReasoning(`⚠️ ${res.reasoning || '未检测到 VLM API Key，已降级回退至本地特征排序'}`);
        }
        setTimeout(() => setVlmReasoning(null), 8000);
      }
    } catch (err: any) {
      console.error('VLM arbitrate error:', err);
      setVlmReasoning(`❌ VLM 纠错调用失败: ${err?.message || '网络或服务端异常'}`);
      setTimeout(() => setVlmReasoning(null), 6000);
    } finally {
      setIsArbitrating(false);
    }
  };

  const handleDownloadCurrentResult = () => {
    const url = currentImage?.refinedUrl || currentImage?.processedUrl;
    if (!url) return;
    const link = document.createElement('a');
    link.href = url;
    const baseName = currentImage.name.replace(/\.[^/.]+$/, '') || 'result';
    link.download = `${baseName}_swapped.png`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    toast({ title: '已开始下载高清结果图', kind: 'info' });
  };

  const handleAssignSourcePerson = (targetPersonId: number, faceIndex: number, sourcePersonId: number | null) => {
    if (sourcePersonId === null) {
      setPersonMappings(personMappings.filter((m) => m.person_index !== targetPersonId));
      toast({ title: `人物 #${targetPersonId} 已设为保持原样（不换脸）`, kind: 'info' });
      return;
    }

    const sourceEntry = sourcePeople.find((p) => p.id === sourcePersonId);
    if (!sourceEntry) return;
    const references = sourceGallery.filter((r) => r.personId === sourcePersonId);
    const { source_asset_ids, source_manual_images } = splitSourceReferences(references);

    const newMapping: PersonMappingItem = {
      person_index: targetPersonId,
      target_reference_url: currentImage?.base64Data || currentImage?.url || '',
      target_reference_person_index: faceIndex,
      source_identity_id:
        source_asset_ids.length === 0 && source_manual_images.length === 0 ? sourceEntry.libraryIdentityId : undefined,
      source_asset_ids,
      source_manual_images,
      source_mode: parameters.source_mode,
      label: sourceEntry.name,
    };

    const nextMappings = personMappings.filter((m) => m.person_index !== targetPersonId);
    nextMappings.push(newMapping);
    setPersonMappings(nextMappings);

    toast({
      title: `已指定人物 #${targetPersonId} → ${sourceEntry.name}`,
      description: references.length ? `已绑定 ${references.length} 张参考图` : '该源人物暂无参考图，请在左侧上传',
      kind: 'success',
    });
  };

  const detectedFaces = currentImage?.faces || [];

  return (
    <div className="relative flex-1 flex flex-col h-full bg-[#0a0a0d] overflow-hidden select-none">
      {/* Hidden File Input for Batch / Single Target Uploads */}
      <input ref={fileInputRef} type="file" multiple accept="image/*" onChange={handleFileUpload} className="hidden" />

      {/* Top Toolbar */}
      <CanvasTopToolbar
        currentImageName={currentImage?.name}
        hasCurrentImage={Boolean(currentImage)}
        selectedTargetPersonId={selectedTargetPersonId}
        onSelectTargetPersonId={setSelectedTargetPersonId}
        distinctGlobalPersons={distinctGlobalPersons}
        canVlmArbitrate={targetImages.length > 1 || sourceGallery.length > 0}
        isArbitrating={isArbitrating}
        onVlmArbitrate={handleVlmArbitrate}
        showFaceMarkers={showFaceMarkers}
        onToggleShowFaceMarkers={() => setShowFaceMarkers(!showFaceMarkers)}
        viewMode={viewMode}
        onChangeViewMode={setViewMode}
      />

      {/* Brush Floating Toolbar */}
      {viewMode === 'BRUSH' && (
        <CanvasBrushToolbar
          isEraser={isEraser}
          onToggleEraser={setIsEraser}
          brushSize={brushSize}
          onChangeBrushSize={setBrushSize}
          canUndo={canUndoMask}
          onUndo={undoManualMask}
          canRedo={canRedoMask}
          onRedo={redoManualMask}
          hasMaskData={Boolean(manualMaskDataUrl)}
          onClear={handleClearMask}
        />
      )}

      {/* Center Main Stage / Canvas */}
      <div
        ref={imageStageRef}
        onPointerMove={handlePointerMove}
        onPointerLeave={() => {
          setMagnifierData(null);
        }}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className="flex-1 min-h-0 relative flex items-center justify-center p-5 overflow-hidden bg-dot-grid"
      >
        {isDragOver && (
          <div className="absolute inset-4 z-50 rounded-3xl border-2 border-dashed border-cyan-400 bg-cyan-950/80 backdrop-blur-md flex flex-col items-center justify-center gap-3 text-cyan-200 pointer-events-none animate-in fade-in shadow-[0_0_50px_rgba(6,182,212,0.25)]">
            <div className="w-16 h-16 rounded-2xl bg-cyan-500/20 border border-cyan-400/40 flex items-center justify-center text-cyan-300 shadow-cyan">
              <Upload className="w-8 h-8 animate-bounce" />
            </div>
            <p className="text-sm font-semibold tracking-tight">释放以添加图片至待处理批次</p>
            <p className="text-xs text-cyan-400/80 font-mono">支持多图批量拖入 · 自动提取面部特征</p>
          </div>
        )}

        {!currentImage ? (
          /* Empty State with Cyber/Studio Precision Blueprint design */
          <div className="flex flex-col items-center justify-center text-center p-8 max-w-md animate-in fade-in zoom-in-95">
            <div className="relative mb-6">
              <div className="w-20 h-20 rounded-3xl bg-surface-2 border border-white/10 flex items-center justify-center text-zinc-400 shadow-elevated relative z-10">
                <Upload className="w-9 h-9 text-primary animate-pulse" />
              </div>
              <div className="absolute -inset-2 rounded-3xl bg-primary/20 blur-xl -z-10" />
            </div>
            <h3 className="text-base font-semibold text-zinc-100 mb-1.5 tracking-tight">暂无待处理照片</h3>
            <p className="text-xs text-zinc-400 max-w-xs mb-6 leading-relaxed">
              支持单张写真或多人合影。拖拽照片至此区域，或点击下方按钮导入
            </p>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="px-5 py-2.5 bg-primary hover:bg-primary-hover text-white rounded-xl text-xs font-semibold shadow-primary active:scale-95 transition-all flex items-center gap-2 cursor-pointer"
            >
              <Upload className="w-4 h-4" />
              <span>选择待换图片</span>
            </button>
            <div className="mt-8 flex items-center gap-4 text-2xs text-zinc-500 font-mono">
              <span className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-primary/60" />
                自动人脸定位
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-cyan-400/60" />
                全局身份聚类
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400/60" />
                JPG · PNG · WebP
              </span>
            </div>
          </div>
        ) : (
          /* Loaded Canvas Viewport (Tightly bounded to image dimensions) */
          <div
            ref={imageContainerRef}
            data-testid="target-image-viewport"
            style={imageViewportSize || undefined}
            className="relative inline-block max-w-full rounded-2xl overflow-hidden shadow-2xl ring-1 ring-white/10 bg-black/70"
          >
            {/* Base Image */}
            <img
              src={
                !isHoldingOriginal && viewMode !== 'SPLIT' && (currentImage.refinedUrl || currentImage.processedUrl)
                  ? currentImage.refinedUrl || currentImage.processedUrl
                  : currentImage.url
              }
              alt="Main Canvas"
              data-testid="target-comparison-before-layer"
              onLoad={(e) => {
                const img = e.currentTarget;
                if (!currentImage.imageWidth || !currentImage.imageHeight) {
                  updateTargetImageById(currentImage.id, {
                    imageWidth: img.naturalWidth,
                    imageHeight: img.naturalHeight,
                  });
                }
              }}
              className={
                imageViewportSize
                  ? 'block h-full w-full object-fill object-left-top pointer-events-none select-none'
                  : 'block h-auto w-auto max-h-[calc(100vh-270px)] max-w-full object-fill object-left-top pointer-events-none select-none'
              }
            />

            {/* Hold to compare original indicator banner */}
            {isHoldingOriginal && Boolean(currentImage.refinedUrl || currentImage.processedUrl) && (
              <div className="absolute top-4 left-1/2 -translate-x-1/2 px-4 py-1.5 rounded-full bg-cyan-950/90 border border-cyan-400/50 text-cyan-200 text-xs font-semibold shadow-[0_0_25px_rgba(6,182,212,0.4)] backdrop-blur-md z-40 flex items-center gap-2 animate-in fade-in">
                <Eye className="w-3.5 h-3.5 text-cyan-400 animate-pulse" />
                <span>正在临时对比原图 (松开 \ 键还原)</span>
              </div>
            )}

            {/* Clustering indicator on top left of image */}
            {(isClustering || clusteringError) && (
              <div
                className={`absolute top-3 left-3 px-3 py-1.5 rounded-lg bg-black/85 backdrop-blur-md border text-xs font-medium flex items-center gap-2 shadow-lg z-30 ${clusteringError ? 'border-red-500/40 text-red-300' : 'border-cyan-500/40 text-cyan-400'}`}
              >
                <Zap className={`w-3.5 h-3.5 ${isClustering ? 'animate-spin' : ''}`} />
                <span>
                  {clusteringError
                    ? `身份聚类失败：${clusteringError}。修改图片或重新上传可重试。`
                    : '跨图全局人物身份聚类中...'}
                </span>
              </div>
            )}

            {/* VLM Reasoning Banner */}
            {vlmReasoning && (
              <div className="absolute top-3 left-1/2 -translate-x-1/2 px-4 py-2 rounded-xl bg-purple-950/90 backdrop-blur-xl border border-purple-500/60 text-purple-200 text-xs font-medium shadow-2xl z-40 max-w-lg text-center leading-relaxed animate-in fade-in slide-in-from-top-2">
                {vlmReasoning}
              </div>
            )}

            {/* 🎯 Interactive Face Markers Overlay with Global Consistent IDs */}
            {showFaceMarkers && detectedFaces.length > 0 && !currentImage.processedUrl && (
              <CanvasFaceMarkersOverlay
                faces={detectedFaces}
                imageWidth={currentImage.imageWidth || 1}
                imageHeight={currentImage.imageHeight || 1}
                selectedTargetPersonId={selectedTargetPersonId}
                onSelectTargetPersonId={setSelectedTargetPersonId}
                personMappings={personMappings}
                sourcePeople={sourcePeople}
                sourceGallery={sourceGallery}
                distinctGlobalPersons={distinctGlobalPersons}
                onAssignSourcePerson={handleAssignSourcePerson}
                onReassignFaceGlobalId={handleReassignFaceGlobalId}
              />
            )}

            {/* Render both comparison images in the same box. A nested clipped wrapper plus
                object-contain can be rasterized on a separate fractional GPU layer, causing
                a visible one-pixel vertical jump even when both files have identical dimensions. */}
            {viewMode === 'SPLIT' && !isHoldingOriginal && (currentImage.refinedUrl || currentImage.processedUrl) && (
              <img
                src={currentImage.refinedUrl || currentImage.processedUrl}
                alt="Processed Result Overlay"
                data-testid="target-comparison-after-layer"
                draggable={false}
                className="pointer-events-none absolute inset-0 block h-full w-full select-none object-fill object-left-top"
                style={{ clipPath: `inset(0 ${100 - splitPosition}% 0 0)` }}
              />
            )}

            {/* Split Comparison Divider Line */}
            {viewMode === 'SPLIT' && !isHoldingOriginal && (currentImage.refinedUrl || currentImage.processedUrl) && (
              <div
                data-testid="target-comparison-control"
                role="slider"
                tabIndex={0}
                aria-label="原图与换脸后图片对比分隔位置"
                aria-valuemin={5}
                aria-valuemax={95}
                aria-valuenow={Math.round(splitPosition)}
                aria-valuetext={`左侧显示换脸后 ${Math.round(splitPosition)}%，右侧显示原图 ${Math.round(100 - splitPosition)}%`}
                className="absolute inset-0 z-20 cursor-ew-resize touch-none outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-400"
                onKeyDown={(event) => {
                  let next: number | null = null;
                  if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') next = splitPosition - 1;
                  if (event.key === 'ArrowRight' || event.key === 'ArrowUp') next = splitPosition + 1;
                  if (event.key === 'Home') next = 5;
                  if (event.key === 'End') next = 95;
                  if (next === null) return;
                  event.preventDefault();
                  setSplitPosition(Math.max(5, Math.min(95, next)));
                }}
                onPointerDown={(event) => {
                  if (event.pointerType === 'mouse' && event.button !== 0) return;
                  event.currentTarget.setPointerCapture(event.pointerId);
                  updateSplitFromPointer(event.currentTarget, event.clientX);
                }}
                onPointerMove={(event) => {
                  if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
                  updateSplitFromPointer(event.currentTarget, event.clientX);
                }}
                onPointerUp={(event) => {
                  if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                    event.currentTarget.releasePointerCapture(event.pointerId);
                  }
                }}
                onPointerCancel={(event) => {
                  if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                    event.currentTarget.releasePointerCapture(event.pointerId);
                  }
                }}
              >
                <div className="pointer-events-none absolute left-3 top-3 rounded-md border border-indigo-400/40 bg-indigo-600/90 px-2.5 py-1 font-mono text-xs font-medium text-white shadow-lg backdrop-blur-md">
                  换脸后
                </div>
                <div
                  className="pointer-events-none absolute top-0 bottom-0 w-0.5 -translate-x-1/2 bg-indigo-500 shadow-lg shadow-indigo-500/80"
                  style={{ left: `${splitPosition}%` }}
                >
                  <div
                    aria-hidden="true"
                    className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-11 h-11 rounded-full bg-indigo-600 border-2 border-white flex items-center justify-center text-white shadow-xl pointer-events-none"
                  >
                    <Columns className="w-4 h-4" />
                  </div>
                </div>

                <div className="absolute top-3 right-3 px-2.5 py-1 rounded-md bg-black/80 backdrop-blur-md text-xs font-mono text-zinc-300 font-medium border border-white/10 shadow-lg pointer-events-none">
                  原图
                </div>
              </div>
            )}

            {/* Manual Inpainting Protection Brush Overlay.
                Stays mounted across view-mode switches (only visually hidden) so its painted
                bitmap survives leaving and returning to BRUSH mode; conditionally rendering it
                would remount a blank canvas each time and silently drop prior strokes. */}
            <canvas
              ref={paintCanvasRef}
              tabIndex={viewMode === 'BRUSH' ? 0 : -1}
              role="img"
              aria-label="手动保护画笔画布。此画布需要指针绘制；键盘用户可使用上方工具栏调整画笔、撤销、重做或清空遮罩。"
              aria-describedby="brush-canvas-help"
              onPointerDown={viewMode === 'BRUSH' ? handleBrushPointerDown : undefined}
              onPointerMove={viewMode === 'BRUSH' ? handleBrushPointerMove : undefined}
              onPointerUp={viewMode === 'BRUSH' ? handleBrushMouseUp : undefined}
              onPointerCancel={viewMode === 'BRUSH' ? handleBrushMouseUp : undefined}
              onPointerLeave={viewMode === 'BRUSH' ? handleBrushMouseUp : undefined}
              className={`absolute inset-0 z-30 touch-none cursor-crosshair focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 ${viewMode === 'BRUSH' ? '' : 'hidden'}`}
            />

            {/* Magnifier View */}
            {viewMode === 'MAGNIFIER' && magnifierData && currentImage && (
              <div
                className="absolute w-48 h-48 rounded-full border-2 border-indigo-400 overflow-hidden shadow-2xl pointer-events-none z-30 ring-4 ring-indigo-500/30 bg-zinc-950"
                style={{
                  left: magnifierData.x - 96,
                  top: magnifierData.y - 96,
                }}
              >
                <img
                  src={
                    !isHoldingOriginal && (currentImage.refinedUrl || currentImage.processedUrl)
                      ? currentImage.refinedUrl || currentImage.processedUrl
                      : currentImage.base64Data || currentImage.url
                  }
                  className="absolute max-w-none pointer-events-none block select-none"
                  style={{
                    width: `${magnifierData.imgWidth * 3}px`,
                    height: `${magnifierData.imgHeight * 3}px`,
                    left: `${-(magnifierData.x * 3 - 96)}px`,
                    top: `${-(magnifierData.y * 3 - 96)}px`,
                  }}
                />
                {/* Magnifier Reticle */}
                <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                  <div className="w-5 h-5 border border-cyan-400/50 rounded-full flex items-center justify-center">
                    <div className="w-1.5 h-1.5 bg-cyan-400 rounded-full shadow-[0_0_8px_rgba(6,182,212,0.8)]" />
                  </div>
                </div>
                {/* Magnifier info badge */}
                <div className="absolute bottom-2 left-1/2 -translate-x-1/2 px-2 py-0.5 rounded-full bg-black/85 backdrop-blur-md text-xs font-mono text-cyan-300 border border-cyan-400/30 shadow-md whitespace-nowrap">
                  {isHoldingOriginal
                    ? '300% 原图细节对比'
                    : currentImage.refinedUrl
                      ? '300% 精修后质感细节'
                      : currentImage.processedUrl
                        ? '300% 换脸后质感细节'
                        : '300% 原图细节'}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
      {currentImage?.status === 'SUCCESS' && (currentImage.refinedUrl || currentImage.processedUrl) && (
        <CanvasResultActionBar
          currentImage={currentImage}
          onOpenRefinement={openRefinement}
          onDownload={handleDownloadCurrentResult}
        />
      )}

      {/* Bottom Batch Filmstrip Thumbnails Carousel */}
      <CanvasBatchFilmstrip
        targetImages={targetImages}
        activeTargetIndex={activeTargetIndex}
        onSelectTargetIndex={setActiveTargetIndex}
        onRemoveTargetImage={removeTargetImage}
        onUploadClick={() => fileInputRef.current?.click()}
        isProcessing={isProcessing}
      />
    </div>
  );
};
