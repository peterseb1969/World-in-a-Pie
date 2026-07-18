import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Path-prefix-ready for k8s/ingress (CASE-370). All of this is net-zero for
// local dev: with VITE_BASE_PATH / APP_BASE_PATH unset, BASE is '/', PREFIX is
// '', and the proxy keys are '/api' and '/wip' — identical to before.
const BASE = ((process.env.VITE_BASE_PATH || process.env.APP_BASE_PATH || '/').replace(/\/+$/, '')) + '/'
const PREFIX = BASE.replace(/\/$/, '')  // '' when BASE === '/'

export default defineConfig({
  base: BASE,
  plugins: [react()],
  server: {
    // Bind to all interfaces so a containerizing Caddy / ingress proxy can
    // reach the dev server (CASE-375). Harmless on the host — the OS firewall
    // normally still gates external access.
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      [`${PREFIX}/api`]: 'http://localhost:3001',
      [`${PREFIX}/wip`]: 'http://localhost:3001',
    },
  },
})
