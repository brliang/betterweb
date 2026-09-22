import { QueryClient } from '@tanstack/react-query'
import { ApiError } from './fetcher'

const MAX_RETRIES = 3

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Client errors (4xx) won't succeed on retry; only retry network and server failures.
      retry: (failureCount, error) =>
        !(error instanceof ApiError && error.status < 500) && failureCount < MAX_RETRIES,
    },
  },
})
