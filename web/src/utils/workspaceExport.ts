import JSZip from 'jszip';
import type { TargetImageItem } from '@/types';
import { sessionApi } from '@/features/refinement/sessionApi';
import { syncAcceptedResult } from '@/features/refinement/acceptedResults';

export function hasWorkspaceResult(target: TargetImageItem) {
  return target.status === 'SUCCESS' && Boolean(target.refinedUrl || target.processedUrl);
}

/** Export the current editor's final images, checking live acceptance without generating or re-encoding. */
export async function buildWorkspaceZip(targets: TargetImageItem[]) {
  const zip = new JSZip();
  const folder = zip.folder('PersonaWeave_Results')!;
  const failures: string[] = [];
  const usedNames = new Set<string>();
  let packed = 0;
  for (const [index, target] of targets.filter(hasWorkspaceResult).entries()) {
    try {
      let url = target.refinedUrl || target.processedUrl!;
      if (target.refinementSessionId) {
        try {
          const session = await sessionApi.get(target.refinementSessionId);
          if (session.target_id !== target.id) throw new Error('精修会话与图片不对应');
          if (session.accepted_version_id) {
            if (!session.accepted_image_url) throw new Error('已接受版本的文件不可用');
            url = session.accepted_image_url;
          }
          syncAcceptedResult(session);
        } catch (error) {
          // An independently frozen accepted result remains exportable after its session is removed.
          // Without that copy, do not silently export an outdated local baseline as the final result.
          if (target.refinedUrl?.startsWith('data:image/')) url = target.refinedUrl;
          else throw error;
        }
      }
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const bytes = new Uint8Array(await response.arrayBuffer());
      const extension =
        bytes[0] === 137 && bytes[1] === 80 && bytes[2] === 78 && bytes[3] === 71
          ? 'png'
          : bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255
            ? 'jpg'
            : String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF' &&
                String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP'
              ? 'webp'
              : null;
      if (!extension) throw new Error('下载内容不是受支持的图片');
      const stem = target.name.replace(/\.[^/.]+$/, '').replace(/[\\/:*?"<>|]/g, '_') || `result_${index + 1}`;
      let filename = `${stem}_result.${extension}`;
      for (let suffix = 2; usedNames.has(filename); suffix++) filename = `${stem}_result_${suffix}.${extension}`;
      usedNames.add(filename);
      folder.file(filename, bytes);
      packed++;
    } catch (error) {
      failures.push(`${target.name}: ${error instanceof Error ? error.message : '下载失败'}`);
    }
  }
  if (!packed) throw new Error(failures.join('\n') || '暂无可下载的已完成图片');
  if (failures.length) folder.file('FAILED_DOWNLOADS.txt', failures.join('\n'));
  return { zip, packed, failures };
}
