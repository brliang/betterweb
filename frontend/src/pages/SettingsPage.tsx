import { useTaxonomy, useUserSettings } from '../api/generated/api'
import { ErrorNotice } from '../components/ErrorNotice'
import { Loading } from '../components/Loading'
import { PinsEditor } from '../components/PinsEditor'
import { PreferencesForm } from '../components/PreferencesForm'

export function SettingsPage() {
  const settings = useUserSettings()
  const taxonomy = useTaxonomy()

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
      <PinsEditor />
      {settings.isPending || taxonomy.isPending ? (
        <Loading />
      ) : settings.isError || taxonomy.isError ? (
        <ErrorNotice error={settings.error ?? taxonomy.error} title="Couldn't load your settings" />
      ) : (
        <PreferencesForm settings={settings.data} topics={taxonomy.data} />
      )}
    </div>
  )
}
