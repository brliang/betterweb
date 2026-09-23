import type { FeedItem, FeedPage, TopicOut, UserSettingsOut } from '../api/generated/api'

export function feedItem(id: number, domain = 'example.com'): FeedItem {
  return {
    recommendation_id: 100 + id,
    position: id,
    slice: 'main',
    score: 1.5,
    document: {
      id,
      url: `https://${domain}/${String(id)}`,
      title: `Document ${String(id)}`,
      domain,
      type: 'article',
      author: null,
      excerpt: 'An excerpt.',
      published_at: '2026-09-20T12:00:00Z',
    },
    reasons: [{ kind: 'sources', text: 'Linked from 2 sites you trust (e.g. a.org, b.org)' }],
  }
}

export function feedPage(items: FeedItem[], next_cursor: string | null = null): FeedPage {
  return { items, next_cursor }
}

export function topic(id: number, name: string, parent_id: number | null = null): TopicOut {
  return {
    id,
    name,
    parent_id,
    tier: parent_id === null ? 1 : 2,
    external_id: String(id),
    description: null,
  }
}

export const defaultSettings: UserSettingsOut = {
  interests: [],
  content_types: ['article', 'post', 'paper'],
  exploration_pct: 0.2,
  exploration_choices: [0.1, 0.2, 0.35],
  exploration_split_semantic: 0.5,
  preset: null,
  summaries_opt_in: false,
  weights: { interest: 1, ppr: 1, feedback: 0.5, recency: 0.5, hide: 1 },
}
