import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(() => {
  cleanup()
})

/** jsdom has no IntersectionObserver; tests that need a working one stub their own. */
class InertIntersectionObserver implements IntersectionObserver {
  readonly root = null
  readonly rootMargin = ''
  readonly scrollMargin = ''
  readonly thresholds: readonly number[] = []
  observe = vi.fn()
  unobserve = vi.fn()
  disconnect = vi.fn()
  takeRecords() {
    return []
  }
}
globalThis.IntersectionObserver = InertIntersectionObserver

// jsdom doesn't implement scrolling.
window.scrollTo = vi.fn()
