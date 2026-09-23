import { ApiError } from './fetcher'

function detailOf(body: unknown): string | null {
  if (typeof body !== 'object' || body === null || !('detail' in body)) return null
  const { detail } = body
  if (typeof detail === 'string') return detail
  // FastAPI validation errors: a list of { loc, msg, ... }.
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item: unknown) =>
      typeof item === 'object' && item !== null && 'msg' in item && typeof item.msg === 'string'
        ? [item.msg]
        : [],
    )
    return messages.length > 0 ? messages.join('; ') : null
  }
  return null
}

/** A short message for any error a query or mutation can throw. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = typeof error.body === 'string' ? null : detailOf(error.body)
    return detail ?? `The server answered ${String(error.status)}.`
  }
  if (error instanceof Error && error.name === 'TypeError') return "Can't reach the server."
  return 'Something went wrong.'
}

export function isStatus(error: unknown, status: number): boolean {
  return error instanceof ApiError && error.status === status
}
