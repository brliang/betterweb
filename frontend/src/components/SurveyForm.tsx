import { useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import type {
  Me,
  SuggestedSourceOut,
  SurveyAnswers,
  TopicOut,
  UserSettingsOut,
} from '../api/generated/api'
import {
  getFeedInfiniteQueryKey,
  getListPinsQueryKey,
  getMeQueryKey,
  getUserSettingsQueryKey,
  useSubmitSurvey,
} from '../api/generated/api'
import { relatedTopicIds } from '../lib/topics'
import { primaryButton, secondaryButton } from '../lib/styles'
import { ContentTypePicker } from './ContentTypePicker'
import { ErrorNotice } from './ErrorNotice'
import { ExplorationPicker } from './ExplorationPicker'
import { InterestPicker } from './InterestPicker'
import { PresetPicker } from './PresetPicker'
import { SitePicker } from './SitePicker'

interface Step {
  title: string
  intro: string
  body: ReactNode
  /** Why Next is disabled, if it is. */
  blocked: string | null
}

export function SurveyForm({
  topics,
  suggestions,
  defaults,
}: {
  topics: TopicOut[]
  suggestions: SuggestedSourceOut[]
  defaults: UserSettingsOut
}) {
  const [step, setStep] = useState(0)
  const [answers, setAnswers] = useState<SurveyAnswers>({
    interests: defaults.interests,
    sites: [],
    content_types: defaults.content_types,
    exploration_pct: defaults.exploration_pct,
    preset: defaults.preset ?? 'balanced',
  })
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const submit = useSubmitSurvey({
    mutation: {
      onSuccess: () => {
        queryClient.setQueryData(getMeQueryKey(), (me: Me | undefined) =>
          me ? { ...me, survey_completed: true } : me,
        )
        for (const queryKey of [getUserSettingsQueryKey(), getListPinsQueryKey()]) {
          void queryClient.invalidateQueries({ queryKey })
        }
        queryClient.removeQueries({ queryKey: getFeedInfiniteQueryKey() })
        void navigate('/', { replace: true })
      },
    },
  })
  const update = (change: Partial<SurveyAnswers>) => {
    setAnswers({ ...answers, ...change })
  }
  const sites = answers.sites ?? []

  const steps: Step[] = [
    {
      title: 'What are you interested in?',
      intro:
        'Pick topics, and mark the ones you care most about. Your feed favors pages on these topics; open a topic to get more specific.',
      body: (
        <InterestPicker
          topics={topics}
          value={answers.interests}
          onChange={(interests) => {
            update({ interests })
          }}
        />
      ),
      blocked: answers.interests.length === 0 ? 'Pick at least one topic.' : null,
    },
    {
      title: 'Which sites do you trust?',
      intro:
        'bribot crawls the sites you pin every night, then follows their links. What your sites link to ranks higher. You can add more later.',
      body: (
        <SitePicker
          suggestions={suggestions}
          relevantTopicIds={relatedTopicIds(topics, answers.interests)}
          value={sites}
          onChange={(value) => {
            update({ sites: value })
          }}
        />
      ),
      blocked: null,
    },
    {
      title: 'What kinds of pages?',
      intro: 'Your feed shows only these.',
      body: (
        <ContentTypePicker
          value={answers.content_types}
          onChange={(content_types) => {
            update({ content_types })
          }}
        />
      ),
      blocked: answers.content_types.length === 0 ? 'Pick at least one kind.' : null,
    },
    {
      title: 'How much should your feed explore?',
      intro:
        'Some items come from topics next to yours and from sites a link or two away from the ones you pinned.',
      body: (
        <ExplorationPicker
          choices={defaults.exploration_choices}
          value={answers.exploration_pct}
          onChange={(exploration_pct) => {
            update({ exploration_pct })
          }}
        />
      ),
      blocked: null,
    },
    {
      title: 'How should your feed be ranked?',
      intro: 'Every item says why it ranked where it did. You can change this in settings.',
      body: (
        <PresetPicker
          value={answers.preset}
          onChange={(preset) => {
            update({ preset })
          }}
        />
      ),
      blocked: null,
    },
  ]
  const current = steps[step] ?? steps[0]
  const last = step === steps.length - 1
  if (!current) return null

  return (
    <form
      aria-labelledby="survey-heading"
      onSubmit={(event) => {
        event.preventDefault()
        if (current.blocked) return
        if (last) submit.mutate({ data: { version: 1, answers } })
        else setStep(step + 1)
      }}
    >
      <p className="text-sm text-stone-500 dark:text-stone-400">
        Step {step + 1} of {steps.length}
      </p>
      {/* A new heading per step, focused so screen readers announce the step. */}
      <h1
        key={step}
        id="survey-heading"
        tabIndex={-1}
        ref={(heading) => {
          if (step > 0) heading?.focus()
        }}
        className="mt-1 text-2xl font-semibold tracking-tight focus:outline-none"
      >
        {current.title}
      </h1>
      <p className="mt-2 mb-6 text-stone-600 dark:text-stone-400">{current.intro}</p>
      {current.body}
      {submit.isError && (
        <div className="mt-6">
          <ErrorNotice error={submit.error} title="Couldn't save your answers" />
        </div>
      )}
      <div className="sticky bottom-0 mt-6 flex items-center gap-3 border-t border-stone-200 bg-stone-50 py-4 dark:border-stone-800 dark:bg-stone-950">
        {step > 0 && (
          <button
            type="button"
            className={secondaryButton}
            onClick={() => {
              setStep(step - 1)
            }}
          >
            Back
          </button>
        )}
        <button
          type="submit"
          className={`${primaryButton} ml-auto`}
          disabled={current.blocked !== null || submit.isPending}
        >
          {last ? (submit.isPending ? 'Saving…' : 'Finish') : 'Next'}
        </button>
        {current.blocked && (
          <p className="order-first text-sm text-stone-500 dark:text-stone-400">
            {current.blocked}
          </p>
        )}
      </div>
    </form>
  )
}
