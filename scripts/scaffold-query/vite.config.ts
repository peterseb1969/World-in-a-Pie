import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The app is served at the root of its own origin
// (https://<label>.<hostname>/), in dev as in production: Vite's default base
// '/' is the one it needs.
export default defineConfig({
  plugins: [react()],
  server: {
    // Bind to all interfaces so a containerizing Caddy / ingress proxy can
    // reach the dev server (CASE-375). Harmless on the host — the OS firewall
    // normally still gates external access.
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': 'http://localhost:3001',
      '/wip': 'http://localhost:3001',
    },
  },
})
