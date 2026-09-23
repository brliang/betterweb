import { useSearchParams } from 'react-router'
import { useSearchInfinite } from '../api/generated/api'
import { isStatus } from '../api/errors'
import { ErrorNotice } from '../components/ErrorNotice'
import { Loading } from '../components/Loading'
import { ResultList } from '../components/ResultList'
import { SearchForm } from '../components/SearchForm'
import { pagedResults } from '../lib/paging'
import { useSummariesOn } from '../lib/summaries'

export function SearchPage() {
  const [params, setParams] = useSearchParams()
  const query = params.get('q')?.trim() ?? ''
  const summaries = useSummariesOn()
  const results = useSearchInfinite(
    { q: query },
    { query: { ...pagedResults, enabled: query !== '' } },
  )

  return (
    <section aria-labelledby="search-heading">
      <h1 id="search-heading" className="mb-4 text-xl font-semibold tracking-tight">
        Search
      </h1>
      <SearchForm
        key={query}
        initial={query}
        onSearch={(q) => {
          setParams({ q })
        }}
      />
      {query === '' ? (
        <p className="text-sm text-stone-600 dark:text-stone-400">
          Results are ranked by how well they match your search, then by your sources, interests and
          likes. Only what bribot has crawled is searched.
        </p>
      ) : results.isPending ? (
        <Loading label="Searching…" />
      ) : results.isError && !results.isFetchNextPageError ? (
        <ErrorNotice
          error={results.error}
          title={
            isStatus(results.error, 503) ? "Search isn't available right now" : "Couldn't search"
          }
          onRetry={() => void results.refetch()}
        />
      ) : results.data.pages[0]?.items.length === 0 ? (
        <p className="text-sm text-stone-600 dark:text-stone-400">Nothing matches “{query}” yet.</p>
      ) : (
        <ResultList
          key={query}
          pages={results.data.pages}
          surface="search"
          summaries={summaries}
          hasNextPage={results.hasNextPage}
          isFetchingNextPage={results.isFetchingNextPage}
          nextPageError={results.isFetchNextPageError ? results.error : null}
          fetchNextPage={() => void results.fetchNextPage()}
        />
      )}
    </section>
  )
}
