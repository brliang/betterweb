import type { InterestChoice, TopicOut } from '../api/generated/api'

/** The chosen topics with their parents and children: what a suggested source may match. */
export function relatedTopicIds(topics: TopicOut[], interests: InterestChoice[]): Set<number> {
  const chosen = new Set(interests.map((choice) => choice.topic_id))
  const related = new Set(chosen)
  for (const topic of topics) {
    if (chosen.has(topic.id) && topic.parent_id !== null) related.add(topic.parent_id)
    if (topic.parent_id !== null && chosen.has(topic.parent_id)) related.add(topic.id)
  }
  return related
}
