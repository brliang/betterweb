import { DocumentType } from '../api/generated/api'
import { DOCUMENT_TYPES } from '../lib/labels'
import { choiceCard } from '../lib/styles'

const ORDER = Object.values(DocumentType)

export function ContentTypePicker({
  value,
  onChange,
}: {
  value: DocumentType[]
  onChange: (value: DocumentType[]) => void
}) {
  return (
    <fieldset>
      <legend className="sr-only">Content types</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {ORDER.map((type) => (
          <label key={type} className={choiceCard}>
            <input
              type="checkbox"
              className="mt-1 accent-teal-700"
              checked={value.includes(type)}
              onChange={(event) => {
                onChange(
                  event.target.checked
                    ? ORDER.filter((t) => t === type || value.includes(t))
                    : value.filter((t) => t !== type),
                )
              }}
            />
            <span>
              <span className="block text-sm font-medium">{DOCUMENT_TYPES[type].label}</span>
              <span className="block text-sm text-stone-600 dark:text-stone-400">
                {DOCUMENT_TYPES[type].description}
              </span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}
