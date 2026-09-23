import { useWhy } from '../api/generated/api'
import { ErrorNotice } from './ErrorNotice'
import { Loading } from './Loading'
import { WhyBreakdown } from './WhyBreakdown'

/** The full "why this?" breakdown (PLAN.md §6.7), loaded when opened. */
export function WhyPanel({ recommendationId, id }: { recommendationId: number; id: string }) {
  // Each fetch logs a why_open event, so don't refetch on focus.
  const why = useWhy(recommendationId, {
    query: { staleTime: Infinity, refetchOnWindowFocus: false },
  })
  return (
    <section
      id={id}
      aria-label="Why this?"
      className="mt-3 rounded-md bg-stone-100 p-3 dark:bg-stone-800"
    >
      {why.isPending ? (
        <Loading />
      ) : why.isError ? (
        <ErrorNotice
          error={why.error}
          title="Couldn't load the breakdown"
          onRetry={() => void why.refetch()}
        />
      ) : (
        <WhyBreakdown why={why.data} />
      )}
    </section>
  )
}
