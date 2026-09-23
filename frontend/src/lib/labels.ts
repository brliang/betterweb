import type {
  Component,
  DocumentType,
  InterestLevel,
  RankingPreset,
  Slice,
} from '../api/generated/api'

export const DOCUMENT_TYPES: Record<DocumentType, { label: string; description: string }> = {
  article: { label: 'Articles', description: 'News and magazine pieces' },
  post: { label: 'Posts', description: 'Blog posts and newsletters' },
  thread: { label: 'Threads', description: 'Forum and discussion threads' },
  paper: { label: 'Papers', description: 'Research papers and preprints' },
  pdf: { label: 'PDFs', description: 'Reports and other documents' },
  video: { label: 'Videos', description: 'Pages built around a video' },
  page: { label: 'Other pages', description: 'Homepages, listings and the rest' },
}

export const TYPE_BADGES: Record<DocumentType, string> = {
  article: 'Article',
  post: 'Post',
  thread: 'Thread',
  paper: 'Paper',
  pdf: 'PDF',
  video: 'Video',
  page: 'Page',
}

export const PRESETS: Record<RankingPreset, { label: string; description: string }> = {
  balanced: {
    label: 'Balanced',
    description: 'Weigh your sources, interests and freshness evenly.',
  },
  trust_my_sources: {
    label: 'Trust my sources',
    description: 'Favor what the sites you pinned link to.',
  },
  match_my_interests: {
    label: 'Match my interests',
    description: 'Favor what is closest to the topics you picked.',
  },
  fresh: { label: 'Fresh', description: 'Favor what was published recently.' },
}

export const INTEREST_LEVELS: Record<InterestLevel, string> = {
  interested: 'Interested',
  very_interested: 'Very interested',
}

export const SLICES: Record<Slice, string> = {
  main: 'For you',
  adjacent_semantic: 'Exploring nearby topics',
  adjacent_graph: 'Exploring nearby sites',
}

export const COMPONENTS: Record<Component, { label: string; description: string }> = {
  interest: { label: 'Interests', description: 'How close it is to the topics you picked' },
  ppr: { label: 'Your sources', description: 'How strongly the sites you pinned link to it' },
  feedback: { label: 'Your likes', description: 'Closer to what you liked than what you hid' },
  recency: { label: 'Freshness', description: 'Newer scores higher; papers and PDFs age slower' },
  hide: { label: 'Hide penalty', description: 'Very close to something you hid; subtracted' },
  query: { label: 'Search match', description: 'How close it is to your search' },
}
