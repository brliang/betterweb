/** Thrown for any non-2xx API response, so TanStack Query sees it as an error. */
export class ApiError<TBody = unknown> extends Error {
  readonly status: number
  readonly body: TBody

  constructor(status: number, body: TBody) {
    super(`API request failed with status ${String(status)}`)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

/** Error type orval uses for generated hooks (`TError`). */
export type ErrorType<TBody> = ApiError<TBody>

/** fetch wrapper used by every generated API call (wired up in orval.config.ts). */
export async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  const isJson = response.headers.get('content-type')?.includes('application/json') ?? false
  const body: unknown = isJson ? await response.json() : await response.text()

  if (!response.ok) throw new ApiError(response.status, body)
  return body as T
}
