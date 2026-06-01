import 'dotenv/config'  // Must be first — loads .env before any other module reads process.env
import express from 'express'
import cors from 'cors'
import session from 'express-session'
import { initAgent, ask } from './agent.js'
import { initAuth, requireAuth, handleCallback, handleLogout } from './auth.js'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const PORT = parseInt(process.env.PORT || '3001')
// Path-prefix-ready for k8s/ingress (CASE-370). Net-zero for local dev:
// APP_BASE_PATH unset → BASE_PATH '' → the router mounts at '/' and the session
// cookie path is '/', identical to before.
const BASE_PATH = (process.env.APP_BASE_PATH || '').replace(/\/+$/, '')
const COOKIE_PATH = BASE_PATH ? BASE_PATH + '/' : '/'
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const app = express()
const router = express.Router()

app.use(cors())
app.use(express.json())

// --- Session (required for OIDC auth). Cookie scoped to the app's base path
// so sibling apps behind the same ingress don't share it. ---
router.use(session({
  secret: process.env.SESSION_SECRET || 'dev-session-secret',
  resave: false,
  saveUninitialized: false,
  cookie: {
    secure: process.env.NODE_ENV === 'production',
    httpOnly: true,
    path: COOKIE_PATH,
    maxAge: 24 * 60 * 60 * 1000, // 24 hours
  },
}))

// --- Auth routes ---
router.get('/auth/callback', (req, res) => { handleCallback(req, res) })
router.get('/auth/logout', handleLogout)

// --- Auth middleware (no-op when OIDC_ISSUER is not set) ---
router.use(requireAuth())

// --- Health ---
router.get('/api/health', (_req, res) => {
  res.json({ status: 'ok' })
})

// --- Ask endpoint ---
router.post('/api/ask', async (req, res) => {
  const { question, sessionId } = req.body
  if (!question) {
    res.status(400).json({ error: 'question is required' })
    return
  }
  try {
    const result = await ask(question, sessionId)
    res.json(result)
  } catch (err: any) {
    console.error('Ask error:', err)
    res.status(500).json({ error: err.message || 'Internal error' })
  }
})

// --- User info (for authenticated apps) ---
router.get('/api/me', (req, res) => {
  if (req.session.user) {
    res.json(req.session.user)
  } else {
    res.json({ anonymous: true })
  }
})

// --- Production: serve the built SPA from this router so the app is
// self-contained behind the ingress (CASE-370). Dev serves the SPA via Vite. ---
if (process.env.NODE_ENV === 'production') {
  const distDir = path.resolve(__dirname, '..', 'dist')
  router.use(express.static(distDir))
  // SPA fallback for client-side routes. A middleware (not app.get('*')) —
  // Express 5 / path-to-regexp rejects a bare '*' route pattern.
  router.use((req, res, next) => {
    if (req.method !== 'GET') return next()
    res.sendFile(path.join(distDir, 'index.html'))
  })
}

// Mount everything under the base path (CASE-370); '/' for local dev.
app.use(BASE_PATH || '/', router)

// --- Start ---
async function main() {
  await initAuth()
  await initAgent()
  app.listen(PORT, () => {
    console.log(`Server listening on http://localhost:${PORT}${BASE_PATH || ''}`)
  })
}

main().catch(err => {
  console.error('Failed to start:', err)
  process.exit(1)
})
