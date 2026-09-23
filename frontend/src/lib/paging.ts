import type { FeedPage } from '../api/generated/api'

/**
 * Query options for the feed and search. Each page is ranked when it is fetched and stored as
 * served, so a page is never refetched behind the user's back (on focus, reconnect or remount):
 * that would rank a new page and log it as shown. Starting over is an explicit reset.
 */
export const pagedResults = {
  initialPageParam: undefined,
  getNextPageParam: (last: FeedPage) => last.next_cursor ?? undefined,
  staleTime: Infinity,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
}
