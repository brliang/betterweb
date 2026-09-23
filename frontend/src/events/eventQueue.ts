import { ApiError } from '../api/fetcher'
import { recordEvents, type EventIn } from '../api/generated/api'

/** Events per POST /events request (the server takes up to EVENTS_MAX_BATCH, 200). */
export const MAX_BATCH = 50
/** How long an event waits for others before its batch is sent. */
export const FLUSH_DELAY_MS = 5000
/** Events kept for a retry while the server is unreachable; older ones are dropped first. */
export const MAX_PENDING = 1000

export type SendEvents = (events: EventIn[], keepalive: boolean) => Promise<unknown>

/**
 * Batches impressions and clicks for POST /events (PLAN.md §9). An item's impression is
 * logged once per recommendation. A failed batch is retried with the next one unless the
 * server rejected it (4xx), which a retry would not fix.
 */
export class EventQueue {
  private readonly send: SendEvents
  private pending: EventIn[] = []
  private readonly seen = new Set<string>()
  private timer: ReturnType<typeof setTimeout> | null = null

  constructor(send: SendEvents) {
    this.send = send
  }

  impression(event: Omit<EventIn, 'kind'>) {
    const key = `${String(event.recommendation_id)}:${String(event.document_id)}`
    if (this.seen.has(key)) return
    this.seen.add(key)
    this.push({ ...event, kind: 'impression' })
  }

  /** A click is sent at once: the user may be leaving the page. */
  click(event: Omit<EventIn, 'kind'>) {
    this.push({ ...event, kind: 'click' })
    void this.flush({ keepalive: true })
  }

  /** Send everything pending; `keepalive` lets the request outlive the page. */
  async flush({ keepalive = false } = {}) {
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
    while (this.pending.length > 0) {
      const batch = this.pending.splice(0, MAX_BATCH)
      try {
        await this.send(batch, keepalive)
      } catch (error) {
        if (!(error instanceof ApiError && error.status < 500)) {
          this.pending = [...batch, ...this.pending].slice(-MAX_PENDING)
        }
        return
      }
    }
  }

  private push(event: EventIn) {
    this.pending.push(event)
    if (this.pending.length > MAX_PENDING) this.pending.shift()
    if (this.pending.length >= MAX_BATCH) void this.flush()
    else this.timer ??= setTimeout(() => void this.flush(), FLUSH_DELAY_MS)
  }
}

export const eventQueue = new EventQueue((events, keepalive) =>
  recordEvents({ events }, { keepalive }),
)
