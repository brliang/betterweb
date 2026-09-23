import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  getFeedInfiniteQueryKey,
  getListPinsQueryKey,
  useAddPin,
  useListPins,
  useRemovePin,
} from '../api/generated/api'
import { formatDate } from '../lib/format'
import { looksLikeSite } from '../lib/sites'
import { card, quietButton, secondaryButton, textInput } from '../lib/styles'
import { ErrorNotice } from './ErrorNotice'
import { Loading } from './Loading'

/** The sites you trust: bribot crawls them nightly and ranks what they link to higher. */
export function PinsEditor() {
  const queryClient = useQueryClient()
  const pins = useListPins()
  const [draft, setDraft] = useState('')
  const [invalid, setInvalid] = useState(false)
  const changed = () => {
    void queryClient.invalidateQueries({ queryKey: getListPinsQueryKey() })
    // The next visit to the feed starts a new, re-ranked session.
    queryClient.removeQueries({ queryKey: getFeedInfiniteQueryKey() })
  }
  const add = useAddPin({
    mutation: {
      onSuccess: () => {
        setDraft('')
        changed()
      },
    },
  })
  const remove = useRemovePin({ mutation: { onSuccess: changed } })

  return (
    <section aria-labelledby="pins-heading" className={card}>
      <h2 id="pins-heading" className="text-lg font-semibold">
        Pinned sites
      </h2>
      <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">
        bribot crawls these every night and follows their links. What they link to ranks higher. A
        new site's pages show up after the next crawl.
      </p>
      <form
        className="mt-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          if (!looksLikeSite(draft)) {
            setInvalid(true)
            return
          }
          add.mutate({ data: { site: draft.trim() } })
        }}
      >
        <label htmlFor="pin-input" className="sr-only">
          Site to pin
        </label>
        <input
          id="pin-input"
          value={draft}
          placeholder="example.com"
          aria-invalid={invalid}
          aria-describedby={invalid ? 'pin-error' : undefined}
          onChange={(event) => {
            setDraft(event.target.value)
            setInvalid(false)
          }}
          className={textInput}
        />
        <button type="submit" className={secondaryButton} disabled={!draft.trim() || add.isPending}>
          Pin
        </button>
      </form>
      {invalid && (
        <p id="pin-error" className="mt-1 text-sm text-red-700 dark:text-red-400">
          Enter a domain like example.com or a link to a page on the site.
        </p>
      )}
      {add.isError && (
        <div className="mt-2">
          <ErrorNotice error={add.error} title="Couldn't pin that site" />
        </div>
      )}
      {remove.isError && (
        <div className="mt-2">
          <ErrorNotice error={remove.error} title="Couldn't unpin that site" />
        </div>
      )}
      {pins.isPending ? (
        <Loading />
      ) : pins.isError ? (
        <div className="mt-4">
          <ErrorNotice
            error={pins.error}
            title="Couldn't load your pins"
            onRetry={() => void pins.refetch()}
          />
        </div>
      ) : pins.data.length === 0 ? (
        <p className="mt-4 text-sm text-stone-600 dark:text-stone-400">
          No pinned sites yet. Your feed needs at least one to start from.
        </p>
      ) : (
        <ul className="mt-4 divide-y divide-stone-200 dark:divide-stone-700">
          {pins.data.map((pin) => (
            <li key={pin.domain_id} className="flex items-center justify-between gap-3 py-2">
              <div>
                <p className="text-sm font-medium">{pin.name ?? pin.host}</p>
                <p className="text-xs text-stone-500 dark:text-stone-400">
                  {pin.name ? `${pin.host} · ` : ''}pinned {formatDate(pin.created_at)}
                </p>
              </div>
              <button
                type="button"
                className={quietButton}
                aria-label={`Unpin ${pin.host}`}
                disabled={remove.isPending}
                onClick={() => {
                  remove.mutate({ domainId: pin.domain_id })
                }}
              >
                Unpin
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
