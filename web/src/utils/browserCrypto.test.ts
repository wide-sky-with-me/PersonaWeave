import { webcrypto } from 'node:crypto';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { requestUuid, sha256Hex } from './browserCrypto';

afterEach(() => vi.unstubAllGlobals());

const httpCrypto = () => ({ getRandomValues: webcrypto.getRandomValues.bind(webcrypto) });

describe('HTTP browser crypto support', () => {
  it('verifies SHA-256 without crypto.subtle and preserves typed-array offsets', () => {
    vi.stubGlobal('crypto', httpCrypto());
    expect(sha256Hex(new ArrayBuffer(0))).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    const bytes = new Uint8Array([0, 97, 98, 99, 0]);
    expect(sha256Hex(bytes.subarray(1, 4))).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  });

  it('produces unique v4 request IDs using getRandomValues when randomUUID is absent', () => {
    vi.stubGlobal('crypto', httpCrypto());
    const ids = Array.from({ length: 100 }, () => requestUuid());
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it('uses native UUIDs when available and reports missing entropy rather than weakening it', () => {
    const randomUUID = vi.fn(() => 'native-request-id');
    vi.stubGlobal('crypto', { randomUUID });
    expect(requestUuid()).toBe('native-request-id');
    expect(randomUUID).toHaveBeenCalledOnce();
    vi.stubGlobal('crypto', undefined);
    expect(() => requestUuid()).toThrow('当前浏览器缺少 Web Crypto 随机数接口');
  });
});
