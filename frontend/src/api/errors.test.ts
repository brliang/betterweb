import { expect, it } from 'vitest'
import { errorMessage } from './errors'
import { ApiError } from './fetcher'

it.each([
  [new ApiError(422, { detail: "not a website: 'nope'" }), "not a website: 'nope'"],
  [
    new ApiError(422, { detail: [{ loc: ['body'], msg: 'Field required', type: 'missing' }] }),
    'Field required',
  ],
  [new ApiError(502, 'Bad Gateway'), 'The server answered 502.'],
  [new TypeError('Failed to fetch'), "Can't reach the server."],
  ['?', 'Something went wrong.'],
])('errorMessage(%o) is %j', (error, message) => {
  expect(errorMessage(error)).toBe(message)
})
