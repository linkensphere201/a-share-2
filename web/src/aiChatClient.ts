export type ChatTemplate = { id: string; label: string; instruction: string }

export type CodexCapabilities = {
  codex: {
    available: boolean
    authenticated: boolean
    version?: string | null
    experimental: boolean
    error?: string | null
  }
  templates: ChatTemplate[]
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
  symbol: string
  timeframe: string
  source_run_id: string
  as_of_date: string
  algorithm_version: string
  config_version: string
  completion_state: string
  preview: boolean
  turns: ChatTurn[]
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
  symbol: string, sourceRunId: string,
): Promise<ChatConversation> {
  return jsonRequest('/api/ai/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol, timeframe: 'daily', source_run_id: sourceRunId }),
  })
}

export function loadChatConversation(conversationId: string): Promise<ChatConversation> {
  return jsonRequest(`/api/ai/conversations/${encodeURIComponent(conversationId)}`)
}

export function startChatTurn(
  conversationId: string, content: string, templateId?: string,
): Promise<{ turn_id: string; status: string }> {
  return jsonRequest(`/api/ai/conversations/${encodeURIComponent(conversationId)}/turns`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, template_id: templateId || null }),
  })
}

export async function cancelChatTurn(turnId: string): Promise<void> {
  await jsonRequest(`/api/ai/turns/${encodeURIComponent(turnId)}/cancel`, { method: 'POST' })
}

export function streamChatTurn(
  turnId: string,
  handlers: {
    onDelta: (delta: string) => void
    onTerminal: (type: 'completed' | 'failed' | 'cancelled', data: Record<string, unknown>) => void
    onError: () => void
  },
): EventSource {
  const source = new EventSource(`/api/ai/turns/${encodeURIComponent(turnId)}/events`)
  source.addEventListener('delta', event => {
    const payload = JSON.parse((event as MessageEvent).data) as { delta?: string }
    if (payload.delta) handlers.onDelta(payload.delta)
  })
  for (const type of ['completed', 'failed', 'cancelled'] as const) {
    source.addEventListener(type, event => {
      handlers.onTerminal(type, JSON.parse((event as MessageEvent).data) as Record<string, unknown>)
      source.close()
    })
  }
  source.onerror = () => handlers.onError()
  return source
}
