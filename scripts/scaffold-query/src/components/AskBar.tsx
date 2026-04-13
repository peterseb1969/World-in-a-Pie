import { useState, useRef, useEffect } from 'react'

interface Message {
  role: 'user' | 'assistant'
  content: string
  toolCalls?: number
}

const BASE_PATH = import.meta.env.BASE_URL || '/'

export default function AskBar() {
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string | undefined>()

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const question = input.trim()
    if (!question || loading) return

    setInput('')
    setMessages(prev => [...prev, { role: 'user', content: question }])
    setLoading(true)

    try {
      const res = await fetch(`${BASE_PATH}api/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, sessionId }),
      })
      const data = await res.json()

      if (!res.ok) {
        setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${data.error}` }])
      } else {
        setSessionId(data.sessionId)
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: data.answer,
          toolCalls: data.toolCalls,
        }])
      }
    } catch (err: any) {
      setMessages(prev => [...prev, { role: 'assistant', content: `Connection error: ${err.message}` }])
    } finally {
      setLoading(false)
    }
  }

  const handleNew = () => {
    setMessages([])
    setSessionId(undefined)
  }

  // --- Closed state: floating button ---
  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 w-14 h-14 rounded-full bg-blue-600 text-white shadow-lg hover:bg-blue-700 flex items-center justify-center z-50"
        aria-label="Open chat"
      >
        <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
        </svg>
      </button>
    )
  }

  // --- Open state: chat panel ---
  return (
    <div className="fixed bottom-6 right-6 w-[420px] max-w-[calc(100vw-2rem)] bg-white rounded-2xl shadow-2xl border border-gray-200 flex flex-col z-50" style={{ maxHeight: 'calc(100vh - 6rem)' }}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 bg-blue-600 text-white rounded-t-2xl">
        <span className="font-semibold text-sm">Ask</span>
        <div className="flex items-center gap-2">
          {messages.length > 0 && (
            <button onClick={handleNew} className="text-xs bg-blue-500 hover:bg-blue-400 px-2 py-1 rounded">
              New
            </button>
          )}
          <button onClick={() => setOpen(false)} className="text-white/80 hover:text-white text-lg leading-none">&times;</button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-[200px]">
        {messages.length === 0 && (
          <div className="text-gray-400 text-sm text-center py-8">
            Ask a question about your data...
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
              msg.role === 'user'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-100 text-gray-800'
            }`}>
              <div className="whitespace-pre-wrap">{msg.content}</div>
              {msg.toolCalls ? (
                <div className="text-xs mt-1 opacity-60">{msg.toolCalls} tool call{msg.toolCalls > 1 ? 's' : ''}</div>
              ) : null}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-100 rounded-lg px-3 py-2 text-sm text-gray-500">
              Thinking...
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="border-t border-gray-100 p-3 flex gap-2">
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          disabled={loading}
          placeholder="Type your question..."
          className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-400"
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </div>
  )
}
