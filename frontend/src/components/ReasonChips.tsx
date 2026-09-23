import type { Reason, ReasonKind } from '../api/generated/api'

const CHIP: Record<ReasonKind, string> = {
  exploration: 'bg-violet-100 text-violet-900 dark:bg-violet-950 dark:text-violet-200',
  sources: 'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200',
  interest: 'bg-teal-100 text-teal-900 dark:bg-teal-950 dark:text-teal-200',
  liked: 'bg-rose-100 text-rose-900 dark:bg-rose-950 dark:text-rose-200',
  recency: 'bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200',
  search: 'bg-stone-200 text-stone-800 dark:bg-stone-800 dark:text-stone-200',
}

export function ReasonChips({ reasons }: { reasons: Reason[] }) {
  if (reasons.length === 0) return null
  return (
    <ul aria-label="Why this is here" className="flex flex-wrap gap-1.5">
      {reasons.map((reason) => (
        <li key={reason.kind} className={`rounded-full px-2.5 py-0.5 text-xs ${CHIP[reason.kind]}`}>
          {reason.text}
        </li>
      ))}
    </ul>
  )
}
