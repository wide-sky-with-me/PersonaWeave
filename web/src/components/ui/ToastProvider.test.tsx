import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider, useToast } from './ToastProvider';

function Trigger() {
  const toast = useToast();
  return (
    <button
      onClick={() => {
        toast({ title: '换脸失败', description: '人物 #1：缺少侧脸参考', kind: 'error' });
        toast({ title: '换脸失败', description: '人物 #1：缺少侧脸参考', kind: 'error' });
        toast({ title: '部分人物未换脸', description: '人物 #2：未匹配', kind: 'warning', modal: true });
      }}
    >
      运行
    </button>
  );
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
describe('acknowledged failure dialogs', () => {
  it('retains errors until acknowledged and queues distinct failures without duplicates', () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText('运行'));
    expect(screen.getByRole('dialog')).toHaveTextContent('人物 #1：缺少侧脸参考');
    expect(screen.getByText('还有 1 条提示')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(6000));
    expect(screen.getByRole('dialog')).toHaveTextContent('换脸失败');
    fireEvent.click(screen.getByText('知道了'));
    expect(screen.getByRole('dialog')).toHaveTextContent('人物 #2：未匹配');
    fireEvent.click(screen.getByText('知道了'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
