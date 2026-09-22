import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The generated client calls `/api/...`; in dev that is proxied to FastAPI (Caddy does the same in prod).
const apiTarget = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: apiTarget, rewrite: (path) => path.replace(/^\/api/, '') },
    },
  },
})
