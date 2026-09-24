/** Preserve server explanations without displaying request bodies, keys or image data. */
export function apiErrorMessage(error: unknown, fallback = '操作失败，请重试'): string {
  const value = error as { response?: { data?: { detail?: unknown; message?: unknown } }; message?: unknown } | null;
  const detail = value?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .filter((item) => item && typeof item.msg === 'string')
      .map((item) => {
        const location = Array.isArray(item.loc) ? item.loc.filter((part: unknown) => part !== 'body').join('.') : '';
        return location ? `${location}：${item.msg}` : item.msg;
      });
    if (messages.length) return messages.join('\n');
  }
  const message = value?.response?.data?.message;
  if (typeof message === 'string' && message.trim()) return message;
  return typeof value?.message === 'string' && value.message.trim() ? value.message : fallback;
}
