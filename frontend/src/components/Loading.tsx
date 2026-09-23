export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <p role="status" className="py-8 text-center text-sm text-stone-500 dark:text-stone-400">
      {label}
    </p>
  )
}
