import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/fetcher'
import type { EventIn } from '../api/generated/api'
import { EventQueue, FLUSH_DELAY_MS, MAX_BATCH } from './eventQueue'

const seen = (n: number) => ({
  document_id: n,
  recommendation_id: 100 + n,
  surface: 'feed' as const,
  position: n,
})

describe('EventQueue', () => {
  let batches: { events: EventIn[]; keepalive: boolean }[]
  let fail: Error | null
  let queue: EventQueue

  beforeEach(() => {
    vi.useFakeTimers()
    batches = []
    fail = null
    queue = new EventQueue((events, keepalive) => {
      if (fail) return Promise.reject(fail)
      batches.push({ events, keepalive })
      return Promise.resolve()
    })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('batches impressions until the flush delay', async () => {
    queue.impression(seen(1))
    queue.impression(seen(2))
    expect(batches).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(FLUSH_DELAY_MS)
    expect(batches).toHaveLength(1)
    expect(batches[0]?.events.map((e) => [e.kind, e.document_id])).toEqual([
      ['impression', 1],
      ['impression', 2],
    ])
    expect(batches[0]?.keepalive).toBe(false)
  })

  it('logs an impression once per recommendation', async () => {
    queue.impression(seen(1))
    queue.impression(seen(1))
    await queue.flush()
    queue.impression(seen(1))
    await queue.flush()
    expect(batches.flatMap((b) => b.events)).toHaveLength(1)
  })

  it('sends a full batch without waiting', async () => {
    for (let n = 0; n < MAX_BATCH + 1; n++) queue.impression(seen(n))
    await vi.advanceTimersByTimeAsync(0)
    // The flush a full batch starts also takes what arrives while it is sending.
    expect(batches.map((b) => b.events.length)).toEqual([MAX_BATCH, 1])
  })

  it('sends a click at once, with keepalive, along with pending impressions', async () => {
    queue.impression(seen(1))
    queue.click(seen(1))
    await vi.advanceTimersByTimeAsync(0)
    expect(batches).toHaveLength(1)
    expect(batches[0]?.keepalive).toBe(true)
    expect(batches[0]?.events.map((e) => e.kind)).toEqual(['impression', 'click'])
  })

  it('keeps a batch for retry when the server is unreachable', async () => {
    fail = new TypeError('Failed to fetch')
    queue.impression(seen(1))
    await queue.flush()
    fail = null
    await queue.flush()
    expect(batches.flatMap((b) => b.events.map((e) => e.document_id))).toEqual([1])
  })

  it('drops a batch the server rejected', async () => {
    fail = new ApiError(422, { detail: 'unknown documents: [1]' })
    queue.impression(seen(1))
    await queue.flush()
    fail = null
    await queue.flush()
    expect(batches).toHaveLength(0)
  })
})
