/** Shared cache identity only; importing it must not load workbench behavior or stores. */
export const sessionKeys = ['refinement-session'] as const;
export const sessionKey = (id: string | null) => [...sessionKeys, id] as const;
export const sessionsKey = ['refinement-sessions'] as const;
