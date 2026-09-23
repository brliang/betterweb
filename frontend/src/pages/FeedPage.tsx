import { useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { getFeedInfiniteQueryKey, useFeedInfinite } from '../api/generated/api'
import { ErrorNotice } from '../components/ErrorNotice'
import { Loading } from '../components/Loading'
import { ResultList } from '../components/ResultList'
import { pagedResults } from '../lib/paging'
import { link, secondaryButton } from '../lib/styles'

export function FeedPage() {
  const queryClient = useQueryClient()
  const feed = useFeedInfinite(undefined, { query: pagedResults })
  const startOver = () => {
    window.scrollTo({ top: 0 })
    void queryClient.resetQueries({ queryKey: getFeedInfiniteQueryKey() })
  }

  return (
    <section aria-labelledby="feed-heading">
      <div className="mb-4 flex items-center justify-between gap-4">
        <h1 id="feed-heading" className="text-xl font-semibold tracking-tight">
          Your feed
        </h1>
        <button
          type="button"
          className={secondaryButton}
          onClick={startOver}
          disabled={feed.isFetching}
        >
          Start over
        </button>
      </div>
      {feed.isPending ? (
        <Loading label="Ranking your feed…" />
      ) : feed.isError && !feed.isFetchNextPageError ? (
        <ErrorNotice error={feed.error} title="Couldn't load your feed" onRetry={startOver} />
      ) : feed.data.pages[0]?.items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-stone-300 p-6 text-sm text-stone-600 dark:border-stone-700 dark:text-stone-400">
          <p className="font-medium text-stone-800 dark:text-stone-200">Nothing to show yet.</p>
          <p className="mt-2">
            bribot fills your feed from its nightly crawl of the sites you pinned and the pages they
            link to. Check back after the next run, or{' '}
            <Link to="/settings" className={link}>
              pin more sites
            </Link>
            .
          </p>
        </div>
      ) : (
        <ResultList
          pages={feed.data.pages}
          surface="feed"
          hasNextPage={feed.hasNextPage}
          isFetchingNextPage={feed.isFetchingNextPage}
          nextPageError={feed.isFetchNextPageError ? feed.error : null}
          fetchNextPage={() => void feed.fetchNextPage()}
        />
      )}
    </section>
  )
}
