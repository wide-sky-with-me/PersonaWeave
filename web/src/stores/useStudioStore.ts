import { create } from 'zustand';
import type {
  PersonMappingItem,
  SourcePerson,
  ServerHealth,
  SourceReference,
  SwapParameters,
  TargetImageItem,
  ViewMode,
} from '../types';

interface StudioState {
  // Source Gallery State
  sourcePeople: SourcePerson[];
  activeSourcePersonId: number | null;
  addSourcePerson: (name: string, libraryIdentityId?: string) => number;
  renameSourcePerson: (id: number, name: string) => void;
  removeSourcePerson: (id: number) => void;
  setActiveSourcePersonId: (id: number | null) => void;
  sourceGallery: SourceReference[];
  addSourceReference: (ref: SourceReference) => void;
  updateSourceReference: (id: string, patch: Partial<Omit<SourceReference, 'kind' | 'assetId' | 'imageData'>>) => void;
  removeSourceReference: (id: string) => void;
  clearSourceGallery: () => void;

  // Target Batch Images State
  targetImages: TargetImageItem[];
  activeTargetIndex: number;
  addTargetImages: (items: TargetImageItem[]) => void;
  setActiveTargetIndex: (index: number) => void;
  updateTargetImage: (index: number, patch: Partial<TargetImageItem>) => void;
  updateTargetImageById: (id: string, patch: Partial<TargetImageItem>) => void;
  batchUpdateTargetImages: (updates: { id: string; patch: Partial<TargetImageItem> }[]) => void;
  removeTargetImage: (index: number) => void;
  clearTargetImages: () => void;

  // Selected target identity discovered by clustering
  selectedTargetPersonId: number | null;
  setSelectedTargetPersonId: (index: number | null) => void;

  // Hyperparameters
  parameters: SwapParameters;
  updateParameters: (patch: Partial<SwapParameters>) => void;

  // Viewport Settings
  viewMode: ViewMode;
  setViewMode: (mode: ViewMode) => void;
  splitPosition: number; // 0 to 100 percentage
  setSplitPosition: (pos: number) => void;
  showFaceMarkers: boolean;
  setShowFaceMarkers: (show: boolean) => void;

  // Brush settings are global; mask content and history belong to each target.
  setManualMaskDataUrl: (data: string | null) => void;
  undoManualMask: () => void;
  redoManualMask: () => void;
  brushSize: number;
  setBrushSize: (size: number) => void;
  isEraser: boolean;
  setIsEraser: (eraser: boolean) => void;
  // Multi-Person Mapping Matrix State
  personMappings: PersonMappingItem[];
  setPersonMappings: (mappings: PersonMappingItem[]) => void;
  isMatrixOpen: boolean;
  setIsMatrixOpen: (open: boolean) => void;

  // Task & Processing State
  isProcessing: boolean;
  setIsProcessing: (isProc: boolean) => void;
  activeTaskId: string | null;
  setActiveTaskId: (taskId: string | null) => void;
  batchProgress: number; // 0 to 100
  setBatchProgress: (prog: number) => void;

  // System Health
  serverHealth: ServerHealth | null;
  setServerHealth: (health: ServerHealth | null) => void;
}

