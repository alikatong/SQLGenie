export function extractError(error, fallback) {
  return error?.response?.data?.detail || fallback
}
