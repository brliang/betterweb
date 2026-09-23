import { Link } from 'react-router'
import { link } from '../lib/styles'

export function NotFoundPage() {
  return (
    <section className="py-12 text-center">
      <h1 className="text-xl font-semibold">Nothing here</h1>
      <p className="mt-2 text-sm text-stone-600 dark:text-stone-400">
        <Link to="/" className={link}>
          Back to your feed
        </Link>
      </p>
    </section>
  )
}
