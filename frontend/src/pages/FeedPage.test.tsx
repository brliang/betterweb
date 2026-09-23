import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import type { Call } from '../test/api'
import { mockApi } from '../test/api'
import { defaultSettings, feedItem, feedPage } from '../test/fixtures'
import { renderPage } from '../test/render'
import { FeedPage } from './FeedPage'

const feedPaths = (calls: Call[]) =>
  calls.map((call) => call.path).filter((path) => path.startsWith('/api/feed'))

it('pages through the feed with the cursor, and starts over on request', async () => {
  const calls = mockApi({
    'GET /api/feed': ({ path }) => ({
      body: path.includes('cursor=101') ? feedPage([feedItem(2)]) : feedPage([feedItem(1)], '101'),
    }),
  })
  renderPage(<FeedPage />)
  expect(await screen.findByText('Document 1')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
  expect(await screen.findByText('Document 2')).toBeInTheDocument()
  expect(screen.getByText("That's everything for now.")).toBeInTheDocument()
  expect(feedPaths(calls)).toEqual(['/api/feed', '/api/feed?cursor=101'])

  await userEvent.click(screen.getByRole('button', { name: 'Start over' }))
  expect(await screen.findByText('Document 1')).toBeInTheDocument()
  expect(feedPaths(calls)).toEqual(['/api/feed', '/api/feed?cursor=101', '/api/feed'])
})

it('explains an empty feed', async () => {
  mockApi({ 'GET /api/feed': { body: feedPage([]) } })
  renderPage(<FeedPage />)
  expect(await screen.findByText('Nothing to show yet.')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'pin more sites' })).toHaveAttribute('href', '/settings')
})

it('offers summaries when the settings say so', async () => {
  mockApi({
    'GET /api/feed': { body: feedPage([feedItem(1)]) },
    'GET /api/settings': { body: { ...defaultSettings, summaries_opt_in: true } },
  })
  renderPage(<FeedPage />)
  expect(await screen.findByRole('button', { name: 'Why might I like this?' })).toBeInTheDocument()
})