export const useStudioStore = create<StudioState>((set) => ({
  // Default Source Gallery
  sourcePeople: [],
  activeSourcePersonId: null,
  addSourcePerson: (name, libraryIdentityId) => {
    let id = 1;
    set((state) => {
      id = Math.max(0, ...state.sourcePeople.map((person) => person.id)) + 1;
      return {
        sourcePeople: [...state.sourcePeople, { id, name, libraryIdentityId }],
        activeSourcePersonId: id,
        personMappings: [],
      };
    });
    return id;
  },
  renameSourcePerson: (id, name) =>
    set((state) => ({
      sourcePeople: state.sourcePeople.map((person) => (person.id === id ? { ...person, name } : person)),
      personMappings: [],
    })),
  removeSourcePerson: (id) =>
    set((state) => {
      const sourcePeople = state.sourcePeople.filter((person) => person.id !== id);
      return {
        sourcePeople,
        sourceGallery: state.sourceGallery.filter((reference) => reference.personId !== id),
        activeSourcePersonId:
          state.activeSourcePersonId === id ? (sourcePeople[0]?.id ?? null) : state.activeSourcePersonId,
        personMappings: [],
      };
    }),
  setActiveSourcePersonId: (id) => set({ activeSourcePersonId: id }),
  sourceGallery: [],
  addSourceReference: (ref) =>
    set((state) => ({ sourceGallery: [...state.sourceGallery, ref], personMappings: [] })),
  updateSourceReference: (id, patch) =>
    set((state) => ({
      sourceGallery: state.sourceGallery.map((ref) => (ref.id === id ? { ...ref, ...patch } : ref)),
      personMappings: [],
    })),
  removeSourceReference: (id) =>
    set((state) => ({
      sourceGallery: state.sourceGallery.filter((r) => r.id !== id),
      personMappings: [],
    })),
  clearSourceGallery: () => set({ sourceGallery: [], sourcePeople: [], activeSourcePersonId: null, personMappings: [] }),

  // Target Batch
  targetImages: [],
  activeTargetIndex: 0,
  addTargetImages: (items) =>
    set((state) => ({
      targetImages: [...state.targetImages, ...items],
      personMappings: [],
    })),
  setActiveTargetIndex: (index) => set({ activeTargetIndex: index }),
  updateTargetImage: (index, patch) =>
    set((state) => {
      const updated = [...state.targetImages];
      if (updated[index]) {
        updated[index] = { ...updated[index], ...patch };
      }
      return { targetImages: updated, personMappings: [] };
    }),
  updateTargetImageById: (id, patch) =>
    set((state) => {
      const topologyChanged = Object.prototype.hasOwnProperty.call(patch, 'faces');
      return {
        targetImages: state.targetImages.map((target) =>
          target.id === id ? { ...target, ...patch } : target,
        ),
        ...(topologyChanged ? { personMappings: [] } : {}),
      };
    }),
  batchUpdateTargetImages: (updates) =>
    set((state) => {
      const patchesById = new Map(updates.map(({ id, patch }) => [id, patch]));
      const topologyChanged = updates.some(({ patch }) => Object.prototype.hasOwnProperty.call(patch, 'faces'));
      return {
        targetImages: state.targetImages.map((target) => {
          const patch = patchesById.get(target.id);
          return patch ? { ...target, ...patch } : target;
        }),
        ...(topologyChanged ? { personMappings: [] } : {}),
      };
    }),
  removeTargetImage: (index) =>
    set((state) => {
      const updated = state.targetImages.filter((_, i) => i !== index);
      const newActive = Math.min(state.activeTargetIndex, Math.max(0, updated.length - 1));
      return { targetImages: updated, activeTargetIndex: newActive, selectedTargetPersonId: null, personMappings: [] };
    }),
  clearTargetImages: () => set({ targetImages: [], activeTargetIndex: 0, selectedTargetPersonId: null, personMappings: [] }),

  // Target Person Selection (left to right 1-based)
  selectedTargetPersonId: null,
  setSelectedTargetPersonId: (index) => set({ selectedTargetPersonId: index }),

  // Algorithm Hyperparameters
  parameters: {
    similarity_threshold: 0.35,
    identity_blend_strength: 0.72,
    enable_color_transfer: true,
    color_transfer_mode: 'boundary',
    enable_dense_mask: true,
    source_mode: 'best-match',
    swapper_model: 'auto',
    allow_research_models: false,
  },
  updateParameters: (patch) =>
    set((state) => ({ parameters: { ...state.parameters, ...patch } })),

  // Viewport Settings
  viewMode: 'SPLIT',
  setViewMode: (mode) => set({ viewMode: mode }),
  splitPosition: 50,
  setSplitPosition: (pos) => set({ splitPosition: pos }),
  showFaceMarkers: true,
  setShowFaceMarkers: (show) => set({ showFaceMarkers: show }),

  // Mask edits are kept on the active target so switching cannot leak protection data.
  setManualMaskDataUrl: (data) =>
    set((state) => {
      const target = state.targetImages[state.activeTargetIndex];
      if (!target) return state;
      const mask = target.manualMask ?? { dataUrl: null, undoStack: [], redoStack: [] };
      if (mask.dataUrl === data) return state;
      const updated = [...state.targetImages];
      updated[state.activeTargetIndex] = {
        ...target,
        manualMask: { dataUrl: data, undoStack: [...mask.undoStack, mask.dataUrl].slice(-20), redoStack: [] },
      };
      return { targetImages: updated, personMappings: [] };
    }),
  undoManualMask: () =>
    set((state) => {
      const target = state.targetImages[state.activeTargetIndex];
      const mask = target?.manualMask;
      if (!target || !mask?.undoStack.length) return state;
      const updated = [...state.targetImages];
      updated[state.activeTargetIndex] = {
        ...target,
        manualMask: {
          dataUrl: mask.undoStack.at(-1) ?? null,
          undoStack: mask.undoStack.slice(0, -1),
          redoStack: [mask.dataUrl, ...mask.redoStack].slice(0, 20),
        },
      };
      return { targetImages: updated, personMappings: [] };
    }),
  redoManualMask: () =>
    set((state) => {
      const target = state.targetImages[state.activeTargetIndex];
      const mask = target?.manualMask;
      if (!target || !mask?.redoStack.length) return state;
      const [next, ...redoStack] = mask.redoStack;
      const updated = [...state.targetImages];
      updated[state.activeTargetIndex] = {
        ...target,
        manualMask: {
          dataUrl: next,
          undoStack: [...mask.undoStack, mask.dataUrl].slice(-20),
          redoStack,
        },
      };
      return { targetImages: updated, personMappings: [] };
    }),
  brushSize: 28,
  setBrushSize: (size) => set({ brushSize: size }),
  isEraser: false,
  setIsEraser: (eraser) => set({ isEraser: eraser }),

  // Multi-person mapping
  personMappings: [],
  setPersonMappings: (mappings) => set({ personMappings: mappings }),
  isMatrixOpen: false,
  setIsMatrixOpen: (open) => set({ isMatrixOpen: open }),

  // Processing state
  isProcessing: false,
  setIsProcessing: (isProc) => set({ isProcessing: isProc }),
  activeTaskId: null,
  setActiveTaskId: (taskId) => set({ activeTaskId: taskId }),
  batchProgress: 0,
  setBatchProgress: (prog) => set({ batchProgress: prog }),

  // Server Health
  serverHealth: null,
  setServerHealth: (health) => set({ serverHealth: health }),
}));
