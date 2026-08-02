export function extractError(error, fallback) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) {
    return detail
  }
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const message = detail.message || detail.reason || detail.error
    if (message) {
      return detail.code ? `${detail.code}：${message}` : String(message)
    }
  }
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((item) => item?.msg || item?.message || String(item))
      .filter(Boolean)
      .join('；')
  }
  return fallback
}
