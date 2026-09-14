import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ResponsiveWorkflowAccess } from './ResponsiveWorkflowAccess';

describe('ResponsiveWorkflowAccess', () => {
  it('exposes labelled narrow-width panel triggers and accessible dialogs', async () => {
    const user = userEvent.setup();
    const onOpenPanel = vi.fn();
    const { rerender } = render(
      <ResponsiveWorkflowAccess
        sourcePanel={<div>Source content</div>}
        parameterPanel={<div>Parameter content</div>}
        openPanel={null}
        onOpenPanel={onOpenPanel}
        readiness={[]}
      />,
    );

    await user.click(screen.getByRole('button', { name: '打开源身份面板' }));
    expect(onOpenPanel).toHaveBeenCalledWith('sources');

    rerender(
      <ResponsiveWorkflowAccess
        sourcePanel={<div>Source content</div>}
        parameterPanel={<div>Parameter content</div>}
        openPanel="sources"
        onOpenPanel={onOpenPanel}
        readiness={[]}
      />,
    );
    expect(screen.getByRole('dialog', { name: '源身份' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '关闭源身份面板' })).toBeInTheDocument();
  });

  it('displays readiness without duplicate mapping actions and opens task controls on narrow screens', async () => {
    const user = userEvent.setup();
    const onOpenPanel = vi.fn();
    render(
      <ResponsiveWorkflowAccess
        sourcePanel={null}
        parameterPanel={null}
        openPanel={null}
        onOpenPanel={onOpenPanel}
        readiness={[
          { label: 'Sources 0', complete: false },
          { label: 'Mappings 0', complete: false },
        ]}
      />,
    );
    expect(screen.queryByRole('button', { name: /Mappings 0/ })).not.toBeInTheDocument();
    expect(screen.getByText('Mappings 0')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '打开换脸控制面板' }));
    expect(onOpenPanel).toHaveBeenCalledWith('parameters');
    expect(screen.getByLabelText('尚未准备运行')).toBeInTheDocument();
  });
});
