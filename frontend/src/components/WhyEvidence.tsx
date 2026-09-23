import type { Evidence } from '../api/generated/api'
import { formatScore } from '../lib/format'
import { SLICES } from '../lib/labels'

function ageText(days: number): string {
  if (days < 1) return 'Less than a day ago'
  const whole = Math.floor(days)
  return whole === 1 ? '1 day ago' : `${String(whole)} days ago`
}

function more(shown: number, total: number): string {
  return total > shown ? ` and ${String(total - shown)} more` : ''
}

/** The evidence behind a score, as a definition list. */
export function WhyEvidence({ evidence }: { evidence: Evidence }) {
  const trusted = evidence.trusted_linkers ?? []
  const others = evidence.other_linkers ?? []
  const interests = evidence.interest_topics ?? []
  const exploration = evidence.exploration
  const rows: [string, string][] = []
  if (evidence.pinned_domain) rows.push(['Site', `${evidence.pinned_domain} (you pinned it)`])
  if (trusted.length > 0) {
    rows.push([
      'Linked from sites you trust',
      trusted.join(', ') + more(trusted.length, evidence.trusted_linker_count ?? 0),
    ])
  }
  if (others.length > 0) {
    rows.push([
      'Linked from other sites',
      others.join(', ') + more(others.length, evidence.other_linker_count ?? 0),
    ])
  }
  if (interests.length > 0) {
    rows.push(['Your interests it matches', interests.map((topic) => topic.name).join(', ')])
  }
  if (evidence.nearest_liked) {
    const liked = evidence.nearest_liked
    rows.push([
      'Closest thing you liked',
      `${liked.title ?? `Document ${String(liked.document_id)}`} (similarity ${formatScore(liked.similarity)})`,
    ])
  }
  rows.push([evidence.published ? 'Published' : 'First found', ageText(evidence.age_days)])
  if (exploration) {
    rows.push(['Exploration', SLICES[exploration.slice]])
    if (exploration.adjacent_topic && exploration.interest_topic) {
      rows.push([
        'Nearby topic',
        `${exploration.adjacent_topic.name}, next to your interest ${exploration.interest_topic.name}`,
      ])
    }
    if (exploration.outside_topic) rows.push(['Its topic', exploration.outside_topic.name])
    if (exploration.path && exploration.path.length > 0) {
      rows.push(['Link path from your sites', exploration.path.join(' → ')])
    }
  }
  return (
    <dl className="grid gap-x-4 gap-y-1.5 text-sm sm:grid-cols-[auto_1fr]">
      {rows.map(([term, detail]) => (
        <div key={term} className="contents">
          <dt className="text-stone-500 dark:text-stone-400">{term}</dt>
          <dd className="break-words">{detail}</dd>
        </div>
      ))}
    </dl>
  )
}
