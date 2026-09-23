import { useState } from 'react'
import { useSetFeedbackReason } from '../api/generated/api'
import { primaryButton, secondaryButton, textInput } from '../lib/styles'
import { ErrorNotice } from './ErrorNotice'

/** The optional "What did you like about it?" after a like (PLAN.md §9). Skipping is the default. */
export function LikeReasonPrompt({
  feedbackId,
  onDone,
}: {
  feedbackId: number
  onDone: () => void
}) {
  const [text, setText] = useState('')
  const save = useSetFeedbackReason({ mutation: { onSuccess: onDone } })
  const fieldId = `like-reason-${String(feedbackId)}`

  return (
    <form
      className="mt-3 rounded-md bg-stone-100 p-3 dark:bg-stone-800"
      onSubmit={(event) => {
        event.preventDefault()
        const reason = text.trim()
        if (reason) save.mutate({ feedbackId, data: { reason_text: reason } })
        else onDone()
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') onDone()
      }}
    >
      <label htmlFor={fieldId} className="text-sm font-medium">
        What did you like about it? <span className="font-normal text-stone-500">(optional)</span>
      </label>
      <textarea
        id={fieldId}
        rows={2}
        value={text}
        onChange={(event) => {
          setText(event.target.value)
        }}
        className={`${textInput} mt-1.5`}
      />
      <div className="mt-2 flex gap-2">
        <button type="submit" className={primaryButton} disabled={save.isPending || !text.trim()}>
          Save
        </button>
        <button type="button" className={secondaryButton} onClick={onDone}>
          Skip
        </button>
      </div>
      {save.isError && (
        <div className="mt-2">
          <ErrorNotice error={save.error} title="Couldn't save that" />
        </div>
      )}
    </form>
  )
}
