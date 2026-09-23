import type { InterestLevel } from '../api/generated/api'
import { INTEREST_LEVELS } from '../lib/labels'

const PICKED = 'has-checked:bg-teal-700 has-checked:text-white dark:has-checked:bg-teal-600'
// "No" is the default for every topic, so checked it stays quiet.
const UNPICKED =
  'has-checked:bg-stone-100 has-checked:text-stone-900 dark:has-checked:bg-stone-800 dark:has-checked:text-stone-100'

const OPTIONS: { value: InterestLevel | null; label: string; checked: string }[] = [
  { value: null, label: 'No', checked: UNPICKED },
  { value: 'interested', label: INTEREST_LEVELS.interested, checked: PICKED },
  { value: 'very_interested', label: INTEREST_LEVELS.very_interested, checked: PICKED },
]

/** A topic's name and a three-way choice: not picked, interested, very interested. */
export function TopicLevel({
  topicId,
  name,
  level,
  onChange,
  prominent = false,
}: {
  topicId: number
  name: string
  level: InterestLevel | null
  onChange: (level: InterestLevel | null) => void
  prominent?: boolean
}) {
  return (
    <fieldset className="flex flex-wrap items-center justify-between gap-2">
      <legend className="sr-only">{name}</legend>
      <span aria-hidden="true" className={prominent ? 'font-medium' : 'text-sm'}>
        {name}
      </span>
      <span className="inline-flex overflow-hidden rounded-md border border-stone-300 text-xs dark:border-stone-600">
        {OPTIONS.map((option) => (
          <label
            key={option.label}
            className={`cursor-pointer px-2.5 py-1 text-stone-600 not-first:border-l not-first:border-stone-300 hover:bg-stone-100 has-focus-visible:outline-2 has-focus-visible:-outline-offset-2 has-focus-visible:outline-teal-500 dark:text-stone-300 dark:not-first:border-stone-600 dark:hover:bg-stone-800 ${option.checked}`}
          >
            <input
              type="radio"
              className="sr-only"
              name={`topic-${String(topicId)}`}
              checked={level === option.value}
              onChange={() => {
                onChange(option.value)
              }}
            />
            {option.label}
          </label>
        ))}
      </span>
    </fieldset>
  )
}
