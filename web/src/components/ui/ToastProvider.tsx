import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { AlertCircle, CheckCircle2, Info, TriangleAlert, X } from 'lucide-react';

type ToastKind = 'info' | 'success' | 'warning' | 'error';
interface ToastInput {
  title: string;
  description?: string;
  kind?: ToastKind;
}
interface ToastItem extends ToastInput {
  id: number;
}

const ToastContext = createContext<((toast: ToastInput) => void) | null>(null);

export const ToastProvider: React.FC<React.PropsWithChildren> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const showToast = useCallback((toast: ToastInput) => {
    const id = Date.now() + Math.random();
    setToasts((items) => [...items, { ...toast, id }].slice(-4));
    window.setTimeout(() => setToasts((items) => items.filter((item) => item.id !== id)), 5000);
  }, []);
  const value = useMemo(() => showToast, [showToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="fixed right-4 top-14 z-[100] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
        aria-live="polite"
        aria-label="通知"
      >
        {toasts.map((toast) => {
          const Icon =
            toast.kind === 'error'
              ? AlertCircle
              : toast.kind === 'warning'
                ? TriangleAlert
                : toast.kind === 'success'
                  ? CheckCircle2
                  : Info;
          return (
            <div
              key={toast.id}
              role={toast.kind === 'error' || toast.kind === 'warning' ? 'alert' : 'status'}
              className="glass rounded-lg border border-border p-3 shadow-xl"
            >
              <div className="flex items-start gap-2">
                <Icon
                  className={`mt-0.5 h-4 w-4 shrink-0 ${toast.kind === 'error' ? 'text-err' : toast.kind === 'warning' ? 'text-warn' : toast.kind === 'success' ? 'text-ok' : 'text-primary'}`}
                />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-zinc-100">{toast.title}</p>
                  {toast.description && <p className="mt-1 text-xs text-zinc-400">{toast.description}</p>}
                </div>
                <button
                  className="btn btn-ghost btn-sm"
                  aria-label="关闭通知"
                  onClick={() => setToasts((items) => items.filter((item) => item.id !== toast.id))}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
};

export const useToast = () => {
  const context = useContext(ToastContext);
  if (!context) throw new Error('useToast must be used within ToastProvider');
  return context;
};
