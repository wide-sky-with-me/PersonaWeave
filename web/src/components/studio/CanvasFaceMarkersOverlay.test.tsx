import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CanvasFaceMarkersOverlay } from './CanvasFaceMarkersOverlay';
import { useStudioStore } from '@/stores/useStudioStore';
import type { DetectedFaceBox, PersonMappingItem, SourcePerson, SourceReference } from '@/types';

afterEach(() => {
  cleanup();
  useStudioStore.setState(useStudioStore.getInitialState());
});

function mountOverlay(overrides: Partial<React.ComponentProps<typeof CanvasFaceMarkersOverlay>> = {}) {
  return render(
    <CanvasFaceMarkersOverlay
      faces={[{ index: 1, global_person_id: 1, bbox: [100, 100, 200, 200] }]}
      imageWidth={1000}
      imageHeight={1000}
      selectedTargetPersonId={null}
      onSelectTargetPersonId={vi.fn()}
      personMappings={[]}
      sourcePeople={[
        { id: 101, name: 'Alice' },
        { id: 102, name: 'Bob' },
      ]}
      sourceGallery={[]}
      distinctGlobalPersons={[1, 2]}
      onAssignSourcePerson={vi.fn()}
      onReassignFaceGlobalId={vi.fn()}
      {...overrides}
    />,
  );
}

const marker = () => screen.getByRole('button', { name: '选择目标人物 1，位置人脸 1' });

