import { describe, expect, it } from 'vitest';
import type { PortraitAssetItem } from '@/types';
import { portraitYawFromLabel, selectDiversePortraitAssets } from './portraitAssetSelection';

const asset = (label: string, category: PortraitAssetItem['category'] = 'angle'): PortraitAssetItem => ({
  asset_id: label,
  category,
  label,
  name: label,
});

describe('portrait asset selection', () => {
  it('parses arbitrary labelled yaw angles', () => {
    expect(portraitYawFromLabel('09_left_75deg')).toBe(-75);
    expect(portraitYawFromLabel('12_right_75deg_chin_down')).toBe(75);
    expect(portraitYawFromLabel('01_front')).toBe(0);
  });

  it('prioritizes both profile directions and lowered-chin variants', () => {
    const chosen = selectDiversePortraitAssets([
      asset('03_right_15deg'),
      asset('02_gentle_smile_front', 'expression'),
      asset('11_left_75deg_chin_down'),
      asset('01_front'),
      asset('04_left_30deg'),
      asset('12_right_75deg_chin_down'),
      asset('09_left_75deg'),
      asset('05_right_30deg'),
      asset('10_right_75deg'),
      asset('06_chin_down_front'),
    ]);

    expect(chosen.map((item) => item.label)).toEqual([
      '01_front',
      '09_left_75deg',
      '10_right_75deg',
      '11_left_75deg_chin_down',
      '12_right_75deg_chin_down',
      '04_left_30deg',
      '05_right_30deg',
      '06_chin_down_front',
    ]);
  });

  it('is stable, excludes contact sheets, and respects the limit', () => {
    const assets = [
      asset('02_left_15deg'),
      asset('contact_sheet', 'contact_sheet'),
      asset('01_neutral_front', 'expression'),
    ];
    expect(selectDiversePortraitAssets(assets, 2).map((item) => item.label)).toEqual([
      '02_left_15deg',
      '01_neutral_front',
    ]);
  });
});
