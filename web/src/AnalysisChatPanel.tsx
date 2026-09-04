import { useEffect, useMemo, useRef, useState } from 'react'
import { Bot, Send, Square } from 'lucide-react'
import {
  cancelChatTurn,
  loadChatConversation,
  loadCodexCapabilities,
  openChatConversation,
  startChatTurn,
  streamChatTurn,
  type ChatConversation,
  type CodexCapabilities,
} from './aiChatClient'
import type { TrendAnalysisRun } from './trendAnalysisClient'

type Props = {
  symbol: string
  run: TrendAnalysisRun
  onHighlightItemChange: (itemId?: string) => void
}

export function AnalysisChatPanel({ symbol, run, onHighlightItemChange }: Props) {
  const [capabilities, setCapabilities] = useState<CodexCapabilities | null>(null)
  const [conversation, setConversation] = useState<ChatConversation | null>(null)
  const [templateId, setTemplateId] = useState<string>()
  const [draft, setDraft] = useState('')
  const [liveResponse, setLiveResponse] = useState('')
  const [activeTurnId, setActiveTurnId] = useState<string>()
  const [error, setError] = useState<string>()
  const streamRef = useRef<EventSource | null>(null)
  const deltaBufferRef = useRef('')
  const flushTimerRef = useRef<number | undefined>(undefined)
  const referenceMap = useMemo(() => buildReferenceMap(run), [run])

  useEffect(() => {
    let cancelled = false
    setConversation(null)
    setError(undefined)
    setLiveResponse('')
    setActiveTurnId(undefined)
    Promise.all([loadCodexCapabilities(), openChatConversation(symbol, run.run_id)])
      .then(([nextCapabilities, nextConversation]) => {
        if (!cancelled) {
          setCapabilities(nextCapabilities)
          setConversation(nextConversation)
        }
      })
      .catch(reason => { if (!cancelled) setError(String(reason)) })
    return () => {
      cancelled = true
      streamRef.current?.close()
      streamRef.current = null
      if (flushTimerRef.current !== undefined) window.clearTimeout(flushTimerRef.current)
    }
  }, [run.run_id, symbol])

  const send = async () => {
    const content = draft.trim()
    if (!conversation || !content || activeTurnId) return
    setError(undefined)
    setLiveResponse('')
    try {
      const turn = await startChatTurn(conversation.conversation_id, content, templateId)
      setDraft('')
      setActiveTurnId(turn.turn_id)
      streamRef.current = streamChatTurn(turn.turn_id, {
        onDelta: delta => {
          deltaBufferRef.current += delta
          if (flushTimerRef.current !== undefined) return
          flushTimerRef.current = window.setTimeout(() => {
            const buffered = deltaBufferRef.current
            deltaBufferRef.current = ''
            flushTimerRef.current = undefined
            setLiveResponse(value => value + buffered)
          }, 40)
        },
        onTerminal: async () => {
          if (flushTimerRef.current !== undefined) window.clearTimeout(flushTimerRef.current)
          flushTimerRef.current = undefined
          deltaBufferRef.current = ''
          setActiveTurnId(undefined)
          try {
            setConversation(await loadChatConversation(conversation.conversation_id))
            setLiveResponse('')
          } catch (reason) {
            setError(String(reason))
          }
        },
        onError: () => setError('Codex 流式连接中断，已保留服务器端结果。'),
      })
    } catch (reason) {
      setError(String(reason))
    }
  }

  const available = Boolean(capabilities?.codex.available && capabilities.codex.authenticated)
  return <section className="analysis-chat-pane" aria-label="Codex形态分析对话">
    <header>
      <span><Bot size={13}/>Codex 对话</span>
      <small className={available ? 'available' : 'unavailable'}>
        {capabilities === null ? '检测中' : available ? '已连接' : '不可用'}
      </small>
    </header>
    <div className="analysis-chat-context">
      <span>{run.expires_at_ms ? '盘中预览' : '正式结果'} · {run.as_of_date}</span>
      <small>结果已固定到本轮分析，更新测算不会改写当前对话</small>
    </div>
    <div className="analysis-chat-messages">
      {conversation?.turns.flatMap(turn => turn.messages.map(message =>
        <article key={message.message_id} className={`analysis-chat-message ${message.role}`}>
          <small>{message.role === 'user' ? '你' : 'Codex'}{message.incomplete ? ' · 未完成' : ''}</small>
          <ReferenceText text={message.content} references={referenceMap} onHighlight={onHighlightItemChange}/>
        </article>,
      ))}
      {liveResponse && <article className="analysis-chat-message assistant streaming">
        <small>Codex · 生成中</small>
        <ReferenceText text={liveResponse} references={referenceMap} onHighlight={onHighlightItemChange}/>
      </article>}
      {!conversation && !error && <p className="analysis-chat-empty">正在建立结果绑定会话…</p>}
      {conversation?.turns.length === 0 && !liveResponse && <p className="analysis-chat-empty">选择模板或直接提问。</p>}
      {error && <p className="analysis-chat-error">{error}</p>}
    </div>
    <div className="analysis-chat-compose">
      <div className="analysis-chat-templates">
        {capabilities?.templates.map(template => <button
          key={template.id}
          className={templateId === template.id ? 'active' : ''}
          title={template.instruction}
          onClick={() => setTemplateId(value => value === template.id ? undefined : template.id)}
        >{template.label}</button>)}
      </div>
      <div className="analysis-chat-input">
        <textarea
          value={draft}
          placeholder={available ? '就本轮形态结果继续分析…' : capabilities?.codex.error ?? 'Codex不可用'}
          disabled={!available || !conversation || Boolean(activeTurnId)}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              void send()
            }
          }}
        />
        {activeTurnId
          ? <button title="停止生成" aria-label="停止生成" onClick={() => void cancelChatTurn(activeTurnId)}><Square size={13}/></button>
          : <button title="发送" aria-label="发送" disabled={!available || !draft.trim()} onClick={() => void send()}><Send size={13}/></button>}
      </div>
    </div>
  </section>
}

function buildReferenceMap(run: TrendAnalysisRun): Map<string, string> {
  const counts = { zone: 0, line: 0, pattern: 0 }
  const prefix = { zone: 'K', line: 'L', pattern: 'P' }
  const result = new Map<string, string>()
  for (const item of run.items) {
    if (!(item.item_type in counts)) continue
    const kind = item.item_type as keyof typeof counts
    counts[kind] += 1
    result.set(`${prefix[kind]}${counts[kind]}`, item.item_id)
  }
  return result
}

function ReferenceText({
  text, references, onHighlight,
}: {
  text: string
  references: Map<string, string>
  onHighlight: (itemId?: string) => void
}) {
  const parts = text.split(/(\[[KLP]\d+\])/g)
  return <p>{parts.map((part, index) => {
    const code = /^\[([KLP]\d+)\]$/.exec(part)?.[1]
    const itemId = code ? references.get(code) : undefined
    return itemId ? <button
      key={`${part}-${index}`}
      className="analysis-chat-reference"
      onPointerEnter={() => onHighlight(itemId)}
      onPointerLeave={() => onHighlight(undefined)}
      onFocus={() => onHighlight(itemId)}
      onBlur={() => onHighlight(undefined)}
    >{part}</button> : <span key={`${index}-${part.slice(0, 8)}`}>{part}</span>
  })}</p>
}