describe('CanvasFaceMarkersOverlay', () => {
  const defaultFaces: DetectedFaceBox[] = [
    { index: 1, global_person_id: 1, bbox: [100, 100, 200, 200] },
    { index: 2, global_person_id: 2, bbox: [300, 100, 400, 200] },
  ];

  const defaultSourcePeople: SourcePerson[] = [
    { id: 101, name: 'Alice' },
    { id: 102, name: 'Bob' },
  ];

  const defaultSourceGallery: SourceReference[] = [
    { id: 'ref-1', name: 'alice_1.jpg', personId: 101, kind: 'manual', imageData: 'data:image/jpeg;base64,aaa' },
    { id: 'ref-2', name: 'bob_1.jpg', personId: 102, kind: 'manual', imageData: 'data:image/jpeg;base64,bbb' },
  ];

  const defaultMappings: PersonMappingItem[] = [
    {
      person_index: 1,
      target_reference_url: '/test.jpg',
      target_reference_person_index: 1,
      source_identity_id: '101',
      source_asset_ids: [],
      source_manual_images: [],
      label: 'Alice',
    },
  ];

  it('renders face markers with ARIA accessibility labels and bounding styles', () => {
    render(
      <CanvasFaceMarkersOverlay
        faces={defaultFaces}
        imageWidth={1000}
        imageHeight={1000}
        selectedTargetPersonId={1}
        onSelectTargetPersonId={vi.fn()}
        personMappings={defaultMappings}
        sourcePeople={defaultSourcePeople}
        sourceGallery={defaultSourceGallery}
        distinctGlobalPersons={[1, 2]}
        onAssignSourcePerson={vi.fn()}
        onReassignFaceGlobalId={vi.fn()}
      />,
    );

    const marker1 = screen.getByRole('button', { name: '选择目标人物 1，位置人脸 1' });
    const marker2 = screen.getByRole('button', { name: '选择目标人物 2，位置人脸 2' });
    expect(marker1).toBeInTheDocument();
    expect(marker2).toBeInTheDocument();
    expect(marker1).toHaveAttribute('aria-pressed', 'true');
    expect(marker2).toHaveAttribute('aria-pressed', 'false');
  });

  it('opens quick assignment popover upon clicking the marker or badge', () => {
    const onSelect = vi.fn();
    render(
      <CanvasFaceMarkersOverlay
        faces={defaultFaces}
        imageWidth={1000}
        imageHeight={1000}
        selectedTargetPersonId={null}
        onSelectTargetPersonId={onSelect}
        personMappings={defaultMappings}
        sourcePeople={defaultSourcePeople}
        sourceGallery={defaultSourceGallery}
        distinctGlobalPersons={[1, 2]}
        onAssignSourcePerson={vi.fn()}
        onReassignFaceGlobalId={vi.fn()}
      />,
    );

    expect(screen.queryByText(/快速指派/)).not.toBeInTheDocument();

    const marker1 = screen.getByRole('button', { name: '选择目标人物 1，位置人脸 1' });
    fireEvent.click(marker1);

    expect(onSelect).toHaveBeenCalledWith(1);
    expect(screen.getByText('人脸 #1 快速指派')).toBeInTheDocument();
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('Bob')).toBeInTheDocument();
  });

  it('assigns a source person and closes popover when clicking a person item', () => {
    const onAssign = vi.fn();
    render(
      <CanvasFaceMarkersOverlay
        faces={defaultFaces}
        imageWidth={1000}
        imageHeight={1000}
        selectedTargetPersonId={1}
        onSelectTargetPersonId={vi.fn()}
        personMappings={defaultMappings}
        sourcePeople={defaultSourcePeople}
        sourceGallery={defaultSourceGallery}
        distinctGlobalPersons={[1, 2]}
        onAssignSourcePerson={onAssign}
        onReassignFaceGlobalId={vi.fn()}
      />,
    );

    // Open popover for face #1
    fireEvent.click(screen.getByRole('button', { name: '选择目标人物 1，位置人脸 1' }));
    expect(screen.getByText('人脸 #1 快速指派')).toBeInTheDocument();

    // Click Bob to assign
    fireEvent.click(screen.getByText('Bob'));
    expect(onAssign).toHaveBeenCalledWith(1, 1, 102);
    expect(screen.queryByText('人脸 #1 快速指派')).not.toBeInTheDocument();
  });

  it('allows unassigning mapping with 保持原样（不换此人）', () => {
    const onAssign = vi.fn();
    render(
      <CanvasFaceMarkersOverlay
        faces={defaultFaces}
        imageWidth={1000}
        imageHeight={1000}
        selectedTargetPersonId={1}
        onSelectTargetPersonId={vi.fn()}
        personMappings={defaultMappings}
        sourcePeople={defaultSourcePeople}
        sourceGallery={defaultSourceGallery}
        distinctGlobalPersons={[1, 2]}
        onAssignSourcePerson={onAssign}
        onReassignFaceGlobalId={vi.fn()}
      />,
    );

    // Open popover for mapped face #1
    fireEvent.click(screen.getByRole('button', { name: '选择目标人物 1，位置人脸 1' }));
    const unassignBtn = screen.getByRole('button', { name: '保持原样（不换此人）' });
    fireEvent.click(unassignBtn);

    expect(onAssign).toHaveBeenCalledWith(1, 1, null);
    expect(screen.queryByText('人脸 #1 快速指派')).not.toBeInTheDocument();
  });

  it('opens multi-person matrix modal when clicking 矩阵大表 button', () => {
    expect(useStudioStore.getState().isMatrixOpen).toBe(false);

    render(
      <CanvasFaceMarkersOverlay
        faces={defaultFaces}
        imageWidth={1000}
        imageHeight={1000}
        selectedTargetPersonId={1}
        onSelectTargetPersonId={vi.fn()}
        personMappings={defaultMappings}
        sourcePeople={defaultSourcePeople}
        sourceGallery={defaultSourceGallery}
        distinctGlobalPersons={[1, 2]}
        onAssignSourcePerson={vi.fn()}
        onReassignFaceGlobalId={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '选择目标人物 1，位置人脸 1' }));
    const matrixBtn = screen.getByRole('button', { name: /矩阵大表/ });
    fireEvent.click(matrixBtn);

    expect(useStudioStore.getState().isMatrixOpen).toBe(true);
    expect(screen.queryByText('人脸 #1 快速指派')).not.toBeInTheDocument();
  });

  it('reassigns global person ID from inline popover', () => {
    const onReassign = vi.fn();
    render(
      <CanvasFaceMarkersOverlay
        faces={defaultFaces}
        imageWidth={1000}
        imageHeight={1000}
        selectedTargetPersonId={1}
        onSelectTargetPersonId={vi.fn()}
        personMappings={defaultMappings}
        sourcePeople={defaultSourcePeople}
        sourceGallery={defaultSourceGallery}
        distinctGlobalPersons={[1, 2]}
        onAssignSourcePerson={vi.fn()}
        onReassignFaceGlobalId={onReassign}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '选择目标人物 1，位置人脸 1' }));
    const id2Btn = screen.getByRole('button', { name: '归属人物 #2' });
    fireEvent.click(id2Btn);

    expect(onReassign).toHaveBeenCalledWith(1, 2);
    expect(screen.queryByText('人脸 #1 快速指派')).not.toBeInTheDocument();
  });

  it('dismisses popover when pressing Escape', () => {
    render(
      <CanvasFaceMarkersOverlay
        faces={defaultFaces}
        imageWidth={1000}
        imageHeight={1000}
        selectedTargetPersonId={1}
        onSelectTargetPersonId={vi.fn()}
        personMappings={defaultMappings}
        sourcePeople={defaultSourcePeople}
        sourceGallery={defaultSourceGallery}
        distinctGlobalPersons={[1, 2]}
        onAssignSourcePerson={vi.fn()}
        onReassignFaceGlobalId={vi.fn()}
      />,
    );

    const marker = screen.getByRole('button', { name: '选择目标人物 1，位置人脸 1' });
    fireEvent.click(marker);
    expect(screen.getByText('人脸 #1 快速指派')).toBeInTheDocument();

    fireEvent.keyDown(marker, { key: 'Escape' });
    expect(screen.queryByText('人脸 #1 快速指派')).not.toBeInTheDocument();
  });

  describe('keyboard and focus', () => {
    it.each([
      ['Enter', '{Enter}'],
      ['Space', ' '],
    ])('assigns a person when %s is pressed on a popover button', async (_name, keys) => {
      const user = userEvent.setup();
      const onAssign = vi.fn();
      mountOverlay({ onAssignSourcePerson: onAssign });
      await user.click(marker());
      screen.getByRole('button', { name: /Bob/ }).focus();
      await user.keyboard(keys);
      expect(onAssign).toHaveBeenCalledWith(1, 1, 102);
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('opens and closes from the marker with the keyboard', async () => {
      const user = userEvent.setup();
      mountOverlay();
      marker().focus();
      await user.keyboard('{Enter}');
      expect(screen.getByRole('dialog', { name: '人脸 #1 快速指派' })).toBeInTheDocument();
      expect(marker()).toHaveAttribute('aria-expanded', 'true');
    });

    it('moves focus into the dialog and returns it to the marker on Escape from inside', async () => {
      const user = userEvent.setup();
      mountOverlay();
      await user.click(marker());
      const dialog = screen.getByRole('dialog');
      expect(dialog).toHaveFocus();
      screen.getByRole('button', { name: /Bob/ }).focus();
      await user.keyboard('{Escape}');
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(marker()).toHaveFocus();
    });

    it('closes on a pointer press outside but not inside the dialog', async () => {
      const user = userEvent.setup();
      mountOverlay();
      await user.click(marker());
      await user.pointer({ keys: '[MouseLeft>]', target: screen.getByRole('dialog') });
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      await user.pointer({ keys: '[MouseLeft>]', target: document.body });
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('keeps interactive content out of the marker: the popover is a sibling and the badge is plain text', async () => {
    const user = userEvent.setup();
    mountOverlay();
    await user.click(marker());
    const dialog = screen.getByRole('dialog');
    expect(marker().contains(dialog)).toBe(false);
    expect(marker().querySelector('button, a, input')).toBeNull();
    expect(dialog.parentElement).toBe(document.body);
  });
});
