import { useListSuggestedSources, useTaxonomy, useUserSettings } from '../api/generated/api'
import { ErrorNotice } from '../components/ErrorNotice'
import { Loading } from '../components/Loading'
import { SurveyForm } from '../components/SurveyForm'

/** The signup survey (PLAN.md §7), shown until it's done. */
export function SurveyPage() {
  const taxonomy = useTaxonomy()
  const sources = useListSuggestedSources()
  // Before the survey these are the defaults, and the exploration shares on offer.
  const settings = useUserSettings()

  if (taxonomy.isPending || sources.isPending || settings.isPending) return <Loading />
  if (taxonomy.isError || sources.isError || settings.isError) {
    return (
      <ErrorNotice
        error={taxonomy.error ?? sources.error ?? settings.error}
        title="Couldn't load the survey"
      />
    )
  }
  return <SurveyForm topics={taxonomy.data} suggestions={sources.data} defaults={settings.data} />
}
