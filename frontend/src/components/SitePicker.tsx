import { useState } from 'react'
import type { SuggestedSourceOut } from '../api/generated/api'
import { looksLikeSite } from '../lib/sites'
import { choiceCard, quietButton, secondaryButton, textInput } from '../lib/styles'

/**
 * Pick sites to pin (PLAN.md §7 step 2): typed in, or from the suggested sources, with those
 * matching the chosen interests first.
 */
export function SitePicker({
  suggestions,
  relevantTopicIds,
  value,
  onChange,
}: {
  suggestions: SuggestedSourceOut[]
  relevantTopicIds: Set<number>
  value: string[]
  onChange: (value: string[]) => void
}) {
  const [draft, setDraft] = useState('')
  const [invalid, setInvalid] = useState(false)
  const suggested = new Set(suggestions.map((source) => source.url))
  const typed = value.filter((site) => !suggested.has(site))
  const relevant = (source: SuggestedSourceOut) =>
    source.topics.some((topic) => relevantTopicIds.has(topic.id))
  const ordered = [...suggestions.filter(relevant), ...suggestions.filter((s) => !relevant(s))]

  const add = () => {
    const site = draft.trim()
    if (!looksLikeSite(site)) {
      setInvalid(true)
      return
    }
    if (!value.includes(site)) onChange([...value, site])
    setDraft('')
    setInvalid(false)
  }

  return (
    <div className="space-y-6">
      <div>
        <label htmlFor="site-input" className="text-sm font-medium">
          Add a site you trust
        </label>
        <div className="mt-1 flex gap-2">
          <input
            id="site-input"
            value={draft}
            placeholder="example.com"
            aria-invalid={invalid}
            aria-describedby={invalid ? 'site-error' : undefined}
            onChange={(event) => {
              setDraft(event.target.value)
              setInvalid(false)
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                add()
              }
            }}
            className={textInput}
          />
          <button type="button" className={secondaryButton} onClick={add} disabled={!draft.trim()}>
            Add
          </button>
        </div>
        {invalid && (
          <p id="site-error" className="mt-1 text-sm text-red-700 dark:text-red-400">
            Enter a domain like example.com or a link to a page on the site.
          </p>
        )}
        {typed.length > 0 && (
          <ul aria-label="Sites you added" className="mt-2 flex flex-wrap gap-2">
            {typed.map((site) => (
              <li
                key={site}
                className="flex items-center gap-1 rounded-full bg-stone-200 py-0.5 pr-1 pl-3 text-sm dark:bg-stone-700"
              >
                {site}
                <button
                  type="button"
                  className={`${quietButton} px-1.5 py-0`}
                  aria-label={`Remove ${site}`}
                  onClick={() => {
                    onChange(value.filter((other) => other !== site))
                  }}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <fieldset>
        <legend className="text-sm font-medium">Or pick from suggested sources</legend>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {ordered.map((source) => (
            <label key={source.url} className={choiceCard}>
              <input
                type="checkbox"
                className="mt-1 accent-teal-700"
                checked={value.includes(source.url)}
                onChange={(event) => {
                  onChange(
                    event.target.checked
                      ? [...value, source.url]
                      : value.filter((site) => site !== source.url),
                  )
                }}
              />
              <span>
                <span className="block text-sm font-medium">{source.name}</span>
                <span className="block text-xs text-stone-500 dark:text-stone-400">
                  {source.host}
                </span>
                <span className="mt-0.5 block text-sm text-stone-600 dark:text-stone-400">
                  {source.description}
                </span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
    </div>
  )
}
