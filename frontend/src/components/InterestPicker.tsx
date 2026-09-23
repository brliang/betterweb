import { useState } from 'react'
import type { InterestChoice, InterestLevel, TopicOut } from '../api/generated/api'
import { quietButton, textInput } from '../lib/styles'
import { TopicLevel } from './TopicLevel'

/**
 * Pick interests from the taxonomy (PLAN.md §7 step 1): tier-1 topics, each expandable to its
 * tier-2 topics. A search box finds a topic at either tier.
 */
export function InterestPicker({
  topics,
  value,
  onChange,
}: {
  topics: TopicOut[]
  value: InterestChoice[]
  onChange: (value: InterestChoice[]) => void
}) {
  const [expanded, setExpanded] = useState(new Set<number>())
  const [filter, setFilter] = useState('')
  const levels = new Map(value.map((choice) => [choice.topic_id, choice.level]))
  const needle = filter.trim().toLowerCase()
  const matches = (topic: TopicOut) => topic.name.toLowerCase().includes(needle)

  const tops = topics.filter((topic) => topic.tier === 1)
  const childrenOf = new Map<number, TopicOut[]>()
  for (const topic of topics) {
    if (topic.tier === 2 && topic.parent_id !== null) {
      childrenOf.set(topic.parent_id, [...(childrenOf.get(topic.parent_id) ?? []), topic])
    }
  }

  const setLevel = (topicId: number, level: InterestLevel | null) => {
    const rest = value.filter((choice) => choice.topic_id !== topicId)
    onChange(level === null ? rest : [...rest, { topic_id: topicId, level }])
  }
  const toggle = (topicId: number) => {
    const next = new Set(expanded)
    if (!next.delete(topicId)) next.add(topicId)
    setExpanded(next)
  }

  const shown = tops.flatMap((top) => {
    const children = childrenOf.get(top.id) ?? []
    if (!needle)
      return [{ top, children: expanded.has(top.id) ? children : [], total: children.length }]
    const matching = children.filter(matches)
    if (!matches(top) && matching.length === 0) return []
    return [{ top, children: matching, total: children.length }]
  })

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="min-w-48 grow">
          <label htmlFor="topic-filter" className="text-sm font-medium">
            Find a topic
          </label>
          <input
            id="topic-filter"
            type="search"
            value={filter}
            onChange={(event) => {
              setFilter(event.target.value)
            }}
            onKeyDown={(event) => {
              // Filtering, not submitting the form around it.
              if (event.key === 'Enter') event.preventDefault()
            }}
            className={`${textInput} mt-1`}
          />
        </div>
        <p className="text-sm text-stone-600 dark:text-stone-400" aria-live="polite">
          {value.length === 1 ? '1 topic picked' : `${String(value.length)} topics picked`}
        </p>
      </div>
      <ul className="mt-3 divide-y divide-stone-200 rounded-lg border border-stone-200 bg-white dark:divide-stone-700 dark:border-stone-700 dark:bg-stone-900">
        {shown.map(({ top, children, total }) => (
          <li key={top.id} className="px-3 py-2">
            <TopicLevel
              topicId={top.id}
              name={top.name}
              level={levels.get(top.id) ?? null}
              onChange={(level) => {
                setLevel(top.id, level)
              }}
              prominent
            />
            {total > 0 && !needle && (
              <button
                type="button"
                className={`${quietButton} -ml-2 text-xs`}
                aria-expanded={expanded.has(top.id)}
                onClick={() => {
                  toggle(top.id)
                }}
              >
                {expanded.has(top.id) ? '▾ Hide subtopics' : `▸ ${String(total)} subtopics`}
              </button>
            )}
            {children.length > 0 && (
              <ul className="mt-1 ml-4 space-y-1.5 border-l border-stone-200 pl-3 dark:border-stone-700">
                {children.map((child) => (
                  <li key={child.id}>
                    <TopicLevel
                      topicId={child.id}
                      name={child.name}
                      level={levels.get(child.id) ?? null}
                      onChange={(level) => {
                        setLevel(child.id, level)
                      }}
                    />
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
        {shown.length === 0 && (
          <li className="px-3 py-4 text-sm text-stone-500">No topic matches “{filter}”.</li>
        )}
      </ul>
    </div>
  )
}
