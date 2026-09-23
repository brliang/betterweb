import { useCycles, useMe, useMetrics } from '../api/generated/api'
import { CycleTable } from '../components/CycleTable'
import { ErrorNotice } from '../components/ErrorNotice'
import { Loading } from '../components/Loading'
import { MetricsReport } from '../components/MetricsReport'
import { Panel } from '../components/Panel'

export function AdminPage() {
  const me = useMe()
  const isAdmin = me.data?.is_admin ?? false
  const metrics = useMetrics({ query: { enabled: isAdmin } })
  const cycles = useCycles({ query: { enabled: isAdmin } })

  if (!isAdmin) {
    return <p className="text-sm text-stone-600 dark:text-stone-400">This page is for admins.</p>
  }
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold tracking-tight">Admin</h1>
      {metrics.isPending ? (
        <Loading />
      ) : metrics.isError ? (
        <ErrorNotice
          error={metrics.error}
          title="Couldn't load metrics"
          onRetry={() => void metrics.refetch()}
        />
      ) : (
        <MetricsReport metrics={metrics.data} />
      )}
      <Panel id="cycles-heading" title="Crawl cycles">
        {cycles.isPending ? (
          <Loading />
        ) : cycles.isError ? (
          <ErrorNotice
            error={cycles.error}
            title="Couldn't load cycles"
            onRetry={() => void cycles.refetch()}
          />
        ) : (
          <CycleTable cycles={cycles.data} />
        )}
      </Panel>
    </div>
  )
}
