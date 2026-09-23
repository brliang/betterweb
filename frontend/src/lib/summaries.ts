import { useUserSettings } from '../api/generated/api'

/**
 * Whether to offer "Why might I like this?": only once the settings have loaded and say the
 * user opted in. While they load, or if they fail, the results show without it.
 */
export function useSummariesOn(): boolean {
  const settings = useUserSettings()
  return settings.isSuccess && settings.data.summaries_opt_in
}
