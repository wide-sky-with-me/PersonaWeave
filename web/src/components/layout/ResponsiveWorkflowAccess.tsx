import * as Dialog from '@radix-ui/react-dialog';
import { Check, Circle, SlidersHorizontal, Users, X } from 'lucide-react';
import type { ReactNode } from 'react';

export type WorkflowPanel = 'sources' | 'parameters';

interface ReadinessItem {
  label: string;
  complete: boolean;
}
interface ResponsiveWorkflowAccessProps {
  sourcePanel: ReactNode;
  parameterPanel: ReactNode;
  openPanel: WorkflowPanel | null;
  onOpenPanel: (panel: WorkflowPanel | null) => void;
  readiness: ReadinessItem[];
}

const panelMeta = {
  sources: { label: '源身份', side: 'left', icon: Users },
  parameters: { label: '换脸控制', side: 'right', icon: SlidersHorizontal },
} as const;

export function ResponsiveWorkflowAccess({
  sourcePanel,
  parameterPanel,
  openPanel,
  onOpenPanel,
  readiness,
}: ResponsiveWorkflowAccessProps) {
  const ready = readiness.every((item) => item.complete);
  const activeMeta = openPanel ? panelMeta[openPanel] : null;

  return (
    <>
      <div className="border-b border-border bg-surface-1/90 px-3 py-1.5 sm:px-4">
        <div className="flex min-w-0 items-center justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <button
              className="btn btn-sm btn-outline min-h-9 shrink-0 gap-1.5 text-xs text-zinc-200 lg:hidden"
              onClick={() => onOpenPanel('sources')}
              aria-label="打开源身份面板"
            >
              <Users className="h-4 w-4 text-primary-soft" /> 源身份
            </button>
            <button
              className="btn btn-sm btn-outline min-h-9 shrink-0 gap-1.5 text-xs text-zinc-200 xl:hidden"
              onClick={() => onOpenPanel('parameters')}
              aria-label="打开换脸控制面板"
            >
              <SlidersHorizontal className="h-4 w-4 text-accent" /> 换脸控制
            </button>
            <div className="flex min-w-0 max-w-full items-center gap-1.5 overflow-x-auto" aria-label="Job readiness">
              <span className="text-xs font-medium text-zinc-400 mr-1 hidden sm:inline-flex items-center gap-1">
                流程状态:
              </span>
              {readiness.map((item) => (
                <span
                  key={item.label}
                  className={`inline-flex shrink-0 items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-medium border transition-colors ${
                    item.complete
                      ? 'bg-white/[0.02] text-zinc-300 border-white/10'
                      : 'bg-transparent text-zinc-400 border-white/5'
                  }`}
                >
                  {item.complete ? (
                    <Check className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                  ) : (
                    <Circle className="h-3.5 w-3.5 text-zinc-400 shrink-0" />
                  )}
                  <span>{item.label}</span>
                </span>
              ))}
              <span
                className={`inline-flex shrink-0 items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-semibold border ${
                  ready
                    ? 'bg-ok-muted text-emerald-300 border-ok-border shadow-[0_0_12px_rgba(34,197,94,0.15)]'
                    : 'bg-warn-muted text-amber-300 border-warn-border'
                }`}
                aria-label={ready ? '已准备运行' : '尚未准备运行'}
              >
                {ready ? (
                  <Check className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                ) : (
                  <Circle className="h-3.5 w-3.5 text-amber-400 shrink-0" />
                )}
                <span>{ready ? '可运行' : '待准备'}</span>
              </span>
            </div>
          </div>
        </div>
      </div>
      <Dialog.Root open={openPanel !== null} onOpenChange={(open) => !open && onOpenPanel(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[70] bg-black/65 backdrop-blur-sm" />
          {openPanel && activeMeta && (
            <Dialog.Content
              aria-describedby={undefined}
              className={`fixed inset-y-0 z-[80] flex w-[min(92vw,360px)] flex-col border-border bg-background shadow-2xl ${activeMeta.side === 'left' ? 'left-0 border-r' : 'right-0 border-l'}`}
            >
              <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
                <Dialog.Title className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
                  <activeMeta.icon className="h-4 w-4 text-primary-soft" />
                  {activeMeta.label}
                </Dialog.Title>
                <Dialog.Close className="btn btn-ghost btn-sm" aria-label={`关闭${activeMeta.label}面板`}>
                  <X className="h-4 w-4" />
                </Dialog.Close>
              </div>
              <div className="min-h-0 flex-1 overflow-hidden">
                {openPanel === 'sources' ? sourcePanel : parameterPanel}
              </div>
            </Dialog.Content>
          )}
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
