import express from 'express'
import cors from 'cors'
import { initAgent, ask } from './agent.js'

const PORT = parseInt(process.env.PORT || '3001')
const app = express()

app.use(cors())
app.use(express.json())

// --- Health ---
app.get('/api/health', (_req, res) => {
  res.json({ status: 'ok' })
})

// --- Ask endpoint ---
app.post('/api/ask', async (req, res) => {
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

// --- Start ---
async function main() {
  await initAgent()
  app.listen(PORT, () => {
    console.log(`Server listening on http://localhost:${PORT}`)
  })
}

main().catch(err => {
  console.error('Failed to start:', err)
  process.exit(1)
})
