import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ResponsiveWorkflowAccess } from './ResponsiveWorkflowAccess';

describe('ResponsiveWorkflowAccess', () => {
  it('exposes labelled narrow-width panel triggers and accessible dialogs', async () => {
    const user = userEvent.setup();
    const onOpenPanel = vi.fn();
    const { rerender } = render(
      <ResponsiveWorkflowAccess sourcePanel={<div>Source content</div>} parameterPanel={<div>Parameter content</div>} openPanel={null} onOpenPanel={onOpenPanel} onOpenMapping={vi.fn()} readiness={[]} />,
    );

    await user.click(screen.getByRole('button', { name: '打开源身份面板' }));
    expect(onOpenPanel).toHaveBeenCalledWith('sources');

    rerender(<ResponsiveWorkflowAccess sourcePanel={<div>Source content</div>} parameterPanel={<div>Parameter content</div>} openPanel="sources" onOpenPanel={onOpenPanel} onOpenMapping={vi.fn()} readiness={[]} />);
    expect(screen.getByRole('dialog', { name: 'Sources' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close Sources panel' })).toBeInTheDocument();
  });

  it('routes readiness actions to the matching workflow', async () => {
    const user = userEvent.setup();
    const onOpenPanel = vi.fn();
    const onOpenMapping = vi.fn();
    render(<ResponsiveWorkflowAccess sourcePanel={null} parameterPanel={null} openPanel={null} onOpenPanel={onOpenPanel} onOpenMapping={onOpenMapping} readiness={[{ label: 'Sources 0', complete: false, action: 'sources' }, { label: 'Mappings 0', complete: false, action: 'mapping' }]} />);

    await user.click(screen.getByRole('button', { name: /Sources 0/ }));
    await user.click(screen.getByRole('button', { name: /Mappings 0/ }));
    expect(onOpenPanel).toHaveBeenCalledWith('sources');
    expect(onOpenMapping).toHaveBeenCalledOnce();
    expect(screen.getByLabelText('尚未准备运行')).toBeInTheDocument();
  });
});
