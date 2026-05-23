import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { SSEClientTransport } from '@modelcontextprotocol/sdk/client/sse.js'
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js'
import Anthropic from '@anthropic-ai/sdk'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))

// ---------- Configuration ----------

let mcpClient: Client | null = null
let mcpTools: Anthropic.Tool[] = []
let systemPrompt = ''

// Read env vars lazily — module-level reads race with dotenv in ESM
const env = () => ({
  MCP_URL: process.env.MCP_URL || '',
  MCP_TRANSPORT: process.env.MCP_TRANSPORT || '',
  MCP_PYTHON: process.env.MCP_PYTHON || '',
  MCP_CWD: process.env.MCP_CWD || '',
  MCP_MODULE: process.env.MCP_MODULE || 'wip_mcp',
  ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY || '',
  CLAUDE_MODEL: process.env.CLAUDE_MODEL || 'claude-haiku-4-5',
  WIP_NAMESPACE: process.env.WIP_NAMESPACE || '',
  // CASE-312 reopen: HTTP/SSE transports need to carry the API key on every
  // request. The MCP SDK's transports don't auto-read process.env — they take
  // explicit requestInit.headers. Without this, the platform's auth middleware
  // 401s the initialize handshake and the agent crashes at startup.
  WIP_API_KEY: process.env.WIP_API_KEY || '',
  MAX_TURNS: parseInt(process.env.MAX_TURNS || '15'),
  SESSION_TTL_MS: parseInt(process.env.SESSION_TTL_MINUTES || '30') * 60_000,
})

// Only expose read/query tools — no create, delete, or admin tools
const ALLOWED_TOOLS = new Set([
  'get_wip_status',
  'describe_data_model',
  'search',
  'search_registry',
  'list_namespaces',
  'get_namespace_stats',
  'list_terminologies',
  'get_terminology',
  'get_terminology_by_value',
  'list_terms',
  'get_term',
  'validate_term_value',
  'get_term_hierarchy',
  'list_templates',
  'get_template',
  'get_template_by_value',
  'get_template_fields',
  'list_documents',
  'get_document',
  'query_documents',
  'query_by_template',
  'get_document_versions',
  'get_file_metadata',
  'list_report_tables',
  'run_report_query',
])

// ---------- Session management ----------

interface Session {
  messages: Anthropic.MessageParam[]
  lastAccess: number
}

const sessions = new Map<string, Session>()

setInterval(() => {
  const now = Date.now()
  for (const [id, session] of sessions) {
    if (now - session.lastAccess > env().SESSION_TTL_MS) {
      sessions.delete(id)
    }
  }
}, 60_000)

// ---------- MCP connection ----------

function createTransport() {
  const e = env()
  if (e.MCP_URL) {
    const url = new URL(e.MCP_URL)

    // CASE-312 reopen: inject the API key into the transport's requestInit
    // headers. The MCP SDK doesn't read process.env on its own. Without this,
    // initialize POSTs to a deployed wip-deploy install hit the platform's
    // ApiKeyMiddleware and 401 — the agent crashes before the askBar can
    // load any tools.
    const headers: Record<string, string> = {}
    if (e.WIP_API_KEY) headers['X-API-Key'] = e.WIP_API_KEY

    if (e.MCP_TRANSPORT === 'sse' || e.MCP_URL.endsWith('/sse')) {
      // SSE has two surfaces — the EventSource for the read stream and the
      // POST endpoint for outbound JSON-RPC. Both need the header.
      return new SSEClientTransport(url, {
        requestInit: { headers },
        eventSourceInit: {
          fetch: (u, init) => fetch(u, {
            ...init,
            headers: { ...(init?.headers as Record<string, string> | undefined), ...headers },
          }),
        },
      })
    }
    // Default to Streamable HTTP for remote URLs
    return new StreamableHTTPClientTransport(url, { requestInit: { headers } })
  }

  // Stdio: spawn local MCP server process
  const pythonPath = e.MCP_PYTHON || 'python'
  const cwd = e.MCP_CWD || process.cwd()
  return new StdioClientTransport({
    command: pythonPath,
    args: ['-m', e.MCP_MODULE],
    cwd,
    env: {
      ...process.env as Record<string, string>,
      WIP_API_KEY: process.env.WIP_API_KEY || 'dev_master_key_for_testing',
      WIP_MCP_MODE: 'readonly',
      PYTHONPATH: join(cwd, 'components/mcp-server/src'),
    },
  })
}

