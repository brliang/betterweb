import { formatPercent } from '../lib/format'
import { choiceCard } from '../lib/styles'

export function ExplorationPicker({
  choices,
  value,
  onChange,
}: {
  choices: number[]
  value: number
  onChange: (value: number) => void
}) {
  return (
    <fieldset>
      <legend className="sr-only">Exploration share</legend>
      <div className="grid gap-2 sm:grid-cols-3">
        {choices.map((share) => (
          <label key={share} className={choiceCard}>
            <input
              type="radio"
              name="exploration"
              className="mt-1 accent-teal-700"
              checked={value === share}
              onChange={() => {
                onChange(share)
              }}
            />
            <span>
              <span className="block text-sm font-medium">{formatPercent(share)}</span>
              <span className="block text-sm text-stone-600 dark:text-stone-400">
                of each feed page shows nearby topics and sites
              </span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}
