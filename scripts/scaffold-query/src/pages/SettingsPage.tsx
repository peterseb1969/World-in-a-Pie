import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'

// Admin-only runtime settings (CASE-509). The Anthropic key control sets/rotates
// the askBar agent's key without a redeploy. The key is write-only here — the
// server never returns it, so we display only configured/source/last-4.
const API = `${import.meta.env.BASE_URL}api`

interface KeyStatus {
  configured: boolean
  source: 'override' | 'file' | 'env' | 'none'
  last4: string | null
  agentReady: boolean
  persisted?: boolean
}

export default function SettingsPage() {
  const [status, setStatus] = useState<KeyStatus | null>(null)
  const [forbidden, setForbidden] = useState(false)
  const [key, setKey] = useState('')
  const [persist, setPersist] = useState(true)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  async function loadStatus() {
    const res = await fetch(`${API}/config/anthropic-key`)
    if (res.status === 403) { setForbidden(true); return }
    if (res.ok) setStatus(await res.json())
  }

  useEffect(() => { loadStatus() }, [])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setMsg(null)
    try {
      const res = await fetch(`${API}/config/anthropic-key`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key.trim(), persist }),
      })
      const body = await res.json()
      if (!res.ok) {
        setMsg({ kind: 'err', text: body.error || 'Request failed' })
        return
      }
      setStatus(body)
      setKey('')
      setMsg({
        kind: 'ok',
        text: `Key set (source: ${body.source}${body.persisted ? ', persisted to file' : ', in-memory only'}). Agent ${body.agentReady ? 'ready' : 'not initialised'}.`,
      })
    } catch (err: any) {
      setMsg({ kind: 'err', text: err?.message || 'Request failed' })
    } finally {
      setBusy(false)
    }
  }

  if (forbidden) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <h1 className="text-xl font-semibold text-gray-800">Settings</h1>
        <p className="mt-4 text-gray-500">Administrator access required.</p>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl p-6">
      <h1 className="text-xl font-semibold text-gray-800">Settings</h1>

      <section className="mt-6 rounded-lg border border-gray-200 bg-white p-5">
        <h2 className="font-medium text-gray-800">Anthropic API key</h2>
        <p className="mt-1 text-sm text-gray-500">
          Powers the askBar agent. Set or rotate the key without a redeploy. The key is
          never displayed.
        </p>

        {status && (
          <dl className="mt-4 grid grid-cols-2 gap-y-1 text-sm">
            <dt className="text-gray-400">Configured</dt>
            <dd className="text-gray-800">{status.configured ? `yes (…${status.last4})` : 'no'}</dd>
            <dt className="text-gray-400">Source</dt>
            <dd className="text-gray-800">{status.source}</dd>
            <dt className="text-gray-400">Agent</dt>
            <dd className="text-gray-800">{status.agentReady ? 'ready' : 'not initialised'}</dd>
          </dl>
        )}

        <form onSubmit={submit} className="mt-5 space-y-3">
          <input
            type="password"
            autoComplete="off"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="sk-ant-…"
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          />
          <label className="flex items-center gap-2 text-sm text-gray-500">
            <input
              type="checkbox"
              checked={persist}
              onChange={(e) => setPersist(e.target.checked)}
            />
            Persist to the key file (survives restart when ANTHROPIC_API_KEY_FILE is set)
          </label>
          <button
            type="submit"
            disabled={busy || !key.trim()}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {busy ? 'Validating…' : 'Set key'}
          </button>
        </form>

        {msg && (
          <p className={`mt-3 text-sm ${msg.kind === 'ok' ? 'text-green-700' : 'text-red-700'}`}>
            {msg.text}
          </p>
        )}
      </section>
    </div>
  )
}
