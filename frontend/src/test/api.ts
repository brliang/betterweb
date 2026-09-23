import { vi } from 'vitest'

export interface Call {
  method: string
  path: string
  body: unknown
  keepalive: boolean
}

export interface Reply {
  status?: number
  body?: unknown
}

/**
 * Replace fetch with canned API replies, keyed "METHOD /api/path" (query string excluded).
 * Unknown routes answer 404. Returns the calls made, in order.
 */
export function mockApi(routes: Record<string, Reply | ((call: Call) => Reply)>): Call[] {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://localhost')
      const call: Call = {
        method: init?.method ?? 'GET',
        path: url.pathname + url.search,
        body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
        keepalive: init?.keepalive ?? false,
      }
      calls.push(call)
      const route = routes[`${call.method} ${url.pathname}`]
      const reply = typeof route === 'function' ? route(call) : route
      const status = reply ? (reply.status ?? 200) : 404
      const body = reply ? reply.body : { detail: 'Not Found' }
      return Promise.resolve(
        body === undefined
          ? new Response(null, { status })
          : new Response(JSON.stringify(body), {
              status,
              headers: { 'content-type': 'application/json' },
            }),
      )
    }),
  )
  return calls
}
