import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import { mockApi } from '../test/api'
import { feedItem, feedPage } from '../test/fixtures'
import { renderPage } from '../test/render'
import { FeedPage } from './FeedPage'

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
  expect(calls.map((call) => call.path)).toEqual(['/api/feed', '/api/feed?cursor=101'])

  await userEvent.click(screen.getByRole('button', { name: 'Start over' }))
  expect(await screen.findByText('Document 1')).toBeInTheDocument()
  expect(calls.map((call) => call.path)).toEqual(['/api/feed', '/api/feed?cursor=101', '/api/feed'])
})

it('explains an empty feed', async () => {
  mockApi({ 'GET /api/feed': { body: feedPage([]) } })
  renderPage(<FeedPage />)
  expect(await screen.findByText('Nothing to show yet.')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'pin more sites' })).toHaveAttribute('href', '/settings')
})
