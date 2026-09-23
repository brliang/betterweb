import type { CycleSummary } from '../api/generated/api'
import { formatDateTime, formatDuration, formatNumber, formatUsd } from '../lib/format'

const STATUS: Record<CycleSummary['status'], string> = {
  running: 'text-sky-700 dark:text-sky-300',
  succeeded: 'text-teal-700 dark:text-teal-300',
  failed: 'text-red-700 dark:text-red-400',
}

/** Crawl cycle history, newest first; each row opens to its per-stage stats. */
export function CycleTable({ cycles }: { cycles: CycleSummary[] }) {
  if (cycles.length === 0) {
    return <p className="text-sm text-stone-600 dark:text-stone-400">No crawl cycles yet.</p>
  }
  return (
    <ul className="divide-y divide-stone-200 dark:divide-stone-700">
      {cycles.map((cycle) => (
        <li key={cycle.id} className="py-2">
          <details>
            <summary className="flex cursor-pointer flex-wrap items-baseline gap-x-4 gap-y-1 text-sm focus-visible:outline-2 focus-visible:outline-teal-600">
              <span className="font-medium">#{cycle.id}</span>
              <span>{formatDateTime(cycle.started_at)}</span>
              <span className={STATUS[cycle.status]}>{cycle.status}</span>
              <span className="tabular-nums">
                {formatNumber(cycle.pages_fetched)} / {formatNumber(cycle.page_budget)} pages
              </span>
              <span className="tabular-nums">{formatUsd(cycle.spend_usd)}</span>
              {cycle.duration_s !== null && (
                <span className="tabular-nums">{formatDuration(cycle.duration_s)}</span>
              )}
            </summary>
            <pre className="mt-2 max-h-80 overflow-auto rounded-md bg-stone-100 p-3 text-xs dark:bg-stone-800">
              {JSON.stringify(cycle.stats, null, 2)}
            </pre>
          </details>
        </li>
      ))}
    </ul>
  )
}
