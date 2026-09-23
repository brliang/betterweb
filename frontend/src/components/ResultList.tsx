import { useState } from 'react'
import type { FeedPage, Surface } from '../api/generated/api'
import { secondaryButton } from '../lib/styles'
import { ErrorNotice } from './ErrorNotice'
import { ResultCard } from './ResultCard'

/** How far below the screen the next page starts loading. */
const PRELOAD_MARGIN = '800px'

/** The endless list of feed or search results, fetching the next page near its end. */
export function ResultList({
  pages,
  surface,
  hasNextPage,
  isFetchingNextPage,
  nextPageError,
  fetchNextPage,
}: {
  pages: FeedPage[]
  surface: Surface
  hasNextPage: boolean
  isFetchingNextPage: boolean
  nextPageError: unknown
  fetchNextPage: () => void
}) {
  // Blocked domains leave the list; the card that blocked one stays to say so.
  const [blockedBy, setBlockedBy] = useState(new Map<string, number>())
  const items = pages
    .flatMap((page) => page.items)
    .filter((item) => {
      const blocker = blockedBy.get(item.document.domain)
      return blocker === undefined || blocker === item.recommendation_id
    })
  const canLoad = hasNextPage && !isFetchingNextPage && !nextPageError

  const watchEnd = (element: HTMLElement | null) => {
    if (!element || !canLoad) return undefined
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) fetchNextPage()
      },
      { rootMargin: PRELOAD_MARGIN },
    )
    observer.observe(element)
    return () => {
      observer.disconnect()
    }
  }

  return (
    <div>
      <ol className="space-y-4">
        {items.map((item) => (
          <li key={item.recommendation_id}>
            <ResultCard
              item={item}
              surface={surface}
              onBlocked={(domain) => {
                setBlockedBy((blocked) => new Map(blocked).set(domain, item.recommendation_id))
              }}
            />
          </li>
        ))}
      </ol>
      <div ref={watchEnd} className="py-6 text-center text-sm text-stone-500 dark:text-stone-400">
        {nextPageError ? (
          <ErrorNotice error={nextPageError} title="Couldn't load more" onRetry={fetchNextPage} />
        ) : isFetchingNextPage ? (
          <p role="status">Loading more…</p>
        ) : hasNextPage ? (
          <button type="button" className={secondaryButton} onClick={fetchNextPage}>
            Load more
          </button>
        ) : (
          items.length > 0 && <p>That's everything for now.</p>
        )}
      </div>
    </div>
  )
}
