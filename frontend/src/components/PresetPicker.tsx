import { RankingPreset } from '../api/generated/api'
import { PRESETS } from '../lib/labels'
import { choiceCard } from '../lib/styles'

export function PresetPicker({
  value,
  onChange,
}: {
  value: RankingPreset
  onChange: (value: RankingPreset) => void
}) {
  return (
    <fieldset>
      <legend className="sr-only">Ranking style</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {Object.values(RankingPreset).map((preset) => (
          <label key={preset} className={choiceCard}>
            <input
              type="radio"
              name="preset"
              className="mt-1 accent-teal-700"
              checked={value === preset}
              onChange={() => {
                onChange(preset)
              }}
            />
            <span>
              <span className="block text-sm font-medium">{PRESETS[preset].label}</span>
              <span className="block text-sm text-stone-600 dark:text-stone-400">
                {PRESETS[preset].description}
              </span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}
