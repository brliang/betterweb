import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { IMPRESSION_MIN_VISIBLE_MS, IMPRESSION_VISIBLE_RATIO } from './impressions'

type Callback = (entries: Partial<IntersectionObserverEntry>[]) => void

let report: Callback = vi.fn()

beforeEach(() => {
  vi.useFakeTimers()
  vi.resetModules() // the observer is created once per module
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      constructor(callback: Callback) {
        report = callback
      }
      observe = vi.fn()
      unobserve = vi.fn()
      disconnect = vi.fn()
    },
  )
})
afterEach(() => {
  vi.useRealTimers()
})

async function watch() {
  const { watchImpression } = await import('./impressions')
  const element = document.createElement('article')
  const onSeen = vi.fn()
  const stop = watchImpression(element, onSeen)
  const visible = (ratio: number) => {
    report([{ target: element, isIntersecting: ratio > 0, intersectionRatio: ratio }])
  }
  return { onSeen, stop, visible }
}

it('counts a card seen after it stays visible long enough, once', async () => {
  const { onSeen, visible } = await watch()
  visible(IMPRESSION_VISIBLE_RATIO)
  vi.advanceTimersByTime(IMPRESSION_MIN_VISIBLE_MS - 1)
  expect(onSeen).not.toHaveBeenCalled()
  vi.advanceTimersByTime(1)
  expect(onSeen).toHaveBeenCalledOnce()
  visible(1)
  vi.advanceTimersByTime(IMPRESSION_MIN_VISIBLE_MS)
  expect(onSeen).toHaveBeenCalledOnce()
})

it('does not count a card scrolled past', async () => {
  const { onSeen, visible } = await watch()
  visible(1)
  vi.advanceTimersByTime(IMPRESSION_MIN_VISIBLE_MS / 2)
  visible(0)
  vi.advanceTimersByTime(IMPRESSION_MIN_VISIBLE_MS)
  expect(onSeen).not.toHaveBeenCalled()
})

it('does not count a card less than the visible ratio on screen', async () => {
  const { onSeen, visible } = await watch()
  visible(IMPRESSION_VISIBLE_RATIO / 2)
  vi.advanceTimersByTime(IMPRESSION_MIN_VISIBLE_MS * 2)
  expect(onSeen).not.toHaveBeenCalled()
})

it('stops counting once unwatched', async () => {
  const { onSeen, stop, visible } = await watch()
  visible(1)
  stop()
  vi.advanceTimersByTime(IMPRESSION_MIN_VISIBLE_MS)
  expect(onSeen).not.toHaveBeenCalled()
})
