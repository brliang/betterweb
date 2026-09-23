import { primaryButton, secondaryButton } from '../lib/styles'

export function BlockConfirm({
  domain,
  pending,
  onConfirm,
  onCancel,
}: {
  domain: string
  pending: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div
      role="group"
      aria-label={`Block ${domain}`}
      className="mt-3 rounded-md bg-stone-100 p-3 text-sm dark:bg-stone-800"
    >
      <p>
        Block <strong>{domain}</strong>? Its pages leave your feed and search results. This can't be
        undone yet.
      </p>
      <div className="mt-2 flex gap-2">
        <button type="button" className={primaryButton} disabled={pending} onClick={onConfirm}>
          Block {domain}
        </button>
        <button type="button" className={secondaryButton} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}
