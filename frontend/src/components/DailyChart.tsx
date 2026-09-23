import type { DailyMetrics } from '../api/generated/api'
import { formatDay } from '../lib/format'

const WIDTH = 600
const HEIGHT = 120
const GAP = 2

/** Daily discoveries as bars; the table below the chart has the exact numbers. */
export function DailyChart({ daily }: { daily: DailyMetrics[] }) {
  const peak = Math.max(1, ...daily.map((day) => day.discoveries))
  const slot = WIDTH / Math.max(1, daily.length)
  const first = daily[0]
  const last = daily.at(-1)
  return (
    <figure>
      <svg
        viewBox={`0 0 ${String(WIDTH)} ${String(HEIGHT)}`}
        role="img"
        aria-label={`Discoveries per day, at most ${String(peak)}`}
        className="h-32 w-full"
        preserveAspectRatio="none"
      >
        <line
          x1={0}
          x2={WIDTH}
          y1={HEIGHT - 0.5}
          y2={HEIGHT - 0.5}
          className="stroke-stone-300 dark:stroke-stone-700"
        />
        {daily.map((day, index) => {
          const height = (day.discoveries / peak) * (HEIGHT - 4)
          return (
            <rect
              key={day.day}
              x={index * slot + GAP / 2}
              y={HEIGHT - height}
              width={Math.max(1, slot - GAP)}
              height={height}
              className="fill-teal-600 dark:fill-teal-400"
            >
              <title>{`${formatDay(day.day)}: ${String(day.discoveries)}`}</title>
            </rect>
          )
        })}
      </svg>
      {first && last && (
        <figcaption className="mt-1 flex justify-between text-xs text-stone-500 dark:text-stone-400">
          <span>{formatDay(first.day)}</span>
          <span>{formatDay(last.day)}</span>
        </figcaption>
      )}
    </figure>
  )
}
