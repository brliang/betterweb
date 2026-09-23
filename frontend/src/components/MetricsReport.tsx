import type { Metrics } from '../api/generated/api'
import { formatDay, formatNumber, formatPercent, formatUsd } from '../lib/format'
import { SLICES, TYPE_BADGES } from '../lib/labels'
import { DailyChart } from './DailyChart'
import { Panel } from './Panel'
import { StatTile } from './StatTile'

const cell = 'py-1 pr-3 text-right tabular-nums'

function rate(value: number | null): string {
  return value === null ? '–' : formatPercent(value)
}

export function MetricsReport({ metrics }: { metrics: Metrics }) {
  const totals = metrics.daily.reduce(
    (sum, day) => ({
      discoveries: sum.discoveries + day.discoveries,
      clicks: sum.clicks + day.clicks,
      likes: sum.likes + day.likes,
    }),
    { discoveries: 0, clicks: 0, likes: 0 },
  )
  const days = `last ${String(metrics.days)} days`
  return (
    <div className="space-y-6">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile
          label={`Discoveries, ${days}`}
          value={formatNumber(totals.discoveries)}
          note="Clicks and likes off your pinned sites"
        />
        <StatTile label="Hide rate" value={rate(metrics.hide_rate)} note="Hides per impression" />
        <StatTile
          label="Documents"
          value={formatNumber(metrics.documents)}
          note={`${formatNumber(metrics.embedded)} embedded`}
        />
        <StatTile
          label="Model spend this month"
          value={formatUsd(metrics.month_spend_usd)}
          note={`of a ${formatUsd(metrics.spend_cap_usd)} cap`}
        />
      </dl>

      <Panel id="discoveries-heading" title="Discoveries per day">
        <p className="mb-3 text-sm text-stone-600 dark:text-stone-400">
          The success metric: clicks and likes on pages from sites you didn't pin.
        </p>
        <DailyChart daily={metrics.daily} />
        <details className="mt-3">
          <summary className="cursor-pointer text-sm font-medium focus-visible:outline-2 focus-visible:outline-teal-600">
            Daily numbers
          </summary>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-stone-500 dark:text-stone-400">
                <tr>
                  <th scope="col" className="py-1 pr-3 text-left font-medium">
                    Day
                  </th>
                  <th scope="col" className={cell}>
                    Shown
                  </th>
                  <th scope="col" className={cell}>
                    Clicks
                  </th>
                  <th scope="col" className={cell}>
                    Likes
                  </th>
                  <th scope="col" className={cell}>
                    Hides
                  </th>
                  <th scope="col" className={cell}>
                    Discoveries
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-200 dark:divide-stone-700">
                {[...metrics.daily].reverse().map((day) => (
                  <tr key={day.day}>
                    <th scope="row" className="py-1 pr-3 text-left font-normal">
                      {formatDay(day.day)}
                    </th>
                    <td className={cell}>{formatNumber(day.impressions)}</td>
                    <td className={cell}>{formatNumber(day.clicks)}</td>
                    <td className={cell}>{formatNumber(day.likes)}</td>
                    <td className={cell}>{formatNumber(day.hides)}</td>
                    <td className={`${cell} font-medium`}>{formatNumber(day.discoveries)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </Panel>

      <Panel id="slices-heading" title="Exploration vs. main feed">
        <table className="w-full text-sm">
          <thead className="text-xs text-stone-500 dark:text-stone-400">
            <tr>
              <th scope="col" className="py-1 pr-3 text-left font-medium">
                Slice
              </th>
              <th scope="col" className={cell}>
                Seen
              </th>
              <th scope="col" className={cell}>
                Clicked
              </th>
              <th scope="col" className={cell}>
                Click-through
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-stone-200 dark:divide-stone-700">
            {metrics.slices.map((slice) => (
              <tr key={slice.slice}>
                <th scope="row" className="py-1 pr-3 text-left font-normal">
                  {SLICES[slice.slice]}
                </th>
                <td className={cell}>{formatNumber(slice.impressions)}</td>
                <td className={cell}>{formatNumber(slice.clicks)}</td>
                <td className={cell}>{rate(slice.click_through)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <div className="grid gap-6 sm:grid-cols-2">
        <Panel id="corpus-heading" title="Corpus by type">
          <table className="w-full text-sm">
            <tbody className="divide-y divide-stone-200 dark:divide-stone-700">
              {metrics.corpus.map((row) => (
                <tr key={row.type}>
                  <th scope="row" className="py-1 pr-3 text-left font-normal">
                    {TYPE_BADGES[row.type]}
                  </th>
                  <td className={cell}>{formatNumber(row.documents)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel id="frontier-heading" title="Frontier">
          <dl className="grid grid-cols-[1fr_auto] gap-y-1 text-sm">
            <dt>Never fetched</dt>
            <dd className="tabular-nums">{formatNumber(metrics.frontier_new)}</dd>
            <dt>Waiting for a recrawl</dt>
            <dd className="tabular-nums">{formatNumber(metrics.frontier_recrawl)}</dd>
            <dt>Due now</dt>
            <dd className="tabular-nums">{formatNumber(metrics.frontier_due)}</dd>
          </dl>
        </Panel>
      </div>
    </div>
  )
}
