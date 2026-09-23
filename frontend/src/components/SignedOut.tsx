export function SignedOut() {
  return (
    <main className="mx-auto max-w-lg px-4 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">bribot</h1>
      <p className="mt-4 text-stone-700 dark:text-stone-300">
        You're signed out. Sign in with a one-time link, made on the server with:
      </p>
      <pre className="mt-3 overflow-x-auto rounded-md bg-stone-100 p-3 text-sm dark:bg-stone-800">
        <code>python -m app.worker users login-link you@example.com</code>
      </pre>
      <p className="mt-3 text-sm text-stone-600 dark:text-stone-400">
        Open the link it prints in this browser. Each link works once.
      </p>
    </main>
  )
}
