/** Share of a card that must be on screen for it to count as seen. */
export const IMPRESSION_VISIBLE_RATIO = 0.5
/** How long it must stay that visible, so scrolling past doesn't count. */
export const IMPRESSION_MIN_VISIBLE_MS = 1000

interface Watched {
  onSeen: () => void
  timer: ReturnType<typeof setTimeout> | null
}

const watched = new Map<Element, Watched>()
let observer: IntersectionObserver | null = null

function observerFor(): IntersectionObserver {
  observer ??= new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const item = watched.get(entry.target)
        if (!item) continue
        if (entry.isIntersecting && entry.intersectionRatio >= IMPRESSION_VISIBLE_RATIO) {
          item.timer ??= setTimeout(() => {
            unwatch(entry.target)
            item.onSeen()
          }, IMPRESSION_MIN_VISIBLE_MS)
        } else if (item.timer !== null) {
          clearTimeout(item.timer)
          item.timer = null
        }
      }
    },
    { threshold: IMPRESSION_VISIBLE_RATIO },
  )
  return observer
}

function unwatch(element: Element) {
  const item = watched.get(element)
  if (item?.timer != null) clearTimeout(item.timer)
  watched.delete(element)
  observer?.unobserve(element)
}

/**
 * Calls `onSeen` once, after `element` has been at least half visible for a second.
 * Returns a function that stops watching (use it as a ref callback's cleanup).
 */
export function watchImpression(element: Element, onSeen: () => void): () => void {
  watched.set(element, { onSeen, timer: null })
  observerFor().observe(element)
  return () => {
    unwatch(element)
  }
}