export async function initAgent() {
  if (!env().ANTHROPIC_API_KEY) {
    console.warn('⚠ ANTHROPIC_API_KEY not set — /api/ask will be unavailable')
    return
  }

  const transport = createTransport()
  mcpClient = new Client({ name: 'wip-query-agent', version: '0.1.0' })
  await mcpClient.connect(transport)
  console.log('✓ MCP client connected')

  // Fetch and filter tools
  const toolsResult = await mcpClient.listTools()
  mcpTools = toolsResult.tools
    .filter(t => ALLOWED_TOOLS.has(t.name))
    .map(t => ({
      name: t.name,
      description: t.description || '',
      input_schema: t.inputSchema as Anthropic.Tool['input_schema'],
    }))

  // CASE-352: surface ALLOWED_TOOLS drift loudly. Each MCP-server tool
  // rename invalidates whatever names this scaffold's whitelist pinned;
  // before this warning, scaffolds silently dropped renamed entries and
  // ran with degraded capabilities. The warning makes the next drift
  // discoverable at startup instead of via reduced functionality.
  const requested = ALLOWED_TOOLS.size
  const matched = mcpTools.length
  if (matched < requested) {
    const live = new Set(toolsResult.tools.map(t => t.name))
    const missing = [...ALLOWED_TOOLS].filter(n => !live.has(n))
    console.warn(
      `⚠ ALLOWED_TOOLS drift: ${matched}/${requested} resolved. ` +
      `Dead-letter entries (renamed or removed in MCP server): ${missing.join(', ')}. ` +
      `Update the whitelist or remove these names.`
    )
  }
  console.log(`✓ ${matched} tools available (filtered from ${toolsResult.tools.length})`)

  // Read the query assistant prompt resource for system prompt
  try {
    const resource = await mcpClient.readResource({ uri: 'wip://query-assistant-prompt' })
    systemPrompt = (resource.contents[0] as any)?.text || ''
    console.log(`✓ System prompt loaded from wip://query-assistant-prompt (${systemPrompt.length} chars)`)
  } catch {
    console.warn('⚠ Could not read wip://query-assistant-prompt — using fallback')
    systemPrompt = 'You are a helpful query assistant for a WIP data store. Use the available tools to answer questions about the data.'
  }

  // Append app-specific instructions if available
  try {
    const extra = readFileSync(join(__dirname, 'prompts', 'assistant.md'), 'utf-8')
    if (extra.trim()) {
      systemPrompt += '\n\n' + extra
    }
  } catch {
    // No app-specific prompt — that's fine
  }
}

// ---------- Ask ----------

export async function ask(
  question: string,
  sessionId?: string,
): Promise<{ answer: string; toolCalls: number; sessionId: string }> {
  if (!mcpClient) {
    throw new Error('Agent not initialised — is ANTHROPIC_API_KEY set?')
  }

  const id = sessionId || crypto.randomUUID()
  let session = sessions.get(id)
  if (!session) {
    session = { messages: [], lastAccess: Date.now() }
    sessions.set(id, session)
  }
  session.lastAccess = Date.now()

  // Add user message
  session.messages.push({ role: 'user', content: question })

  const e = env()
  const anthropic = new Anthropic({ apiKey: e.ANTHROPIC_API_KEY })
  let totalToolCalls = 0

  for (let turn = 0; turn < e.MAX_TURNS; turn++) {
    const response = await anthropic.messages.create({
      model: e.CLAUDE_MODEL,
      max_tokens: 4096,
      // CASE-308 — prompt caching on the static blocks. The system prompt and
      // tool definitions don't change across turns within a session; marking
      // them as cache breakpoints lets turn 2+ within the 5-minute TTL hit
      // cache instead of re-reading the same tokens. The cache_control on
      // the LAST tool definition extends caching across the whole tools array.
      system: [
        { type: 'text', text: systemPrompt, cache_control: { type: 'ephemeral' } },
      ],
      tools: mcpTools.map((t, idx, arr) =>
        idx === arr.length - 1
          ? { ...t, cache_control: { type: 'ephemeral' } }
          : t
      ),
      messages: session.messages,
    })

    if (response.stop_reason === 'end_turn' || response.stop_reason !== 'tool_use') {
      // Extract text from response
      const answer = response.content
        .filter((b): b is Anthropic.TextBlock => b.type === 'text')
        .map(b => b.text)
        .join('\n')

      session.messages.push({ role: 'assistant', content: response.content })
      return { answer, toolCalls: totalToolCalls, sessionId: id }
    }

    // Handle tool calls
    const toolResults: Anthropic.ToolResultBlockParam[] = []

    for (const block of response.content) {
      if (block.type !== 'tool_use') continue
      totalToolCalls++

      const args = block.input as Record<string, unknown>

      // Inject namespace if configured and the tool accepts it
      if (e.WIP_NAMESPACE && 'namespace' in (args || {})) {
        args.namespace = e.WIP_NAMESPACE
      } else if (e.WIP_NAMESPACE && args && !('namespace' in args)) {
        // Only inject if the tool likely accepts namespace
        const toolDef = mcpTools.find(t => t.name === block.name)
        const props = (toolDef?.input_schema as any)?.properties || {}
        if ('namespace' in props) {
          args.namespace = e.WIP_NAMESPACE
        }
      }

      try {
        const result = await mcpClient!.callTool({ name: block.name, arguments: args })
        toolResults.push({
          type: 'tool_result',
          tool_use_id: block.id,
          content: typeof result.content === 'string'
            ? result.content
            : JSON.stringify(result.content),
          is_error: result.isError === true,
        })
      } catch (err: any) {
        toolResults.push({
          type: 'tool_result',
          tool_use_id: block.id,
          content: `Tool error: ${err.message}`,
          is_error: true,
        })
      }
    }

    // Feed tool results back
    session.messages.push({ role: 'assistant', content: response.content })
    session.messages.push({ role: 'user', content: toolResults })
  }

  return {
    answer: 'Reached maximum number of tool-call turns. Try a more specific question.',
    toolCalls: totalToolCalls,
    sessionId: id,
  }
}
