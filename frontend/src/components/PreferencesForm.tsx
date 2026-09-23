import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { TopicOut, UserSettingsIn, UserSettingsOut } from '../api/generated/api'
import {
  getFeedInfiniteQueryKey,
  getUserSettingsQueryKey,
  useUpdateUserSettings,
} from '../api/generated/api'
import { sameSettings, settingsInput } from '../lib/settings'
import { primaryButton } from '../lib/styles'
import { ContentTypePicker } from './ContentTypePicker'
import { ErrorNotice } from './ErrorNotice'
import { ExplorationPicker } from './ExplorationPicker'
import { InterestPicker } from './InterestPicker'
import { PresetPicker } from './PresetPicker'
import { Panel } from './Panel'

/** Interests, content types, exploration, ranking style and the summaries opt-in. */
export function PreferencesForm({
  settings,
  topics,
}: {
  settings: UserSettingsOut
  topics: TopicOut[]
}) {
  const saved = settingsInput(settings)
  const [draft, setDraft] = useState(saved)
  const queryClient = useQueryClient()
  const save = useUpdateUserSettings({
    mutation: {
      onSuccess: (updated) => {
        queryClient.setQueryData(getUserSettingsQueryKey(), updated)
        setDraft(settingsInput(updated))
        queryClient.removeQueries({ queryKey: getFeedInfiniteQueryKey() })
      },
    },
  })
  const dirty = !sameSettings(draft, saved)
  const update = (change: Partial<UserSettingsIn>) => {
    setDraft({ ...draft, ...change })
  }

  return (
    <form
      className="space-y-6"
      onSubmit={(event) => {
        event.preventDefault()
        save.mutate({ data: draft })
      }}
    >
      <Panel id="interests-heading" title="Interests">
        <InterestPicker
          topics={topics}
          value={draft.interests}
          onChange={(interests) => {
            update({ interests })
          }}
        />
      </Panel>
      <Panel id="types-heading" title="Kinds of pages">
        <ContentTypePicker
          value={draft.content_types}
          onChange={(content_types) => {
            update({ content_types })
          }}
        />
      </Panel>
      <Panel id="exploration-heading" title="Exploration">
        <ExplorationPicker
          choices={settings.exploration_choices}
          value={draft.exploration_pct}
          onChange={(exploration_pct) => {
            update({ exploration_pct })
          }}
        />
      </Panel>
      <Panel id="preset-heading" title="Ranking style">
        <PresetPicker
          value={draft.preset}
          onChange={(preset) => {
            update({ preset })
          }}
        />
      </Panel>
      <Panel id="summaries-heading" title="“Why might I like this?” summaries">
        <label className="flex gap-3">
          <input
            type="checkbox"
            className="mt-1 accent-teal-700"
            checked={draft.summaries_opt_in}
            onChange={(event) => {
              update({ summaries_opt_in: event.target.checked })
            }}
          />
          <span className="text-sm">
            <span className="block font-medium">
              Offer short AI summaries of why you might like an item
            </span>
            <span className="mt-1 block text-stone-600 dark:text-stone-400">
              Off unless you turn it on. A summary is written only when you ask for one: the page's
              title and text, its topics and the names of your interests are sent to a model
              provider through OpenRouter. Nothing that identifies you or your account is sent.
            </span>
          </span>
        </label>
      </Panel>
      <div className="sticky bottom-0 flex items-center gap-3 border-t border-stone-200 bg-stone-50 py-4 dark:border-stone-800 dark:bg-stone-950">
        <button
          type="submit"
          className={primaryButton}
          disabled={!dirty || save.isPending || draft.content_types.length === 0}
        >
          {save.isPending ? 'Saving…' : 'Save changes'}
        </button>
        <p role="status" className="text-sm text-stone-600 dark:text-stone-400">
          {draft.content_types.length === 0
            ? 'Pick at least one kind of page.'
            : dirty
              ? 'Unsaved changes'
              : save.isSuccess
                ? 'Saved. Your feed starts over with the new settings.'
                : ''}
        </p>
      </div>
      {save.isError && <ErrorNotice error={save.error} title="Couldn't save your settings" />}
    </form>
  )
}
