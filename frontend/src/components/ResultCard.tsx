import { useState } from 'react'
import type { FeedItem, Surface } from '../api/generated/api'
import { useGiveFeedback } from '../api/generated/api'
import { eventQueue } from '../events/eventQueue'
import { watchImpression } from '../events/impressions'
import { formatDate } from '../lib/format'
import { SLICES, TYPE_BADGES } from '../lib/labels'
import { card, link, quietButton } from '../lib/styles'
import { BlockConfirm } from './BlockConfirm'
import { ErrorNotice } from './ErrorNotice'
import { LikeReasonPrompt } from './LikeReasonPrompt'
import { ReasonChips } from './ReasonChips'
import { WhyPanel } from './WhyPanel'

/** One feed or search result: the document, why it's here, and what you can do with it. */
export function ResultCard({
  item,
  surface,
  onBlocked,
}: {
  item: FeedItem
  surface: Surface
  onBlocked: (domain: string) => void
}) {
  const { document, recommendation_id, position } = item
  const [whyOpen, setWhyOpen] = useState(false)
  const [confirmingBlock, setConfirmingBlock] = useState(false)
  const [promptDone, setPromptDone] = useState(false)
  const like = useGiveFeedback()
  const hide = useGiveFeedback()
  const block = useGiveFeedback({
    mutation: {
      onSuccess: () => {
        onBlocked(document.domain)
      },
    },
  })
  const reference = { document_id: document.id, recommendation_id, position }
  const titleId = `result-${String(recommendation_id)}`
  const whyId = `why-${String(recommendation_id)}`
  const feedbackError = like.error ?? hide.error ?? block.error

  const trackImpression = (element: HTMLElement | null) =>
    element
      ? watchImpression(element, () => {
          eventQueue.impression({ ...reference, surface })
        })
      : undefined
  const trackClick = () => {
    eventQueue.click({ ...reference, surface })
  }

  if (hide.isSuccess || block.isSuccess) {
    return (
      <article
        className={`${card} text-sm text-stone-500 dark:text-stone-400`}
        aria-labelledby={titleId}
      >
        <p id={titleId}>
          {block.isSuccess
            ? `Blocked ${document.domain}. Its pages won't appear again.`
            : `Hidden. “${document.title ?? document.url}” won't appear again.`}
        </p>
      </article>
    )
  }

  return (
    <article ref={trackImpression} className={card} aria-labelledby={titleId}>
      {item.slice !== 'main' && (
        <p className="mb-1 text-xs font-medium tracking-wide text-violet-700 uppercase dark:text-violet-300">
          {SLICES[item.slice]}
        </p>
      )}
      <h2 id={titleId} className="text-lg leading-snug font-semibold">
        <a
          href={document.url}
          target="_blank"
          rel="noopener noreferrer"
          className={link}
          onClick={trackClick}
          onAuxClick={(event) => {
            if (event.button === 1) trackClick()
          }}
        >
          {document.title ?? document.url}
        </a>
      </h2>
      <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-stone-500 dark:text-stone-400">
        <span>{document.domain}</span>
        <span className="rounded-sm bg-stone-100 px-1.5 py-0.5 text-xs text-stone-700 dark:bg-stone-800 dark:text-stone-300">
          {TYPE_BADGES[document.type]}
        </span>
        {document.published_at && (
          <time dateTime={document.published_at}>{formatDate(document.published_at)}</time>
        )}
        {document.author && <span>by {document.author}</span>}
      </p>
      {document.excerpt && (
        <p className="mt-2 line-clamp-3 text-sm text-stone-700 dark:text-stone-300">
          {document.excerpt}
        </p>
      )}
      <div className="mt-3">
        <ReasonChips reasons={item.reasons} />
      </div>
      <div className="mt-2 -ml-2 flex flex-wrap gap-1">
        <button
          type="button"
          className={quietButton}
          aria-pressed={like.isSuccess}
          disabled={like.isPending || like.isSuccess}
          onClick={() => {
            like.mutate({ data: { ...reference, kind: 'like' } })
          }}
        >
          {like.isSuccess ? 'Liked' : 'Like'}
        </button>
        <button
          type="button"
          className={quietButton}
          disabled={hide.isPending}
          onClick={() => {
            hide.mutate({ data: { ...reference, kind: 'hide' } })
          }}
        >
          Hide
        </button>
        <button
          type="button"
          className={quietButton}
          aria-expanded={confirmingBlock}
          onClick={() => {
            setConfirmingBlock(!confirmingBlock)
          }}
        >
          Block site
        </button>
        <button
          type="button"
          className={quietButton}
          aria-expanded={whyOpen}
          aria-controls={whyOpen ? whyId : undefined}
          onClick={() => {
            setWhyOpen(!whyOpen)
          }}
        >
          Why this?
        </button>
      </div>
      {feedbackError && (
        <div className="mt-2">
          <ErrorNotice error={feedbackError} title="Couldn't save that" />
        </div>
      )}
      {confirmingBlock && (
        <BlockConfirm
          domain={document.domain}
          pending={block.isPending}
          onConfirm={() => {
            block.mutate({ data: { ...reference, kind: 'block_domain' } })
          }}
          onCancel={() => {
            setConfirmingBlock(false)
          }}
        />
      )}
      {like.data && !promptDone && (
        <LikeReasonPrompt
          feedbackId={like.data.id}
          onDone={() => {
            setPromptDone(true)
          }}
        />
      )}
      {whyOpen && <WhyPanel id={whyId} recommendationId={recommendation_id} />}
    </article>
  )
}
