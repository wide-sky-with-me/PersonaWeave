import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AppHeader } from './AppHeader';
import { useStudioStore } from '@/stores/useStudioStore';
vi.mock('@/features/history/components/RecentJobsPopover', () => ({ RecentJobsPopover: () => null }));
afterEach(cleanup);
it('does not claim readiness or GPU acceleration from missing health or provider availability', () => {
  useStudioStore.setState({ serverHealth: null, isProcessing: false });
  const view = render(<AppHeader />);
  expect(screen.getByText('服务未知')).toBeInTheDocument();
  useStudioStore.setState({
    serverHealth: {
      status: 'ok',
      app_name: 'PersonaWeave',
      version: 'test',
      pipeline_ready: true,
      gpu_available: true,
      providers: ['CUDAExecutionProvider'],
    },
  });
  view.rerender(<AppHeader />);
  expect(screen.getByText('本地就绪')).toBeInTheDocument();
  expect(screen.queryByText('GPU')).not.toBeInTheDocument();
  expect(screen.queryByTitle('GPU 硬件加速已启用')).not.toBeInTheDocument();
});

it('shows actual CPU fallback even when the runtime advertises CUDA capability', () => {
  useStudioStore.setState({
    serverHealth: {
      status: 'ok',
      app_name: 'PersonaWeave',
      version: 'test',
      pipeline_ready: true,
      gpu_available: true,
      providers: ['CUDAExecutionProvider'],
      model_providers: { detector: ['CPUExecutionProvider'], inswapper_128: ['CPUExecutionProvider'] },
    },
  });
  render(<AppHeader />);
  expect(screen.getByText('CPU 模式')).toBeInTheDocument();
  expect(screen.queryByText('CUDA 已加载')).not.toBeInTheDocument();
});
