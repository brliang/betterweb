import { useState } from 'react'
import { primaryButton, textInput } from '../lib/styles'

export function SearchForm({
  initial,
  onSearch,
}: {
  initial: string
  onSearch: (q: string) => void
}) {
  const [draft, setDraft] = useState(initial)
  return (
    <form
      role="search"
      className="mb-6 flex gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        const q = draft.trim()
        if (q) onSearch(q)
      }}
    >
      <label htmlFor="search-query" className="sr-only">
        Search what bribot has found
      </label>
      <input
        id="search-query"
        type="search"
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value)
        }}
        placeholder="Search what bribot has found"
        className={textInput}
      />
      <button type="submit" className={primaryButton} disabled={!draft.trim()}>
        Search
      </button>
    </form>
  )
}
