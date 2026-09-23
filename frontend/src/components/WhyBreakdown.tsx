import type { Why } from '../api/generated/api'
import { formatDateTime, formatScore } from '../lib/format'
import { COMPONENTS, SLICES } from '../lib/labels'
import { ReasonChips } from './ReasonChips'
import { WhyEvidence } from './WhyEvidence'

/** Every score component with its inputs, weight and contribution, then the evidence. */
export function WhyBreakdown({ why }: { why: Why }) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-stone-600 dark:text-stone-400">
        Shown {formatDateTime(why.created_at)}{' '}
        {why.surface === 'search' ? `for the search “${why.query ?? ''}”` : 'in your feed'}
        {why.slice !== 'main' && ` · ${SLICES[why.slice]}`}
      </p>
      <ReasonChips reasons={why.reasons} />
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Score breakdown</caption>
          <thead className="text-xs text-stone-500 uppercase dark:text-stone-400">
            <tr>
              <th scope="col" className="py-1 pr-3 font-medium">
                Part of the score
              </th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">
                Value
              </th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">
                Weight
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                Adds
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-stone-200 dark:divide-stone-700">
            {why.components.map((component) => (
              <tr key={component.name}>
                <th scope="row" className="py-1.5 pr-3 font-normal">
                  <span className="font-medium">{COMPONENTS[component.name].label}</span>
                  <span className="hidden text-xs text-stone-500 sm:block dark:text-stone-400">
                    {COMPONENTS[component.name].description}
                  </span>
                  <span className="block font-mono text-xs break-all text-stone-500 dark:text-stone-400">
                    {Object.entries(component.inputs)
                      .map(([key, value]) => `${key} ${formatScore(value)}`)
                      .join(' · ')}
                  </span>
                </th>
                <td className="py-1.5 pr-3 text-right font-mono">{formatScore(component.value)}</td>
                <td className="py-1.5 pr-3 text-right font-mono">
                  {formatScore(component.weight)}
                </td>
                <td className="py-1.5 text-right font-mono">
                  {formatScore(component.contribution)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t-2 border-stone-300 dark:border-stone-600">
              <th scope="row" colSpan={3} className="py-1.5 pr-3 font-medium">
                Score
              </th>
              <td className="py-1.5 text-right font-mono font-medium">{formatScore(why.score)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      <WhyEvidence evidence={why.evidence} />
    </div>
  )
}
