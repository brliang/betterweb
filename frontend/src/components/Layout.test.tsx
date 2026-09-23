import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { mockApi } from '../test/api'
import { Layout } from './Layout'

function renderAt(path: string) {
  const router = createMemoryRouter(
    [
      {
        element: <Layout />,
        children: [
          { index: true, element: <p>feed page</p> },
          { path: 'survey', element: <p>survey page</p> },
          { path: 'admin', element: <p>admin page</p> },
        ],
      },
    ],
    { initialEntries: [path] },
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

const me = (survey_completed: boolean, is_admin = false) => ({
  body: { email: 'reader@example.com', timezone: 'UTC', survey_completed, is_admin },
})

it('shows how to sign in without a session', async () => {
  mockApi({ 'GET /api/me': { status: 401, body: { detail: 'not signed in' } } })
  renderAt('/')
  expect(await screen.findByText(/You're signed out/)).toBeInTheDocument()
})

it('sends a new user to the survey', async () => {
  mockApi({ 'GET /api/me': me(false) })
  const router = renderAt('/')
  expect(await screen.findByText('survey page')).toBeInTheDocument()
  expect(router.state.location.pathname).toBe('/survey')
  expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
})

it('shows the feed and navigation after the survey', async () => {
  mockApi({ 'GET /api/me': me(true) })
  renderAt('/survey')
  expect(await screen.findByText('feed page')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Settings' })).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Admin' })).not.toBeInTheDocument()
})

it('links the admin page for admins', async () => {
  mockApi({ 'GET /api/me': me(true, true) })
  renderAt('/')
  expect(await screen.findByRole('link', { name: 'Admin' })).toBeInTheDocument()
})
