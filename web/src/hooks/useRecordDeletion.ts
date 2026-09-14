import { useEffect, useRef, useState } from 'react';
import axios from 'axios';

export interface DeletableRecord {
  id: string;
  revision: number;
}

/** Delete eligible records independently; conflicts keep their rows and selections. */
export function useRecordDeletion({
  records,
  description,
  remove,
  onDeleted,
  refresh,
}: {
  records: DeletableRecord[];
  description: string;
  remove: (record: DeletableRecord) => Promise<void>;
  onDeleted: (id: string) => void | Promise<void>;
  refresh: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lock = useRef(false);
  useEffect(() => {
    const eligible = new Set(records.map((record) => record.id));
    setSelected((current) => {
      const next = new Set([...current].filter((id) => eligible.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [records]);
  const toggle = (ids: string[], checked: boolean) =>
    setSelected((current) => {
      const next = new Set(current);
      ids.forEach((id) => (checked ? next.add(id) : next.delete(id)));
      return next;
    });
  const request = async (ids: string[]) => {
    if (lock.current) return;
    const chosen = records.filter((record) => ids.includes(record.id));
    if (!chosen.length || !window.confirm(`删除选中的 ${chosen.length} 条${description}？此操作无法撤销。`)) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    const failed: string[] = [];
    try {
      // Serial requests avoid flooding storage; a lost response can safely retry
      // removal, and an already absent record is treated as deleted.
      for (const record of chosen) {
        try {
          await remove(record);
        } catch (err) {
          if (!axios.isAxiosError(err) || err.response?.status !== 404) {
            failed.push(record.id);
            continue;
          }
        }
        await onDeleted(record.id);
        toggle([record.id], false);
      }
      if (failed.length)
        setError(`${failed.length} 条记录未删除，状态可能已变化或服务暂不可用。已刷新，请检查后重试。`);
    } catch {
      setError('删除结果未能完整刷新，请重新读取记录后确认。');
    } finally {
      refresh();
      lock.current = false;
      setBusy(false);
    }
  };
  return { selected, toggle, busy, error, request };
}
