import type { SummaryOut } from '../api/generated/api'
import { isStatus } from '../api/errors'
import { ErrorNotice } from './ErrorNotice'
import { Loading } from './Loading'

/** An opt-in AI-written "Why might I like this?" note (PLAN.md §6.8). */
export function SummaryPanel({
  id,
  summary,
  error,
  onRetry,
}: {
  id: string
  summary: SummaryOut | undefined
  error: unknown
  onRetry: () => void
}) {
  return (
    <section
      id={id}
      aria-label="Why might I like this?"
      className="mt-3 rounded-md bg-violet-50 p-3 dark:bg-violet-950/40"
    >
      {summary ? (
        <>
          <p className="text-sm text-stone-800 dark:text-stone-200">{summary.text}</p>
          <p className="mt-2 text-xs text-stone-500 dark:text-stone-400">
            Written by AI ({summary.model}) from the page and your interests. It can be wrong.
          </p>
        </>
      ) : error ? (
        <ErrorNotice
          error={error}
          title={
            isStatus(error, 503)
              ? "Summaries aren't available right now"
              : "Couldn't write a summary"
          }
          onRetry={isStatus(error, 403) ? undefined : onRetry}
        />
      ) : (
        <Loading label="Writing a summary…" />
      )}
    </section>
  )
}
