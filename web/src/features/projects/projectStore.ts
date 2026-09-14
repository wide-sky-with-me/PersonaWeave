import { create } from 'zustand';

export const useProjectStore = create<{
  id: string | null;
  revision: number;
  title: string;
  status: 'loading' | 'idle' | 'dirty' | 'saving' | 'error';
  error: string | null;
  warnings: string[];
}>(() => ({ id: null, revision: 0, title: '', status: 'loading', error: null, warnings: [] }));
