import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import { mockApi } from '../test/api'
import { defaultSettings, topic } from '../test/fixtures'
import { renderPage } from '../test/render'
import { SurveyPage } from './SurveyPage'

const source = {
  name: 'Example Journal',
  url: 'https://journal.example/',
  host: 'journal.example',
  feed: 'https://journal.example/feed',
  description: 'Long reads.',
  topics: [{ id: 2, name: 'Hiking' }],
}

function start() {
  const calls = mockApi({
    'GET /api/taxonomy': { body: [topic(1, 'Travel'), topic(2, 'Hiking', 1), topic(3, 'Science')] },
    'GET /api/suggested-sources': { body: [source] },
    'GET /api/settings': { body: defaultSettings },
    'POST /api/survey': ({ body }) => ({
      status: 201,
      body: { id: 1, version: 1, answers: body, created_at: '2026-09-23T00:00:00Z' },
    }),
  })
  return { calls, ...renderPage(<SurveyPage />, { path: '/survey' }) }
}

const next = () => userEvent.click(screen.getByRole('button', { name: 'Next' }))

it('walks through the five steps and submits the answers', async () => {
  const { calls, router } = start()

  expect(
    await screen.findByRole('heading', { name: 'What are you interested in?' }),
  ).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  await userEvent.click(
    within(screen.getByRole('group', { name: 'Travel' })).getByLabelText('Interested'),
  )
  await userEvent.click(screen.getByRole('button', { name: '▸ 1 subtopics' }))
  await userEvent.click(
    within(screen.getByRole('group', { name: 'Hiking' })).getByLabelText('Very interested'),
  )
  await next()

  expect(screen.getByRole('heading', { name: 'Which sites do you trust?' })).toHaveFocus()
  await userEvent.type(screen.getByRole('textbox', { name: 'Add a site you trust' }), 'not a site')
  await userEvent.click(screen.getByRole('button', { name: 'Add' }))
  expect(screen.getByText(/Enter a domain like example.com/)).toBeInTheDocument()
  await userEvent.clear(screen.getByRole('textbox', { name: 'Add a site you trust' }))
  await userEvent.type(
    screen.getByRole('textbox', { name: 'Add a site you trust' }),
    'blog.example.org{Enter}',
  )
  await userEvent.click(screen.getByRole('checkbox', { name: /Example Journal/ }))
  await next()

  expect(screen.getByRole('checkbox', { name: /Articles/ })).toBeChecked()
  await userEvent.click(screen.getByRole('checkbox', { name: /Papers/ }))
  await next()

  await userEvent.click(screen.getByRole('radio', { name: /35%/ }))
  await next()

  await userEvent.click(screen.getByRole('radio', { name: /Fresh/ }))
  await userEvent.click(screen.getByRole('button', { name: 'Finish' }))

  await waitFor(() => {
    expect(router.state.location.pathname).toBe('/')
  })
  const submitted = calls.find((call) => call.method === 'POST')
  expect(submitted?.body).toEqual({
    version: 1,
    answers: {
      interests: [
        { topic_id: 1, level: 'interested' },
        { topic_id: 2, level: 'very_interested' },
      ],
      sites: ['blog.example.org', 'https://journal.example/'],
      content_types: ['article', 'post'],
      exploration_pct: 0.35,
      preset: 'fresh',
    },
  })
})

it('keeps what you picked when you go back', async () => {
  start()
  await screen.findByRole('heading', { name: 'What are you interested in?' })
  await userEvent.click(
    within(screen.getByRole('group', { name: 'Science' })).getByLabelText('Interested'),
  )
  await next()
  await userEvent.click(screen.getByRole('button', { name: 'Back' }))
  expect(
    within(screen.getByRole('group', { name: 'Science' })).getByLabelText('Interested'),
  ).toBeChecked()
  expect(screen.getByText('1 topic picked')).toBeInTheDocument()
})

it('finds subtopics by name', async () => {
  start()
  await userEvent.type(await screen.findByRole('searchbox', { name: 'Find a topic' }), 'hik')
  expect(screen.getByRole('group', { name: 'Hiking' })).toBeInTheDocument()
  expect(screen.queryByRole('group', { name: 'Science' })).not.toBeInTheDocument()
})
