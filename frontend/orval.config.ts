import { defineConfig } from 'orval'

// Generates the typed API client + TanStack Query hooks from the backend's OpenAPI schema.
// Never hand-edit src/api/generated; run `make codegen` from the repo root instead.
export default defineConfig({
  api: {
    input: { target: '../openapi.json' },
    output: {
      mode: 'single',
      target: './src/api/generated/api.ts',
      client: 'react-query',
      httpClient: 'fetch',
      baseUrl: '/api',
      clean: true,
      override: {
        // Return the response body directly and throw ApiError on non-2xx (see fetcher.ts).
        mutator: { path: './src/api/fetcher.ts', name: 'apiFetch' },
        fetch: { includeHttpResponseReturnType: false },
      },
    },
  },
})
