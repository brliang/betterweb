import { useHealth } from './api/generated/api'

export default function App() {
  const health = useHealth()

  const apiStatus = health.isPending
    ? 'checking…'
    : health.isError
      ? 'unreachable'
      : health.data.status

  return (
    <main className="mx-auto max-w-3xl px-4 py-8">
      <h1 className="text-2xl font-semibold tracking-tight">Discovery Engine</h1>
      <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">API: {apiStatus}</p>
    </main>
  )
}
