import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useStudioStore } from '@/stores/useStudioStore';
import { MultiPersonMatrixModal } from './MultiPersonMatrixModal';
import { resolveExecutionMappings } from '@/utils/swapPayload';

vi.mock('@/components/ui/ToastProvider', () => ({ useToast: () => vi.fn() }));
afterEach(() => {
  cleanup();
  useStudioStore.setState(useStudioStore.getInitialState());
});

it('can replace a restored null mapping by explicitly choosing one of three local faces', () => {
  useStudioStore.setState({
    isMatrixOpen: true,
    sourcePeople: [{ id: 7, name: '测试素材' }],
    sourceGallery: [{ id: 'ref', name: 'ref', personId: 7, kind: 'manual', imageData: '/source' }],
    targetImages: [
      {
        id: 'target',
        name: '20260929-184609.jpg',
        url: '/target',
        status: 'FAILED',
        faces: [1, 2, 3].map((index) => ({
          index,
          global_person_id: null,
          bbox: [index * 20, 0, index * 20 + 10, 10],
        })),
      },
    ],
    personMappings: [
      {
        person_index: null as unknown as number,
        target_reference_person_index: 1,
        target_reference_url: '/target',
        source_asset_ids: [],
        source_manual_images: ['/source'],
      },
    ],
  });
  render(<MultiPersonMatrixModal />);
  expect(screen.getByText('合影人物 1')).toBeInTheDocument();
  expect(screen.getByText('合影人物 2')).toBeInTheDocument();
  expect(screen.getByText('合影人物 3')).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole('button', { name: /测试素材/ })[1]);
  fireEvent.click(screen.getByRole('button', { name: /保存任务映射/ }));
  const state = useStudioStore.getState();
  expect(state.personMappings).toHaveLength(1);
  expect(state.personMappings[0]).toMatchObject({ person_index: 2, target_reference_person_index: 2 });
  expect(resolveExecutionMappings(state).ready).toBe(true);
});
