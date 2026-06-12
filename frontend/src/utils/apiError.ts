/**
 * Normalize an axios/fetch error into a renderable string.
 *
 * FastAPI 422 validation errors put an *array of objects* in
 * `response.data.detail`; storing that raw into error state and rendering it
 * as a React child throws "Objects are not valid as a React child" and white-
 * screens the app (no ErrorBoundary exists). Always pass errors through here
 * before setState.
 */
export function apiErrorMessage(err: any, fallback = 'request failed'): string {
  const d = err?.response?.data?.detail;
  if (typeof d === 'string') return d;
  if (d != null) {
    try {
      return JSON.stringify(d);
    } catch {
      /* circular — fall through */
    }
  }
  return err?.message || fallback;
}
