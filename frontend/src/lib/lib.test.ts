import { expect, it } from 'vitest'
import type { UserSettingsIn } from '../api/generated/api'
import { sameSettings } from './settings'
import { looksLikeSite } from './sites'
import { relatedTopicIds } from './topics'

it.each([
  ['example.com', true],
  ['https://www.example.com/blog/post', true],
  ['sub.example.co.uk', true],
  ['localhost', false],
  ['not a site', false],
  ['', false],
])('looksLikeSite(%j) is %s', (text, expected) => {
  expect(looksLikeSite(text)).toBe(expected)
})

it('compares settings regardless of order', () => {
  const a: UserSettingsIn = {
    interests: [
      { topic_id: 1, level: 'interested' },
      { topic_id: 2, level: 'very_interested' },
    ],
    content_types: ['article', 'paper'],
    exploration_pct: 0.2,
    preset: 'balanced',
    summaries_opt_in: false,
  }
  const b = {
    ...a,
    interests: [...a.interests].reverse(),
    content_types: ['paper', 'article'] as const,
  }
  expect(sameSettings(a, { ...b, content_types: [...b.content_types] })).toBe(true)
  expect(sameSettings(a, { ...a, interests: [{ topic_id: 1, level: 'very_interested' }] })).toBe(
    false,
  )
})

it('relates chosen topics to their parents and children', () => {
  const topic = (id: number, parent_id: number | null) => ({
    id,
    parent_id,
    name: String(id),
    external_id: String(id),
    tier: parent_id === null ? 1 : 2,
    description: null,
  })
  const topics = [topic(1, null), topic(2, 1), topic(3, 1), topic(4, null), topic(5, 4)]
  const related = relatedTopicIds(topics, [
    { topic_id: 1, level: 'interested' },
    { topic_id: 5, level: 'interested' },
  ])
  expect([...related].sort()).toEqual([1, 2, 3, 4, 5])
  expect(relatedTopicIds(topics, [{ topic_id: 2, level: 'interested' }])).toEqual(new Set([1, 2]))
})
