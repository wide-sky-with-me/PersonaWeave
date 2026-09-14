import type { PortraitAssetItem } from '@/types';

const POSE_LABEL = /(?:^|_)(left|right)_(\d+)deg(?:_|$)/;

export const portraitYawFromLabel = (label: string): number => {
  const match = label.match(POSE_LABEL);
  if (!match) return 0;
  const degrees = Number(match[2]);
  return match[1] === 'left' ? -degrees : degrees;
};

export const describePortraitYaw = (yaw: number): string => {
  if (yaw === 0) return '正脸';
  return yaw > 0 ? `+${yaw}° 右侧` : `${yaw}° 左侧`;
};

/**
 * Selects a deterministic gallery covering frontal, profile, and lowered-chin views.
 * Pose coverage matters more than expression variety for profile face swapping.
 */
export const selectDiversePortraitAssets = (assets: PortraitAssetItem[], limit = 8): PortraitAssetItem[] => {
  if (limit <= 0) return [];

  const angles = assets
    .filter((asset) => asset.category === 'angle')
    .map((asset) => ({ asset, yaw: portraitYawFromLabel(asset.label), chinDown: asset.label.includes('chin_down') }));
  const selected: PortraitAssetItem[] = [];
  const selectedIds = new Set<string>();
  const add = (item: (typeof angles)[number] | undefined) => {
    if (!item || selected.length >= limit || selectedIds.has(item.asset.asset_id)) return;
    selected.push(item.asset);
    selectedIds.add(item.asset.asset_id);
  };
  const pick = (items: typeof angles, score: (item: (typeof angles)[number]) => number) =>
    items
      .filter((item) => !selectedIds.has(item.asset.asset_id))
      .sort((left, right) => score(left) - score(right) || left.asset.label.localeCompare(right.asset.label))[0];

  add(
    pick(
      angles.filter((item) => item.yaw === 0 && !item.chinDown),
      (item) => (item.asset.label === '01_front' ? 0 : 1),
    ),
  );
  add(
    pick(
      angles.filter((item) => item.yaw < 0 && !item.chinDown),
      (item) => item.yaw,
    ),
  );
  add(
    pick(
      angles.filter((item) => item.yaw > 0 && !item.chinDown),
      (item) => -item.yaw,
    ),
  );
  add(
    pick(
      angles.filter((item) => item.yaw < 0 && item.chinDown),
      (item) => item.yaw,
    ),
  );
  add(
    pick(
      angles.filter((item) => item.yaw > 0 && item.chinDown),
      (item) => -item.yaw,
    ),
  );
  add(
    pick(
      angles.filter((item) => item.yaw < 0 && !item.chinDown),
      (item) => Math.abs(item.yaw + 30),
    ),
  );
  add(
    pick(
      angles.filter((item) => item.yaw > 0 && !item.chinDown),
      (item) => Math.abs(item.yaw - 30),
    ),
  );
  add(
    pick(
      angles.filter((item) => item.yaw === 0 && item.chinDown),
      () => 0,
    ),
  );

  const remaining = assets
    .filter((asset) => !selectedIds.has(asset.asset_id) && asset.category !== 'contact_sheet')
    .sort((left, right) => {
      const roleOrder = Number(left.category !== 'angle') - Number(right.category !== 'angle');
      if (roleOrder) return roleOrder;
      return (
        Math.abs(portraitYawFromLabel(right.label)) - Math.abs(portraitYawFromLabel(left.label)) ||
        left.label.localeCompare(right.label)
      );
    });
  remaining.forEach((asset) => {
    if (selected.length < limit) {
      selected.push(asset);
      selectedIds.add(asset.asset_id);
    }
  });
  return selected;
};
