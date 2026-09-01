import React, { lazy, Suspense, useState, useRef, useEffect, useMemo, useCallback } from 'react';
import {
  Columns,
  Maximize2,
  ZoomIn,
  Upload,
  UserCheck,
  Sparkles,
  CheckCircle,
  Clock,
  AlertCircle,
  Eye,
  EyeOff,
  Crosshair,
  Zap,
  Paintbrush,
  Eraser,
  Trash2,
  Undo2,
  Redo2,
  X,
  BrainCircuit,
} from 'lucide-react';
import { motion } from 'framer-motion';
import { useStudioStore } from '../../stores/useStudioStore';
import { api } from '../../services/api';
import { LazyModalFallback } from '../ui/ErrorBoundary';
const RefinementModal = lazy(() => import('./RefinementModal').then((module) => ({ default: module.RefinementModal })));
import { relabelFaceSafely } from '../../utils/faceTopology';
import type { RefinementWorkspaceState, TargetImageItem } from '../../types';
import { useIdentityClustering } from '../../hooks/useIdentityClustering';
import { validateImageFiles } from '../../utils/imageFiles';
import {
  comparisonPositionFromPointer,
  FACE_MARKER_PADDING,
  faceBoxToPercentage,
  fitImageWithinViewport,
  type ViewportSize,
} from '../../utils/imageViewport';
import { useToast } from '../ui/ToastProvider';
import { useManualProtectionBrush } from '../../hooks/useManualProtectionBrush';
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
  const [isRefinementOpen, setIsRefinementOpen] = useState(false);
  const toast = useToast();
  const {
    sourceGallery,
    personMappings,
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
  const currentTargetId = currentImage?.id ?? null;
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
  const persistRefinementWorkspace = useCallback((workspace: RefinementWorkspaceState) => {
    if (currentTargetId) updateTargetImageById(currentTargetId, { refinementWorkspace: workspace });
  }, [currentTargetId, updateTargetImageById]);
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
        current && next &&
        Math.abs(current.width - next.width) < 0.5 &&
        Math.abs(current.height - next.height) < 0.5
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

  // Handle batch images upload
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files?.length) return;
    const { accepted, errors } = await validateImageFiles(files);
    const reads = accepted.map(({ file, width, height }, idx) => new Promise<TargetImageItem>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const base64Str = reader.result as string;
        resolve({ id: `tgt_${Date.now()}_${idx}_${Math.random().toString(36).slice(2, 7)}`, name: file.name, url: base64Str, base64Data: base64Str, imageWidth: width, imageHeight: height, status: 'IDLE' });
      };
      reader.onerror = () => reject(new Error(`无法读取 ${file.name}`));
      reader.readAsDataURL(file);
    }));
    const settled = await Promise.allSettled(reads);
    const newItems = settled.flatMap((result) => result.status === 'fulfilled' ? [result.value] : []);
    const readErrors = settled.flatMap((result) => result.status === 'rejected' ? [String(result.reason?.message ?? result.reason)] : []);
    if (newItems.length) addTargetImages(newItems);
    if (errors.length || readErrors.length) toast({ title: '部分图片无法使用', description: [...errors, ...readErrors].join('；'), kind: 'error' });
    if (fileInputRef.current) fileInputRef.current.value = '';
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

  const [editingFaceIndex, setEditingFaceIndex] = useState<number | null>(null);

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
    setEditingFaceIndex(null);
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
          idx !== activeTargetIndex &&
          t.faces?.some((f) => (f.global_person_id ?? f.index) === selectedTargetPersonId)
      );

      if (otherImgWithPerson && otherImgWithPerson.faces) {
        const refFace = otherImgWithPerson.faces.find(
          (f) => (f.global_person_id ?? f.index) === selectedTargetPersonId
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
          (t, idx) =>
            idx !== activeTargetIndex &&
            t.faces?.some((f) => (f.global_person_id ?? f.index) === missingGid)
        );
        if (refImg && refImg.faces) {
          const refFace = refImg.faces.find(
            (f) => (f.global_person_id ?? f.index) === missingGid
          );
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
      setVlmReasoning('⚠️ 当前目标批次中没有可确认的同人物锚点。请重新运行身份聚类，或手动解决人物归属；不会使用源素材作为目标身份参考。');
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
              res.confidence * 100
            )}% 置信度) — ${res.reasoning}`
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

  const detectedFaces = currentImage?.faces || [];

  return (
    <div className="relative flex-1 flex flex-col h-full bg-[#0a0a0d] overflow-hidden select-none">
      {currentImage?.processedUrl && isRefinementOpen && (
        <Suspense fallback={<LazyModalFallback label="AI 精修" />}>
          <RefinementModal
            key={currentImage.id}
            target={currentImage}
            selectedPersonId={selectedTargetPersonId}
            fallbackPersonIds={personMappings.map((mapping) => mapping.person_index)}
            workspace={currentImage.refinementWorkspace}
            onWorkspaceChange={persistRefinementWorkspace}
            onClose={() => setIsRefinementOpen(false)}
            onAccepted={(url) => updateTargetImageById(currentImage.id, { refinedUrl: url })}
          />
        </Suspense>
      )}

      {/* Hidden File Input for Batch / Single Target Uploads */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/*"
        onChange={handleFileUpload}
        className="hidden"
      />

      {/* Top Toolbar */}
      <div className="min-h-14 px-2 py-2 sm:px-5 border-b border-white/10 flex flex-wrap items-center justify-between gap-2 bg-[#121215]/80 backdrop-blur-md shrink-0">
        {/* Left: Active Image Info & Global Person Selector */}
        <div className="flex min-w-0 flex-wrap items-center gap-2 sm:gap-4">
          <span className="text-sm font-semibold text-zinc-200 truncate max-w-[180px]">
            {currentImage ? currentImage.name : '未选择图片'}
          </span>

          {currentImage && (
            <label className="flex items-center gap-1.5 rounded-lg border border-white/5 bg-zinc-900 px-2.5 py-1 text-xs">
              <span className="flex items-center gap-1 font-medium text-zinc-400">
                <Crosshair className="h-3.5 w-3.5 text-indigo-400" />
                替换目标
              </span>
              <select
                value={selectedTargetPersonId ?? 'all'}
                onChange={(event) => setSelectedTargetPersonId(event.target.value === 'all' ? null : Number(event.target.value))}
                className="h-7 min-w-[108px] rounded-md border border-border bg-surface-3 px-2 font-mono text-xs font-semibold text-zinc-200"
                aria-label="选择要替换的目标人物"
              >
                <option value="all">全部人物</option>
                {distinctGlobalPersons.map((gid) => (
                  <option key={gid} value={gid}>人物 #{gid}</option>
                ))}
              </select>
            </label>
          )}
        </div>

        {/* Right: Face Markers Toggle, Multi-Person Matrix, VLM Arbiter & View Mode Selectors */}
        <div className="flex max-w-full items-center gap-2 overflow-x-auto pb-1">
          {/* Multi-Person Matrix Mapping Button */}

          {/* AI VLM Arbitrate Button */}
          {currentImage && (targetImages.length > 1 || sourceGallery.length > 0) && (
            <button
              onClick={handleVlmArbitrate}
              disabled={isArbitrating}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-gradient-to-r from-purple-600/20 to-indigo-600/20 border border-purple-500/40 text-purple-300 hover:from-purple-600/30 hover:to-indigo-600/30 transition-all shadow-sm active:scale-95 disabled:opacity-50"
              title="调用多模态视觉大模型综合五官、发型、衣服款式进行智能纠错"
            >
              <Sparkles className={`w-3.5 h-3.5 text-purple-400 ${isArbitrating ? 'animate-spin' : ''}`} />
              <span>{isArbitrating ? 'AI 视觉纠错中...' : 'AI 大模型纠错'}</span>
            </button>
          )}

          {currentImage?.processedUrl && (
            <button
              onClick={() => setIsRefinementOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-gradient-to-r from-cyan-600/20 to-purple-600/20 border border-cyan-500/40 text-cyan-200 hover:from-cyan-600/30 hover:to-purple-600/30 transition-all"
              title="多模态评审换脸问题，并按人工需求调用生图模型精修"
            >
              <BrainCircuit className="w-3.5 h-3.5" />
              AI 评审精修
            </button>
          )}
          {/* Face Markers Toggle */}
          {currentImage && (
            <button
              onClick={() => setShowFaceMarkers(!showFaceMarkers)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                showFaceMarkers
                  ? 'bg-cyan-500/15 text-cyan-400 border-cyan-500/40 shadow-sm'
                  : 'bg-zinc-900 text-zinc-400 border-white/10 hover:text-zinc-200'
              }`}
              title="在画面上显示/隐藏所有人脸检测定位框与编号"
            >
              {showFaceMarkers ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
              <span>人脸定位框</span>
            </button>
          )}

          {/* View Modes */}
          <div className="flex items-center gap-1 bg-zinc-900 p-1 rounded-lg border border-white/5">
            <button
              onClick={() => setViewMode('SINGLE')}
              className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium transition-all ${
                viewMode === 'SINGLE'
                  ? 'bg-indigo-600/30 text-indigo-300 border border-indigo-500/40'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <Maximize2 className="w-3.5 h-3.5" />
              <span>单图</span>
            </button>
            <button
              onClick={() => setViewMode('SPLIT')}
              className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium transition-all ${
                viewMode === 'SPLIT'
                  ? 'bg-indigo-600/30 text-indigo-300 border border-indigo-500/40'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <Columns className="w-3.5 h-3.5" />
              <span>前后对比</span>
            </button>
            <button
              onClick={() => setViewMode('MAGNIFIER')}
              className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium transition-all ${
                viewMode === 'MAGNIFIER'
                  ? 'bg-indigo-600/30 text-indigo-300 border border-indigo-500/40'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <ZoomIn className="w-3.5 h-3.5" />
              <span>放大镜</span>
            </button>
            <button
              onClick={() => setViewMode('BRUSH')}
              className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium transition-all ${
                viewMode === 'BRUSH'
                  ? 'bg-amber-600/30 text-amber-300 border border-amber-500/40'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
              title="手动涂抹保护笔：绘制不希望被换脸替换的区域（发饰、眼镜、胎记等）"
            >
              <Paintbrush className="w-3.5 h-3.5" />
              <span>保护笔</span>
            </button>
          </div>
        </div>
      </div>

      {/* Brush Floating Toolbar */}
      {viewMode === 'BRUSH' && (
        <div className="absolute top-16 left-2 right-2 sm:left-1/2 sm:right-auto sm:-translate-x-1/2 px-3 sm:px-4 py-2 rounded-2xl bg-zinc-900/90 backdrop-blur-xl border border-amber-500/40 shadow-2xl flex items-center gap-3.5 overflow-x-auto z-40 animate-in fade-in slide-in-from-top-2">
          <div id="brush-canvas-help" className="flex items-center gap-1.5 text-xs text-amber-300 font-medium">
            <Paintbrush className="w-4 h-4" />
            <span>保护笔；键盘可操作工具栏，绘制需使用触控笔、触摸或鼠标。</span>
          </div>
          <div className="h-4 w-px bg-white/10" />
          <button
            onClick={() => setIsEraser(false)}
            className={`min-h-11 px-3 py-2 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-all ${
              !isEraser
                ? 'bg-amber-500/20 text-amber-300 border border-amber-400/50 shadow-sm'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            <Paintbrush className="w-3.5 h-3.5" />
            <span>画笔</span>
          </button>
          <button
            onClick={() => setIsEraser(true)}
            className={`min-h-11 px-3 py-2 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-all ${
              isEraser
                ? 'bg-amber-500/20 text-amber-300 border border-amber-400/50 shadow-sm'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            <Eraser className="w-3.5 h-3.5" />
            <span>橡皮</span>
          </button>
          <div className="flex items-center gap-2 text-xs text-zinc-300">
            <span className="text-xs text-zinc-400">粗细:</span>
            <input
              aria-label="保护画笔粗细"
              type="range"
              min="10"
              max="60"
              value={brushSize}
              onChange={(e) => setBrushSize(Number(e.target.value))}
              className="w-20 h-11 accent-amber-500 cursor-pointer"
            />
            <span className="font-mono text-xs text-amber-300">{brushSize}px</span>
          </div>
          <button
            onClick={undoManualMask}
            disabled={!canUndoMask}
            className="min-w-11 min-h-11 p-2 rounded-lg text-zinc-400 hover:text-amber-300 disabled:opacity-30 disabled:pointer-events-none transition-colors"
            title="撤销遮罩编辑"
            aria-label="撤销遮罩编辑"
          >
            <Undo2 className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={redoManualMask}
            disabled={!canRedoMask}
            className="min-w-11 min-h-11 p-2 rounded-lg text-zinc-400 hover:text-amber-300 disabled:opacity-30 disabled:pointer-events-none transition-colors"
            title="重做遮罩编辑"
            aria-label="重做遮罩编辑"
          >
            <Redo2 className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={handleClearMask}
            disabled={!manualMaskDataUrl}
            className="min-h-11 px-3 py-2 rounded-lg text-xs text-zinc-400 hover:text-rose-400 hover:bg-rose-500/10 disabled:opacity-30 disabled:pointer-events-none transition-colors flex items-center gap-1"
            title="清空当前涂抹遮罩"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>清空</span>
          </button>
        </div>
      )}

      {/* Center Main Stage / Canvas */}
      <div
        ref={imageStageRef}
        onPointerMove={handlePointerMove}
        onPointerLeave={() => {
          setMagnifierData(null);
        }}
        className="flex-1 min-h-0 relative flex items-center justify-center p-5 overflow-hidden"
      >
        {!currentImage ? (
          /* Empty State */
          <div className="flex flex-col items-center justify-center text-center p-8 max-w-sm">
            <div className="w-16 h-16 rounded-2xl bg-zinc-900 border border-white/10 flex items-center justify-center text-zinc-400 mb-4 shadow-xl">
              <Upload className="w-8 h-8 text-indigo-400" />
            </div>
            <h3 className="text-sm font-semibold text-zinc-100 mb-4">
              暂无待处理照片
            </h3>
            <button
              onClick={() => fileInputRef.current?.click()}
              className="px-4 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-medium shadow-lg shadow-indigo-600/25 active:scale-95 transition-all flex items-center gap-2 cursor-pointer"
            >
              <Upload className="w-4 h-4" />
              选择待换图片
            </button>
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
                viewMode !== 'SPLIT' && (currentImage.refinedUrl || currentImage.processedUrl)
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
              className={imageViewportSize
                ? 'block h-full w-full object-fill object-left-top pointer-events-none select-none'
                : 'block h-auto w-auto max-h-[calc(100vh-270px)] max-w-full object-fill object-left-top pointer-events-none select-none'}
            />

            {/* Clustering indicator on top left of image */}
            {(isClustering || clusteringError) && (
              <div className={`absolute top-3 left-3 px-3 py-1.5 rounded-lg bg-black/85 backdrop-blur-md border text-xs font-medium flex items-center gap-2 shadow-lg z-30 ${clusteringError ? 'border-red-500/40 text-red-300' : 'border-cyan-500/40 text-cyan-400'}`}>
                <Zap className={`w-3.5 h-3.5 ${isClustering ? 'animate-spin' : ''}`} />
                <span>{clusteringError ? `身份聚类失败：${clusteringError}。修改图片或重新上传可重试。` : '跨图全局人物身份聚类中...'}</span>
              </div>
            )}

            {/* VLM Reasoning Banner */}
            {vlmReasoning && (
              <div className="absolute top-3 left-1/2 -translate-x-1/2 px-4 py-2 rounded-xl bg-purple-950/90 backdrop-blur-xl border border-purple-500/60 text-purple-200 text-xs font-medium shadow-2xl z-40 max-w-lg text-center leading-relaxed animate-in fade-in slide-in-from-top-2">
                {vlmReasoning}
              </div>
            )}

            {/* 🎯 Interactive Face Markers Overlay with Global Consistent IDs */}
            {showFaceMarkers &&
              detectedFaces.length > 0 &&
              !currentImage.processedUrl && (
                <div className="absolute inset-0 pointer-events-none z-20">
                  {detectedFaces.map((face) => {
                    const personId = face.global_person_id ?? face.index;
                    const isSelected = selectedTargetPersonId === personId;
                    const w = currentImage.imageWidth || 1;
                    const h = currentImage.imageHeight || 1;
                    // SCRFD returns a tight recognition ROI. Expand only the visual marker,
                    // especially below the ROI, so the frame reads as a complete face box;
                    // matching and swap coordinates continue using the untouched detector box.
                    const box = faceBoxToPercentage(face.bbox, w, h, FACE_MARKER_PADDING);
                    if (!box) return null;

                    return (
                      <div
                        role="button"
                        tabIndex={0}
                        key={face.index}
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedTargetPersonId(personId);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            setSelectedTargetPersonId(personId);
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
                            : 'border-2 border-dashed border-white/60 bg-black/25 hover:border-indigo-400 hover:bg-indigo-500/20 hover:scale-[1.02] z-10'
                        }`}
                        aria-label={`选择目标人物 ${personId}，位置人脸 ${face.index}`}
                        aria-pressed={isSelected}
                        title={`目标人物 #${personId} (点击选定，点击标签可调整全局编号)`}
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
                                : 'bg-zinc-900/95 text-zinc-100 border border-white/30 hover:border-cyan-400'
                            }`}
                            title="点击手动修改人物编号"
                          >
                            <span>{isSelected ? `🎯 #${personId} 目标` : `👤 #${personId}`}</span>
                          </button>

                          {/* Quick Global ID Reassignment Popover */}
                          {editingFaceIndex === face.index && (
                            <div
                              onClick={(e) => e.stopPropagation()}
                              className="absolute top-full mt-1.5 p-2 rounded-xl bg-zinc-950/95 border border-cyan-500/50 shadow-2xl backdrop-blur-md z-50 flex flex-col gap-1.5 min-w-[140px] animate-in fade-in zoom-in-95"
                            >
                              <div className="text-xs text-zinc-400 font-sans font-medium px-1 flex items-center justify-between">
                                <span>调整归属人物:</span>
                                <button
                                  onClick={() => setEditingFaceIndex(null)}
                                  className="text-zinc-500 hover:text-white"
                                >
                                  ✕
                                </button>
                              </div>
                              <div className="flex items-center gap-1 flex-wrap">
                                {distinctGlobalPersons.map((gid) => (
                                  <button
                                    key={gid}
                                    onClick={() => handleReassignFaceGlobalId(face.index, gid)}
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
                                    handleReassignFaceGlobalId(face.index, maxId + 1);
                                  }}
                                  className="px-2 py-0.5 rounded text-xs font-sans text-indigo-300 bg-indigo-900/40 border border-indigo-500/30 hover:bg-indigo-800/60"
                                  title="分配为全新独立人物"
                                >
                                  +新人物
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

            {/* Render both comparison images in the same box. A nested clipped wrapper plus
                object-contain can be rasterized on a separate fractional GPU layer, causing
                a visible one-pixel vertical jump even when both files have identical dimensions. */}
            {viewMode === 'SPLIT' && (currentImage.refinedUrl || currentImage.processedUrl) && (
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
            {viewMode === 'SPLIT' && (currentImage.refinedUrl || currentImage.processedUrl) && (
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
                  <div aria-hidden="true" className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-11 h-11 rounded-full bg-indigo-600 border-2 border-white flex items-center justify-center text-white shadow-xl pointer-events-none">
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
                    currentImage.refinedUrl ||
                    currentImage.processedUrl ||
                    currentImage.base64Data ||
                    currentImage.url
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
                  {currentImage.processedUrl ? '300% 换脸后质感细节' : '300% 原图细节'}
                </div>
              </div>
            )}

            {/* Bottom floating badge */}
            <div className="absolute bottom-3 left-3 flex items-center gap-2 z-20">
              <div className="px-3 py-1 rounded-lg bg-black/80 backdrop-blur-md border border-white/10 text-xs font-medium flex items-center gap-1.5 text-zinc-300">
                <UserCheck className="w-3.5 h-3.5 text-cyan-400" />
                <span>
                  {selectedTargetPersonId !== null
                    ? `锁定目标人物: #${selectedTargetPersonId} 号`
                    : '替换模式: 全员'}
                </span>
                {detectedFaces.length > 0 && (
                  <span className="text-zinc-400 text-xs">
                    (本图检测到 {detectedFaces.length} 处人脸)
                  </span>
                )}
              </div>
              {currentImage.status === 'SUCCESS' && (
                <div className="px-3 py-1 rounded-lg bg-emerald-500/20 border border-emerald-500/30 text-emerald-300 text-xs font-mono flex items-center gap-1">
                  <Sparkles className="w-3.5 h-3.5" />
                  <span>已完成 ({currentImage.costMs} ms)</span>
                </div>
              )}
              {currentImage.swapWarning && (
                <div className="max-w-md rounded-lg border border-amber-500/30 bg-amber-500/15 px-3 py-1 text-xs text-amber-200 backdrop-blur-md">
                  部分人物未换：{currentImage.swapWarning}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Bottom Batch Filmstrip Thumbnails Carousel */}
      <div className="h-20 border-t border-white/10 bg-[#121215]/90 backdrop-blur-xl px-5 flex items-center gap-3 overflow-x-auto shrink-0">
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={isProcessing}
          className="w-14 h-14 rounded-xl border border-dashed border-white/20 hover:border-indigo-500/60 bg-zinc-900/60 hover:bg-indigo-500/5 flex flex-col items-center justify-center text-zinc-400 hover:text-indigo-400 transition-all shrink-0 active:scale-95 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          title="添加更多待换脸图片"
        >
          <Upload className="w-4 h-4" />
          <span className="text-xs mt-0.5 font-medium">加图</span>
        </button>

        <div className="flex items-center gap-2.5 overflow-x-auto py-1">
          {targetImages.map((item, idx) => {
            const isActive = idx === activeTargetIndex;
            return (
              <motion.div
                key={item.id}
                whileHover={{ scale: 1.05 }}
                className={`group relative w-14 h-14 rounded-xl border-2 transition-all shrink-0 bg-black/50 ${
                  isActive ? 'border-indigo-500 shadow-lg shadow-indigo-500/30' : 'border-white/10 hover:border-white/30 opacity-70 hover:opacity-100'
                }`}
              >
                <button type="button" onClick={() => setActiveTargetIndex(idx)} aria-label={`选择图片 ${idx + 1}：${item.name}`} aria-pressed={isActive} className="absolute inset-0 overflow-hidden rounded-[10px] cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300">
                <img
                  src={item.refinedUrl || item.processedUrl || item.url}
                  alt={item.name}
                  className="w-full h-full object-cover"
                />
                </button>
                <div className="absolute top-0.5 left-0.5 px-1.5 rounded bg-black/70 text-xs font-mono text-zinc-300 font-semibold">
                  #{idx + 1}
                </div>

                {/* Delete / Remove image button on hover */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    removeTargetImage(idx);
                  }}
                  className="absolute -top-2 -right-2 w-11 h-11 rounded-full bg-black/80 hover:bg-red-500 text-zinc-200 hover:text-white flex items-center justify-center opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 transition-all z-10"
                  title="移除此图片"
                  aria-label={`移除图片 ${idx + 1}：${item.name}`}
                >
                  <X className="w-3 h-3" />
                </button>

                <div className="absolute bottom-0.5 right-0.5">
                  {item.status === 'SUCCESS' && (
                    <CheckCircle className="w-3.5 h-3.5 text-emerald-400 drop-shadow" />
                  )}
                  {item.status === 'PROCESSING' && (
                    <Clock className="w-3.5 h-3.5 text-amber-400 animate-spin drop-shadow" />
                  )}
                  {item.status === 'FAILED' && (
                    <AlertCircle className="w-3.5 h-3.5 text-red-400 drop-shadow" />
                  )}
                </div>
              </motion.div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
