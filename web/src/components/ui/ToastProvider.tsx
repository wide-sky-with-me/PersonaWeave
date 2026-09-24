import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { AlertCircle, CheckCircle2, Info, TriangleAlert, X } from 'lucide-react';
import * as Dialog from '@radix-ui/react-dialog';
import { motion } from 'framer-motion';

type ToastKind = 'info' | 'success' | 'warning' | 'error';
interface ToastInput {
  title: string;
  description?: string;
  kind?: ToastKind;
  modal?: boolean;
}
interface ToastItem extends ToastInput {
  id: number;
}

const ToastContext = createContext<((toast: ToastInput) => void) | null>(null);

export const ToastProvider: React.FC<React.PropsWithChildren> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [dialogs, setDialogs] = useState<ToastItem[]>([]);
  const showToast = useCallback((toast: ToastInput) => {
    const id = Date.now() + Math.random();
    // Failures require acknowledgement. Queue distinct failures rather than losing them to a timer.
    if (toast.kind === 'error' || toast.modal) {
      setDialogs((items) =>
        items.some((item) => item.title === toast.title && item.description === toast.description)
          ? items
          : [...items, { ...toast, id }],
      );
      return;
    }
    setToasts((items) => [...items, { ...toast, id }].slice(-4));
    window.setTimeout(() => setToasts((items) => items.filter((item) => item.id !== id)), 5000);
  }, []);
  const value = useMemo(() => showToast, [showToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <Dialog.Root open={dialogs.length > 0} onOpenChange={(open) => !open && setDialogs((items) => items.slice(1))}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[200] bg-black/65 backdrop-blur-sm" />
          <Dialog.Content
            onPointerDownOutside={(event) => event.preventDefault()}
            className="fixed left-1/2 top-1/2 z-[201] w-[min(34rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 outline-none"
          >
            <motion.div
              key={dialogs[0]?.id}
              initial={{ opacity: 0, scale: 0.96, y: 8 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              transition={{ type: 'spring', stiffness: 300, damping: 25 }}
              className="rounded-2xl border border-white/10 bg-zinc-900 p-6 shadow-2xl"
            >
              <Dialog.Title className="flex items-center gap-2 text-base font-semibold text-zinc-100">
                <TriangleAlert className="h-5 w-5 shrink-0 text-amber-400" />
                {dialogs[0]?.title}
              </Dialog.Title>
              <Dialog.Description className="mt-4 max-h-[55vh] overflow-y-auto whitespace-pre-wrap break-words text-sm leading-6 text-zinc-300">
                {dialogs[0]?.description || '操作未完成，请检查输入后重试。'}
              </Dialog.Description>
              <div className="mt-5 flex items-center justify-between gap-3">
                <span className="text-xs text-zinc-500">
                  {dialogs.length > 1 ? `还有 ${dialogs.length - 1} 条提示` : ''}
                </span>
                <Dialog.Close className="btn btn-primary px-5 py-2">知道了</Dialog.Close>
              </div>
            </motion.div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
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
