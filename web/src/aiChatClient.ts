export type ChatTemplate = { id: string; version: string; label: string; instruction: string }

export type CodexCapabilities = {
  codex: {
    available: boolean
    authenticated: boolean
    version?: string | null
    provider?: string
    model?: string | null
    experimental: boolean
    process_running?: boolean
    transport?: string
    sandbox?: string
    approval_policy?: string
    mcp_enabled?: boolean
    mcp_server?: string
    mcp_tools?: string[]
    builtin_tools_disabled?: string[]
    tool_event_tripwire?: boolean
    restart_count?: number
    error?: string | null
  }
  templates: ChatTemplate[]
  signal_templates?: ChatTemplate[]
}

export type ChatMessage = {
  message_id: string
  role: 'user' | 'assistant'
  sequence: number
  content: string
  incomplete: boolean
  created_at_ms: number
}

export type ChatTurn = {
  turn_id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  error?: string | null
  messages: ChatMessage[]
}

export type ChatConversation = {
  conversation_id: string
  context_kind?: 'trend_analysis' | 'signal_run'
  context_id?: string
  symbol: string | null
  timeframe: string | null
  source_run_id: string | null
  as_of_date: string
  algorithm_version: string
  config_version: string
  completion_state: string
  preview: boolean
  input_digest?: string
  source_observed_at_ms?: number | null
  title: string
  status: 'active' | 'archived'
  turns: ChatTurn[]
}

export type ChatConversationSummary = {
  conversation_id: string
  context_kind?: 'trend_analysis' | 'signal_run'
  context_id?: string
  source_run_id: string | null
  title: string
  status: 'active' | 'archived'
  as_of_date: string
  turn_count: number
  created_at_ms: number
  updated_at_ms: number
}

export type ChatTurnContextSummary = {
  schema_version: string
  workspace_reference: string
  source_run_id: string | null
  as_of_date: string
  input_start_date: string
  input_end_date: string
  input_digest: string
  algorithm_version: string
  config_version: string
  completion_state: string
  price_basis?: string
  volume_semantics?: string
  preview: boolean
  source_observed_at_ms?: number | null
  stale: boolean
  stale_reasons: string[]
  truncated: boolean
  evidence_codes: string[]
  sources: string[]
  tool_request_id?: string | null
}

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const payload = await response.json() as { detail?: string }
      if (payload.detail) detail = payload.detail
    } catch {
      // Preserve the HTTP status for non-JSON failures.
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export function loadCodexCapabilities(): Promise<CodexCapabilities> {
  return jsonRequest('/api/ai/codex/status')
}

export function openChatConversation(
  symbol: string, sourceRunId: string, forceNew = false,
): Promise<ChatConversation> {
  return jsonRequest('/api/ai/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol, timeframe: 'daily', source_run_id: sourceRunId, force_new: forceNew }),
  })
}

export function listChatConversations(
  symbol: string, sourceRunId: string,
): Promise<{ items: ChatConversationSummary[] }> {
  const query = new URLSearchParams({ symbol, timeframe: 'daily', source_run_id: sourceRunId })
  return jsonRequest(`/api/ai/conversations?${query}`)
}

export function openSignalChatConversation(
  runId: string, forceNew = false,
): Promise<ChatConversation> {
  return jsonRequest('/api/ai/conversations', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ context_kind: 'signal_run', context_id: runId, force_new: forceNew }),
  })
}

export function listSignalChatConversations(
  runId: string,
): Promise<{ items: ChatConversationSummary[] }> {
  const query = new URLSearchParams({ context_kind: 'signal_run', context_id: runId })
  return jsonRequest(`/api/ai/conversations?${query}`)
}

export function updateChatConversation(
  conversationId: string, update: { title?: string; status?: 'active' | 'archived' },
): Promise<ChatConversation> {
  return jsonRequest(`/api/ai/conversations/${encodeURIComponent(conversationId)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  })
}

export async function deleteChatConversation(conversationId: string): Promise<void> {
  const response = await fetch(`/api/ai/conversations/${encodeURIComponent(conversationId)}`, {
    method: 'DELETE',
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
}

export function loadChatConversation(conversationId: string): Promise<ChatConversation> {
  return jsonRequest(`/api/ai/conversations/${encodeURIComponent(conversationId)}`)
}

export function startChatTurn(
  conversationId: string, content: string, templateId?: string,
  userInputs?: {
    position?: { direction: 'long' | 'short'; entry_price: number; stop_price?: number; target_price?: number }
    risk_reward?: { direction: 'long' | 'short'; entry_price: number; stop_price: number; target_price: number }
  },
  selectedSignalItemIds?: string[],
): Promise<{ turn_id: string; status: string }> {
  return jsonRequest(`/api/ai/conversations/${encodeURIComponent(conversationId)}/turns`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, template_id: templateId || null, ...userInputs,
      selected_signal_item_ids: selectedSignalItemIds ?? [] }),
  })
}

export async function cancelChatTurn(turnId: string): Promise<void> {
  await jsonRequest(`/api/ai/turns/${encodeURIComponent(turnId)}/cancel`, { method: 'POST' })
}

export function retryChatTurn(turnId: string): Promise<{ turn_id: string; status: string }> {
  return jsonRequest(`/api/ai/turns/${encodeURIComponent(turnId)}/retry`, { method: 'POST' })
}

export function loadChatTurnContext(turnId: string): Promise<ChatTurnContextSummary> {
  return jsonRequest(`/api/ai/turns/${encodeURIComponent(turnId)}/context`)
}

export function streamChatTurn(
  turnId: string,
  handlers: {
    onDelta: (delta: string) => void
    onTerminal: (type: 'completed' | 'failed' | 'cancelled', data: Record<string, unknown>) => void
    onError: () => void
    onTool?: (type: 'tool-started' | 'tool-completed', data: ChatToolEvent) => void
  },
): EventSource {
  const source = new EventSource(`/api/ai/turns/${encodeURIComponent(turnId)}/events`)
  source.addEventListener('delta', event => {
    const payload = JSON.parse((event as MessageEvent).data) as { delta?: string }
    if (payload.delta) handlers.onDelta(payload.delta)
  })
  for (const type of ['tool-started', 'tool-completed'] as const) {
    source.addEventListener(type, event => {
      handlers.onTool?.(
        type,
        JSON.parse((event as MessageEvent).data) as ChatToolEvent,
      )
    })
  }
  for (const type of ['completed', 'failed', 'cancelled'] as const) {
    source.addEventListener(type, event => {
      handlers.onTerminal(type, JSON.parse((event as MessageEvent).data) as Record<string, unknown>)
      source.close()
    })
  }
  source.onerror = () => handlers.onError()
  return source
}

export type ChatToolEvent = {
  item_id: string
  server: string
  tool: string
  status: string
  duration_ms?: number | null
  arguments?: Record<string, string | number | boolean>
  error?: string | null
}
