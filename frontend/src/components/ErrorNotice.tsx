import { errorMessage } from '../api/errors'

export function ErrorNotice({
  error,
  title = "That didn't work",
  onRetry,
}: {
  error: unknown
  title?: string
  onRetry?: () => void
}) {
  return (
    <div
      role="alert"
      className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
    >
      <p className="font-medium">{title}</p>
      <p className="mt-1">{errorMessage(error)}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 rounded-sm font-medium underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-red-600"
        >
          Try again
        </button>
      )}
    </div>
  )
}
