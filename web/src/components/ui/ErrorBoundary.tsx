import React from 'react';

interface State {
  error: Error | null;
}

export class ErrorBoundary extends React.Component<React.PropsWithChildren, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('Application render failed:', error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="flex min-h-screen items-center justify-center bg-background p-6 text-zinc-200">
        <div role="alert" className="max-w-lg rounded-xl border border-err/40 bg-surface-2 p-6">
          <h1 className="text-lg font-semibold">界面加载失败</h1>
          <p className="mt-2 text-sm text-zinc-400">
            已持久化的任务记录保留在服务端，请重新载入后从历史查看。若问题持续，请查看控制台日志。
          </p>
          <button className="btn btn-primary btn-md mt-4" onClick={() => window.location.reload()}>
            重新载入
          </button>
        </div>
      </main>
    );
  }
}

export const LazyModalFallback: React.FC<{ label: string }> = ({ label }) => (
  <div
    role="status"
    aria-live="polite"
    className="fixed inset-x-0 bottom-16 z-50 mx-auto w-fit rounded-lg border border-border bg-surface-2 px-3 py-2 text-xs text-zinc-300 shadow-xl"
  >
    正在加载{label}…
  </div>
);
