import { useHealth } from './api/generated/api'

export default function App() {
  const health = useHealth()

  return (
    <main>
      <h1>Discovery Engine</h1>
      <p>
        API:{' '}
        {health.isPending ? 'checking…' : health.isError ? 'unreachable' : health.data.data.status}
      </p>
    </main>
  )
}
