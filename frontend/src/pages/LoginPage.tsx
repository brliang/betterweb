import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router'
import { getMeQueryKey, useSignIn } from '../api/generated/api'
import { isStatus } from '../api/errors'
import { ErrorNotice } from '../components/ErrorNotice'
import { SignedOut } from '../components/SignedOut'
import { primaryButton } from '../lib/styles'

/**
 * Where a login link lands. Signing in takes a click rather than happening on load, so a link
 * previewer that opens the URL can't use up the one-time token.
 */
export function LoginPage() {
  const [params] = useSearchParams()
  const token = params.get('token')
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const signIn = useSignIn({
    mutation: {
      onSuccess: (me) => {
        queryClient.removeQueries()
        queryClient.setQueryData(getMeQueryKey(), me)
        void navigate(me.survey_completed ? '/' : '/survey', { replace: true })
      },
    },
  })

  if (!token) return <SignedOut />

  return (
    <main className="mx-auto max-w-lg px-4 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">Sign in to bribot</h1>
      <p className="mt-4 text-stone-700 dark:text-stone-300">
        This link signs this browser in. It works once.
      </p>
      <button
        type="button"
        className={`${primaryButton} mt-6`}
        disabled={signIn.isPending}
        onClick={() => {
          signIn.mutate({ data: { token } })
        }}
      >
        {signIn.isPending ? 'Signing in…' : 'Sign in'}
      </button>
      {signIn.isError && (
        <div className="mt-6">
          <ErrorNotice
            error={signIn.error}
            title={
              isStatus(signIn.error, 401)
                ? 'This link was already used or has expired. Make a new one.'
                : "Couldn't sign in"
            }
          />
        </div>
      )}
    </main>
  )
}
