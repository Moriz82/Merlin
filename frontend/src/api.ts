export type Session = { user: { id: string; name: string; role: string } | null; csrf: string | null; app: string; engagement: { id: string; name: string }; mode: string }
export type RecordItem = { id: string; kind: string; revision_id: string; data: Record<string, unknown>; updated_at: string }
export class ApiError extends Error { status: number; detail: unknown; constructor(status: number, detail: unknown) { super(typeof detail === 'string' ? detail : (detail as { message?: string })?.message || `Request failed (${status})`); this.status = status; this.detail = detail } }
export class ApiClient {
  csrf: string | null = null
  async request<T>(path: string, init: RequestInit = {}): Promise<T> { const headers = new Headers(init.headers); if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json'); if (init.method && init.method !== 'GET') { if (this.csrf) headers.set('X-CSRF-Token', this.csrf); headers.set('Accept', 'application/json') }; const response = await fetch(path, { credentials: 'same-origin', ...init, headers }); const contentType = response.headers.get('content-type') || ''; const body = response.status === 204 ? undefined : contentType.includes('application/json') ? await response.json() : await response.text(); if (!response.ok) throw new ApiError(response.status, (body as { detail?: unknown })?.detail ?? body); return body as T }
  get<T>(path: string) { return this.request<T>(path) }
  post<T>(path: string, body?: unknown) { return this.request<T>(path, { method: 'POST', body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body) }) }
  put<T>(path: string, body: unknown) { return this.request<T>(path, { method: 'PUT', body: JSON.stringify(body) }) }
}
export const api = new ApiClient()
export function downloadJson(filename: string, value: unknown) { const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' })); const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url) }
