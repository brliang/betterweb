import type { UserSettingsIn, UserSettingsOut } from '../api/generated/api'

export function settingsInput(settings: UserSettingsOut): UserSettingsIn {
  return {
    interests: settings.interests,
    content_types: settings.content_types,
    exploration_pct: settings.exploration_pct,
    preset: settings.preset ?? 'balanced',
    summaries_opt_in: settings.summaries_opt_in,
  }
}

/** Equal as settings: the order of interests and content types doesn't matter. */
export function sameSettings(a: UserSettingsIn, b: UserSettingsIn): boolean {
  const interests = (s: UserSettingsIn) =>
    s.interests
      .map((choice) => `${String(choice.topic_id)}:${choice.level}`)
      .sort()
      .join()
  const types = (s: UserSettingsIn) => [...s.content_types].sort().join()
  return (
    interests(a) === interests(b) &&
    types(a) === types(b) &&
    a.exploration_pct === b.exploration_pct &&
    a.preset === b.preset &&
    a.summaries_opt_in === b.summaries_opt_in
  )
}
