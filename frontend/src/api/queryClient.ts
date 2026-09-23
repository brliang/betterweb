import {
  hashKey,
  MutationCache,
  QueryCache,
  QueryClient,
  type QueryKey,
} from '@tanstack/react-query'
import { ApiError } from './fetcher'
import { getMeQueryKey } from './generated/api'

const MAX_RETRIES = 3

/** A 401 anywhere means the session ended: refetch /me so the app shows the signed-out page. */
function onError(error: unknown) {
  if (error instanceof ApiError && error.status === 401) {
    void queryClient.invalidateQueries({ queryKey: getMeQueryKey(), refetchType: 'all' })
  }
}

function isMe(queryKey: QueryKey): boolean {
  return hashKey(queryKey) === hashKey(getMeQueryKey())
}

export const queryClient: QueryClient = new QueryClient({
  queryCache: new QueryCache({
    // /me itself answering 401 is how the app learns it; refetching it again would loop.
    onError: (error, query) => {
      if (!isMe(query.queryKey)) onError(error)
    },
  }),
  mutationCache: new MutationCache({ onError }),
  defaultOptions: {
    queries: {
      // Client errors (4xx) won't succeed on retry, and neither will a 503: the API sends it
      // when a feature is switched off or the month's model spend cap is reached. Retry
      // network and other server failures.
      retry: (failureCount, error) =>
        !(error instanceof ApiError && (error.status < 500 || error.status === 503)) &&
        failureCount < MAX_RETRIES,
    },
  },
})
