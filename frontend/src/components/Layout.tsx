import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Navigate, NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { isStatus } from '../api/errors'
import { useMe, useSignOut } from '../api/generated/api'
import { eventQueue } from '../events/eventQueue'
import { quietButton } from '../lib/styles'
import { ErrorNotice } from './ErrorNotice'
import { Loading } from './Loading'
import { SignedOut } from './SignedOut'

const navLink = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-2.5 py-1.5 text-sm font-medium focus-visible:outline-2 focus-visible:outline-teal-600 ${
    isActive
      ? 'bg-stone-200 text-stone-900 dark:bg-stone-700 dark:text-stone-50'
      : 'text-stone-600 hover:bg-stone-100 hover:text-stone-900 dark:text-stone-400 dark:hover:bg-stone-800 dark:hover:text-stone-100'
  }`

/** Signed-in pages: the session gate, the survey gate, and the header. */
export function Layout() {
  const me = useMe()
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const signOut = useSignOut({
    mutation: {
      onSuccess: () => {
        queryClient.removeQueries()
        void navigate('/')
      },
    },
  })

  // Send pending impressions before the tab is hidden or closed.
  useEffect(() => {
    const flush = () => {
      if (document.visibilityState === 'hidden') void eventQueue.flush({ keepalive: true })
    }
    document.addEventListener('visibilitychange', flush)
    window.addEventListener('pagehide', flush)
    return () => {
      document.removeEventListener('visibilitychange', flush)
      window.removeEventListener('pagehide', flush)
    }
  }, [])

  if (me.isPending) return <Loading />
  if (me.isError) {
    if (isStatus(me.error, 401)) return <SignedOut />
    return (
      <main className="mx-auto max-w-lg px-4 py-16">
        <ErrorNotice
          error={me.error}
          title="Couldn't reach bribot"
          onRetry={() => void me.refetch()}
        />
      </main>
    )
  }
  const surveying = location.pathname === '/survey'
  if (!me.data.survey_completed && !surveying) return <Navigate to="/survey" replace />
  if (me.data.survey_completed && surveying) return <Navigate to="/" replace />

  return (
    <div className="min-h-screen">
      <header className="border-b border-stone-200 bg-white/90 dark:border-stone-800 dark:bg-stone-950/90">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <span className="text-lg font-semibold tracking-tight">bribot</span>
          {me.data.survey_completed && (
            <nav aria-label="Main" className="flex flex-wrap gap-1">
              <NavLink to="/" end className={navLink}>
                Feed
              </NavLink>
              <NavLink to="/search" className={navLink}>
                Search
              </NavLink>
              <NavLink to="/settings" className={navLink}>
                Settings
              </NavLink>
              {me.data.is_admin && (
                <NavLink to="/admin" className={navLink}>
                  Admin
                </NavLink>
              )}
            </nav>
          )}
          <div className="ml-auto flex items-center gap-2 text-sm text-stone-500 dark:text-stone-400">
            <span className="hidden sm:inline">{me.data.email}</span>
            <button
              type="button"
              className={quietButton}
              disabled={signOut.isPending}
              onClick={() => {
                void eventQueue.flush({ keepalive: true })
                signOut.mutate()
              }}
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}
