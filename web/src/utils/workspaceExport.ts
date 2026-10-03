import JSZip from 'jszip';
import type { TargetImageItem } from '@/types';
import { sessionApi } from '@/features/refinement/sessionApi';
import {
  hasFrozenAcceptedResult,
  syncAcceptedResult,
  unavailableRefinementMessage,
} from '@/features/refinement/acceptedResults';
import { apiErrorMessage, isResourceUnavailable } from './apiError';

export function hasWorkspaceResult(target: TargetImageItem) {
  return (
    target.status === 'SUCCESS' &&
    !target.refinementResultUnavailable &&
    Boolean(target.refinedUrl || target.processedUrl)
  );
}

/** Shared single/ZIP download boundary: confirm the final version before fetching its bytes. */
export async function fetchWorkspaceResult(target: TargetImageItem) {
  if (target.refinementResultUnavailable) throw new Error(unavailableRefinementMessage);
  if (!hasWorkspaceResult(target)) throw new Error('此图片没有可下载的已完成结果');
  let url = target.refinedUrl || target.processedUrl!;
  if (target.refinementSessionId) {
    let session;
    try {
      session = await sessionApi.get(target.refinementSessionId);
    } catch (error) {
      // Only confirmed deletion permits an independently frozen copy. A network/server
      // failure cannot tell us whether this copy is still the accepted final version.
      if (isResourceUnavailable(error) && hasFrozenAcceptedResult(target)) url = target.refinedUrl!;
      else throw error;
    }
    if (session) {
      if (session.target_id !== target.id || session.id !== target.refinementSessionId)
        throw new Error('精修会话与图片不对应');
      if (session.accepted_version_id) {
        if (!session.accepted_image_url) throw new Error('已接受版本的文件不可用');
        url = session.accepted_image_url;
      }
      syncAcceptedResult(session);
    }
  }
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const bytes = await response.arrayBuffer();
  const signature = new Uint8Array(bytes);
  const extension =
    signature[0] === 137 && signature[1] === 80 && signature[2] === 78 && signature[3] === 71
      ? 'png'
      : signature[0] === 255 && signature[1] === 216 && signature[2] === 255
        ? 'jpg'
        : String.fromCharCode(...signature.slice(0, 4)) === 'RIFF' &&
            String.fromCharCode(...signature.slice(8, 12)) === 'WEBP'
          ? 'webp'
          : null;
  if (!extension) throw new Error('下载内容不是受支持的图片');
  return { bytes, extension };
}

/** Export the current editor's final images, checking live acceptance without generating or re-encoding. */
export async function buildWorkspaceZip(targets: TargetImageItem[]) {
  const zip = new JSZip();
  const folder = zip.folder('PersonaWeave_Results')!;
  const failures: string[] = [];
  const usedNames = new Set<string>();
  let packed = 0;
  // Include unavailable accepted results in the failure manifest instead of silently
  // substituting their still-existing local baseline or omitting them from a mixed export.
  for (const [index, target] of targets
    .filter((item) => hasWorkspaceResult(item) || item.refinementResultUnavailable)
    .entries()) {
    try {
      const { bytes, extension } = await fetchWorkspaceResult(target);
      const stem = target.name.replace(/\.[^/.]+$/, '').replace(/[\\/:*?"<>|]/g, '_') || `result_${index + 1}`;
      let filename = `${stem}_result.${extension}`;
      for (let suffix = 2; usedNames.has(filename); suffix++) filename = `${stem}_result_${suffix}.${extension}`;
      usedNames.add(filename);
      folder.file(filename, bytes);
      packed++;
    } catch (error) {
      failures.push(`${target.name}: ${apiErrorMessage(error, '下载失败')}`);
    }
  }
  if (!packed) throw new Error(failures.join('\n') || '暂无可下载的已完成图片');
  if (failures.length) folder.file('FAILED_DOWNLOADS.txt', failures.join('\n'));
  return { zip, packed, failures };
}
