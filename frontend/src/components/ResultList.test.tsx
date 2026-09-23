import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { eventQueue } from '../events/eventQueue'
import { mockApi } from '../test/api'
import { feedItem, feedPage } from '../test/fixtures'
import { renderPage } from '../test/render'
import { ResultList } from './ResultList'

function renderList(items = [feedItem(1)]) {
  return renderPage(
    <ResultList
      pages={[feedPage(items)]}
      surface="feed"
      hasNextPage={false}
      isFetchingNextPage={false}
      nextPageError={null}
      fetchNextPage={vi.fn()}
    />,
  )
}

const feedback = (id: number, kind: string) => ({
  status: 201,
  body: { id, document_id: 1, kind, reason_text: null, created_at: '2026-09-23T00:00:00Z' },
})

it('shows a card with its reasons', () => {
  renderList()
  const card = screen.getByRole('article', { name: 'Document 1' })
  expect(within(card).getByRole('link', { name: 'Document 1' })).toHaveAttribute(
    'href',
    'https://example.com/1',
  )
  expect(within(card).getByText(/Linked from 2 sites you trust/)).toBeInTheDocument()
})

it('asks what you liked after a like, and saves the answer', async () => {
  const calls = mockApi({
    'POST /api/feedback': feedback(7, 'like'),
    'PATCH /api/feedback/7': { body: {} },
  })
  renderList()
  await userEvent.click(screen.getByRole('button', { name: 'Like' }))
  expect(calls[0]?.body).toEqual({
    document_id: 1,
    recommendation_id: 101,
    position: 1,
    kind: 'like',
  })
  await userEvent.type(
    await screen.findByRole('textbox', { name: /What did you like/ }),
    'Clear argument',
  )
  await userEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => {
    expect(screen.queryByRole('textbox', { name: /What did you like/ })).not.toBeInTheDocument()
  })
  expect(calls[1]).toMatchObject({ method: 'PATCH', body: { reason_text: 'Clear argument' } })
  expect(screen.getByRole('button', { name: 'Liked' })).toHaveAttribute('aria-pressed', 'true')
})

it('skips the like prompt without saving anything', async () => {
  const calls = mockApi({ 'POST /api/feedback': feedback(7, 'like') })
  renderList()
  await userEvent.click(screen.getByRole('button', { name: 'Like' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Skip' }))
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  expect(calls).toHaveLength(1)
})

it('collapses a hidden card', async () => {
  mockApi({ 'POST /api/feedback': feedback(8, 'hide') })
  renderList()
  await userEvent.click(screen.getByRole('button', { name: 'Hide' }))
  expect(await screen.findByText(/won't appear again/)).toBeInTheDocument()
  expect(screen.queryByRole('link')).not.toBeInTheDocument()
})

it('removes every card from a blocked site after confirming', async () => {
  const calls = mockApi({ 'POST /api/feedback': feedback(9, 'block_domain') })
  renderList([feedItem(1, 'spam.example'), feedItem(2, 'spam.example'), feedItem(3, 'good.org')])
  const first = screen.getByRole('article', { name: 'Document 1' })
  await userEvent.click(within(first).getByRole('button', { name: 'Block site' }))
  expect(calls).toHaveLength(0)
  await userEvent.click(within(first).getByRole('button', { name: 'Block spam.example' }))
  expect(await screen.findByText(/Blocked spam.example/)).toBeInTheDocument()
  expect(screen.queryByText('Document 2')).not.toBeInTheDocument()
  expect(screen.getByText('Document 3')).toBeInTheDocument()
  expect(calls[0]?.body).toMatchObject({ kind: 'block_domain', document_id: 1 })
})

it('opens the score breakdown', async () => {
  const item = feedItem(1)
  const calls = mockApi({
    'GET /api/recommendations/101/why': {
      body: {
        recommendation_id: 101,
        surface: 'feed',
        query: null,
        slice: 'main',
        score: 1.5,
        created_at: '2026-09-23T00:00:00Z',
        document: item.document,
        reasons: item.reasons,
        components: [
          { name: 'ppr', inputs: { ppr: 0.002 }, value: 1, weight: 1, contribution: 1 },
          { name: 'recency', inputs: { age_days: 3 }, value: 1, weight: 0.5, contribution: 0.5 },
        ],
        evidence: {
          age_days: 3,
          published: true,
          trusted_linkers: ['a.org'],
          trusted_linker_count: 3,
        },
      },
    },
  })
  renderList([item])
  await userEvent.click(screen.getByRole('button', { name: 'Why this?' }))
  const table = await screen.findByRole('table', { name: 'Score breakdown' })
  expect(within(table).getByRole('rowheader', { name: /Your sources/ })).toBeInTheDocument()
  expect(within(table).getByRole('rowheader', { name: 'Score' })).toBeInTheDocument()
  expect(screen.getByText('a.org and 2 more')).toBeInTheDocument()
  expect(calls.map((c) => c.path)).toEqual(['/api/recommendations/101/why'])
})

it('logs a click on the title', async () => {
  const click = vi.spyOn(eventQueue, 'click').mockImplementation(vi.fn())
  renderList()
  const link = screen.getByRole('link', { name: 'Document 1' })
  link.addEventListener('click', (event) => {
    event.preventDefault() // jsdom can't navigate
  })
  await userEvent.click(link)
  expect(click).toHaveBeenCalledWith({
    document_id: 1,
    recommendation_id: 101,
    position: 1,
    surface: 'feed',
  })
})
