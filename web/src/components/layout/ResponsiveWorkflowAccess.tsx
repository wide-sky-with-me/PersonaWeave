import * as Dialog from '@radix-ui/react-dialog';
import { Check, Circle, SlidersHorizontal, Users, X } from 'lucide-react';
import type { ReactNode } from 'react';

export type WorkflowPanel = 'sources' | 'parameters';

interface ReadinessItem { label: string; complete: boolean; action?: WorkflowPanel | 'mapping' }
interface ResponsiveWorkflowAccessProps {
  sourcePanel: ReactNode; parameterPanel: ReactNode; openPanel: WorkflowPanel | null;
  onOpenPanel: (panel: WorkflowPanel | null) => void; onOpenMapping: () => void; readiness: ReadinessItem[];
}

const panelMeta = {
  sources: { label: 'Sources', side: 'left', icon: Users },
  parameters: { label: 'Parameters', side: 'right', icon: SlidersHorizontal },
} as const;

export function ResponsiveWorkflowAccess({ sourcePanel, parameterPanel, openPanel, onOpenPanel, onOpenMapping, readiness }: ResponsiveWorkflowAccessProps) {
  const ready = readiness.every((item) => item.complete);
  const activeMeta = openPanel ? panelMeta[openPanel] : null;
  const activate = (action?: ReadinessItem['action']) => {
    if (action === 'mapping') onOpenMapping();
    else if (action) onOpenPanel(action);
  };

  return (
    <>
      <div className="border-b border-border bg-surface-1/95 px-2 py-2 sm:px-4">
        <div className="flex min-w-0 items-center gap-2 overflow-x-auto">
          <button className="btn btn-sm btn-outline min-h-11 shrink-0 gap-1.5 lg:hidden" onClick={() => onOpenPanel('sources')} aria-label="打开源身份面板"><Users className="h-4 w-4" /> 源身份</button>
          <button className="btn btn-sm btn-outline min-h-11 shrink-0 gap-1.5 xl:hidden" onClick={() => onOpenPanel('parameters')} aria-label="打开参数面板"><SlidersHorizontal className="h-4 w-4" /> 参数</button>
          <div className="flex min-w-max items-center gap-1" aria-label="Job readiness">
            {readiness.map((item) => (
              <button key={item.label} type="button" onClick={() => activate(item.action)} disabled={!item.action} className={`chip gap-1 ${item.complete ? 'chip-ok' : 'chip-neutral'} disabled:cursor-default`}>
                {item.complete ? <Check className="h-3 w-3" /> : <Circle className="h-3 w-3" />}{item.label}
              </button>
            ))}
            <span className={`chip gap-1 font-semibold ${ready ? 'chip-ok' : 'chip-warn'}`} aria-label={ready ? '已准备运行' : '尚未准备运行'}>{ready ? <Check className="h-3 w-3" /> : <Circle className="h-3 w-3" />}{ready ? '可运行' : '待准备'}</span>
          </div>
        </div>
      </div>
      <Dialog.Root open={openPanel !== null} onOpenChange={(open) => !open && onOpenPanel(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[70] bg-black/65 backdrop-blur-sm" />
          {openPanel && activeMeta && (
            <Dialog.Content aria-describedby={undefined} className={`fixed inset-y-0 z-[80] flex w-[min(92vw,360px)] flex-col border-border bg-background shadow-2xl ${activeMeta.side === 'left' ? 'left-0 border-r' : 'right-0 border-l'}`}>
              <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
                <Dialog.Title className="flex items-center gap-2 text-sm font-semibold text-zinc-100"><activeMeta.icon className="h-4 w-4 text-primary" />{activeMeta.label}</Dialog.Title>
                <Dialog.Close className="btn btn-ghost btn-sm" aria-label={`Close ${activeMeta.label} panel`}><X className="h-4 w-4" /></Dialog.Close>
              </div>
              <div className="min-h-0 flex-1 overflow-hidden">{openPanel === 'sources' ? sourcePanel : parameterPanel}</div>
            </Dialog.Content>
          )}
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
