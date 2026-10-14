// Decision: .agents/notes/frontend/20261014-verify-project-assets-without-secure-context.md
import { sha256 } from '@noble/hashes/sha2.js';
import { bytesToHex } from '@noble/hashes/utils.js';

/** Verify identical bytes on HTTP deployments as well as secure browser contexts. */
export function sha256Hex(data: ArrayBuffer | Uint8Array): string {
  return bytesToHex(sha256(data instanceof Uint8Array ? data : new Uint8Array(data)));
}

/** Request IDs retain secure entropy when HTTP browsers omit crypto.randomUUID. */
export function requestUuid(): string {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID();
  if (typeof cryptoApi?.getRandomValues !== 'function') throw new Error('当前浏览器缺少 Web Crypto 随机数接口');
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytesToHex(bytes);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
