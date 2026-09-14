import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import type { TurnRequest } from './sessionTypes';

type View = 'prepare' | 'refinement' | 'history';
interface WorkspaceSelection {
  viewVersionId?: string;
  baseVersionId?: string;
  message?: string;
  confirmedDiagnosisTurnId?: string;
  pane?: 'image' | 'conversation' | 'assets';
  comparison?: 'original' | 'baseline' | 'none';
  zoom?: number;
  model?: string;
}
interface WorkbenchState {
  view: View;
  sessionId: string | null;
  targetId: string | null;
  selections: Record<string, WorkspaceSelection>;
  pendingTurns: Record<string, TurnRequest | undefined>;
  navigate: (view: View) => void;
  openTarget: (targetId: string, sessionId?: string) => void;
  openSession: (sessionId: string) => void;
  select: (sessionId: string, selection: Partial<WorkspaceSelection>) => void;
  pending: (sessionId: string, request?: TurnRequest) => void;
  forgetSession: (id: string) => void;
}
/** Editor choices survive tab refresh; complete linked projects additionally save them on the server. */
export const useWorkbenchStore = create<WorkbenchState>()(
  persist(
    (set) => ({
      view: 'prepare',
      sessionId: null,
      targetId: null,
      selections: {},
      pendingTurns: {},
      navigate: (view) => set({ view }),
      openTarget: (targetId, sessionId) => set({ view: 'refinement', targetId, sessionId: sessionId ?? null }),
      openSession: (sessionId) => set({ view: 'refinement', sessionId, targetId: null }),
      select: (id, selection) =>
        set((state) => ({ selections: { ...state.selections, [id]: { ...state.selections[id], ...selection } } })),
      pending: (id, request) => set((state) => ({ pendingTurns: { ...state.pendingTurns, [id]: request } })),
      forgetSession: (id) =>
        set((state) => {
          const selections = { ...state.selections };
          const pendingTurns = { ...state.pendingTurns };
          delete selections[id];
          delete pendingTurns[id];
          return { selections, pendingTurns, ...(state.sessionId === id ? { sessionId: null, targetId: null } : {}) };
        }),
    }),
    {
      name: 'personaweave-refinement-pending-commands',
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({
        pendingTurns: state.pendingTurns,
        selections: state.selections,
        view: state.view,
        sessionId: state.sessionId,
        targetId: state.targetId,
      }),
    },
  ),
);
