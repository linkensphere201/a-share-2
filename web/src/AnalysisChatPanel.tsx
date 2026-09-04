import { useEffect, useMemo, useRef, useState } from 'react'
import { Archive, Bot, Database, Pencil, PanelRightClose, Plus, RotateCcw, Send, ShieldCheck, Square, Trash2, X } from 'lucide-react'
import {
  cancelChatTurn,
  deleteChatConversation,
  listChatConversations,
  loadChatTurnContext,
  loadChatConversation,
  loadCodexCapabilities,
  openChatConversation,
  retryChatTurn,
  startChatTurn,
  streamChatTurn,
  type ChatConversation,
  type ChatConversationSummary,
  type ChatTurnContextSummary,
  type CodexCapabilities,
  updateChatConversation,
} from './aiChatClient'
import type { TrendAnalysisRun } from './trendAnalysisClient'
import { MarkdownPreview } from './MarkdownPreview'

type Props = {
  symbol: string
  run: TrendAnalysisRun
  onHighlightItemChange: (itemId?: string) => void
  onCollapse: () => void
}

export function AnalysisChatPanel({ symbol, run, onHighlightItemChange, onCollapse }: Props) {
  const [capabilities, setCapabilities] = useState<CodexCapabilities | null>(null)
  const [conversation, setConversation] = useState<ChatConversation | null>(null)
  const [conversations, setConversations] = useState<ChatConversationSummary[]>([])
  const [templateId, setTemplateId] = useState<string>()
  const [draft, setDraft] = useState('')
  const [tradeInputs, setTradeInputs] = useState({
    direction: 'long' as 'long' | 'short', entry: '', stop: '', target: '',
  })
  const [liveResponse, setLiveResponse] = useState('')
  const [pendingPrompt, setPendingPrompt] = useState<string>()
  const [activeTurnId, setActiveTurnId] = useState<string>()
  const [error, setError] = useState<string>()
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false)
  const [contextDetail, setContextDetail] = useState<ChatTurnContextSummary>()
  const streamRef = useRef<EventSource | null>(null)
  const deltaBufferRef = useRef('')
  const flushTimerRef = useRef<number | undefined>(undefined)
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const referenceMap = useMemo(() => buildReferenceMap(run), [run])

  useEffect(() => {
    let cancelled = false
    setConversation(null)
    setError(undefined)
    setLiveResponse('')
    setPendingPrompt(undefined)
    setActiveTurnId(undefined)
    Promise.all([
      loadCodexCapabilities(), openChatConversation(symbol, run.run_id),
    ]).then(async ([nextCapabilities, nextConversation]) => {
        const history = await listChatConversations(symbol, run.run_id)
        if (!cancelled) {
          setCapabilities(nextCapabilities)
          setConversation(nextConversation)
          setConversations(history.items)
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

  useEffect(() => {
    const element = messagesRef.current
    if (!element || typeof element.scrollTo !== 'function') return
    element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' })
  }, [activeTurnId, conversation?.turns.length, liveResponse, pendingPrompt])

  const refreshConversations = async (selectedId?: string) => {
    const history = await listChatConversations(symbol, run.run_id)
    setConversations(history.items)
    if (selectedId) setConversation(await loadChatConversation(selectedId))
  }

  const newConversation = async () => {
    if (activeTurnId) return
    setError(undefined)
    try {
      const created = await openChatConversation(symbol, run.run_id, true)
      setConversation(created)
      await refreshConversations()
    } catch (reason) { setError(String(reason)) }
  }

  const renameConversation = async () => {
    if (!conversation) return
    const title = window.prompt('会话名称', conversation.title)
    if (!title?.trim()) return
    try {
      setConversation(await updateChatConversation(conversation.conversation_id, { title }))
      await refreshConversations()
    } catch (reason) { setError(String(reason)) }
  }

  const archiveConversation = async () => {
    if (!conversation || activeTurnId) return
    try {
      await updateChatConversation(conversation.conversation_id, { status: 'archived' })
      const replacement = await openChatConversation(symbol, run.run_id)
      setConversation(replacement)
      await refreshConversations()
    } catch (reason) { setError(String(reason)) }
  }

  const removeConversation = async () => {
    if (!conversation || activeTurnId || !window.confirm(`删除会话“${conversation.title}”及其消息？分析结果不会被删除。`)) return
    try {
      await deleteChatConversation(conversation.conversation_id)
      const replacement = await openChatConversation(symbol, run.run_id)
      setConversation(replacement)
      await refreshConversations()
    } catch (reason) { setError(String(reason)) }
  }

  const retryTurn = async (turnId: string) => {
    if (activeTurnId) return
    try {
      const turn = await retryChatTurn(turnId)
      setLiveResponse('')
      setActiveTurnId(turn.turn_id)
      connectStream(turn.turn_id)
    } catch (reason) { setError(String(reason)) }
  }

  const connectStream = (turnId: string) => {
    streamRef.current?.close()
    streamRef.current = streamChatTurn(turnId, {
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
        if (!conversation) {
          setPendingPrompt(undefined)
          return
        }
        try {
          setConversation(await loadChatConversation(conversation.conversation_id))
          setLiveResponse('')
          await refreshConversations()
        } catch (reason) { setError(String(reason)) }
        finally { setPendingPrompt(undefined) }
      },
      onError: () => setError('Codex 流式连接中断，已保留服务器端结果。'),
    })
  }

  const send = async () => {
    const content = draft.trim()
    if (!conversation || !content || activeTurnId || pendingPrompt || !validateTradeInputs(templateId, tradeInputs)) return
    setError(undefined)
    setLiveResponse('')
    setPendingPrompt(content)
    setDraft('')
    try {
      const turn = await startChatTurn(
        conversation.conversation_id, content, templateId,
        buildTradeInputs(templateId, tradeInputs),
      )
      setActiveTurnId(turn.turn_id)
      connectStream(turn.turn_id)
    } catch (reason) {
      setPendingPrompt(undefined)
      setDraft(content)
      setError(String(reason))
    }
  }

  const available = Boolean(capabilities?.codex.available && capabilities.codex.authenticated)
  const canSend = available && conversation?.status === 'active'
  const tradeInputsValid = validateTradeInputs(templateId, tradeInputs)
  return <section className="analysis-chat-pane" aria-label="Codex形态分析对话">
    <header>
      <span><Bot size={13}/>Codex 对话</span>
      <div className="analysis-chat-actions">
        <small className={available ? 'available' : 'unavailable'}>{capabilities === null ? '检测中' : available ? '已连接' : '不可用'}</small>
        <button title="新建会话" aria-label="新建会话" onClick={() => void newConversation()}><Plus size={12}/></button>
        <button title="重命名会话" aria-label="重命名会话" onClick={() => void renameConversation()}><Pencil size={11}/></button>
        <button title="归档会话" aria-label="归档会话" onClick={() => void archiveConversation()}><Archive size={11}/></button>
        <button title="删除会话" aria-label="删除会话" onClick={() => void removeConversation()}><Trash2 size={11}/></button>
        <button title="Codex诊断" aria-label="Codex诊断" onClick={() => setDiagnosticsOpen(value => !value)}><ShieldCheck size={11}/></button>
        <button title="收起Codex对话" aria-label="收起Codex对话" onClick={onCollapse}><PanelRightClose size={11}/></button>
      </div>
    </header>
    {diagnosticsOpen && capabilities && <CodexDiagnostics capabilities={capabilities}/ >}
    <div className="analysis-chat-context">
      <select aria-label="会话历史" value={conversation?.conversation_id ?? ''} onChange={event => void refreshConversations(event.target.value)}>
        {conversations.map(item => <option key={item.conversation_id} value={item.conversation_id}>{item.status === 'archived' ? '[归档] ' : ''}{item.title} · {item.turn_count}</option>)}
      </select>
      <span>{run.expires_at_ms ? '盘中预览' : '正式结果'} · {run.as_of_date}</span>
      <small>{run.algorithm_version ?? conversation?.algorithm_version ?? '算法版本未知'} · {run.stale ? '结果已过期' : '快照有效'} · 结果固定到本轮分析</small>
    </div>
    <div className="analysis-chat-messages" ref={messagesRef} aria-live="polite">
      {conversation?.turns.flatMap(turn => turn.messages.map(message =>
        <article key={message.message_id} className={`analysis-chat-message ${message.role}`}>
          <small>{message.role === 'user' ? '你' : 'Codex'}{message.incomplete ? ' · 未完成' : ''}{message.role === 'assistant' && <button title="查看本轮证据快照" aria-label="查看本轮证据快照" onClick={() => void loadChatTurnContext(turn.turn_id).then(setContextDetail).catch(reason => setError(String(reason)))}><Database size={10}/></button>}{message.role === 'assistant' && turn.status !== 'running' && <button title="按相同上下文重试" aria-label="按相同上下文重试" onClick={() => void retryTurn(turn.turn_id)}><RotateCcw size={10}/></button>}</small>
          <ChatMarkdown text={message.content} references={referenceMap} onHighlight={onHighlightItemChange}/>
        </article>,
      ))}
      {pendingPrompt && <article className="analysis-chat-message user pending">
        <small>你 · 已发送</small>
        <ChatMarkdown text={pendingPrompt} references={referenceMap} onHighlight={onHighlightItemChange}/>
      </article>}
      {liveResponse && <article className="analysis-chat-message assistant streaming">
        <small>Codex · 生成中</small>
        <ChatMarkdown text={liveResponse} references={referenceMap} onHighlight={onHighlightItemChange}/>
      </article>}
      {(activeTurnId || pendingPrompt) && !liveResponse && <article className="analysis-chat-message assistant working" role="status">
        <small>Codex</small>
        <div className="analysis-chat-working"><span>Working</span><i/><i/><i/></div>
      </article>}
      {!conversation && !error && <p className="analysis-chat-empty">正在建立结果绑定会话…</p>}
      {conversation?.turns.length === 0 && !pendingPrompt && !liveResponse && <p className="analysis-chat-empty">选择模板或直接提问。</p>}
      {error && <p className="analysis-chat-error">{error}</p>}
    </div>
    {contextDetail && <ContextDetail value={contextDetail} onClose={() => setContextDetail(undefined)}/>}
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
        {(templateId === 'position-tracking' || templateId === 'risk-reward') && <div className="analysis-chat-trade-inputs">
          <select aria-label="方向" value={tradeInputs.direction} onChange={event => setTradeInputs(value => ({ ...value, direction: event.target.value as 'long' | 'short' }))}><option value="long">多头</option><option value="short">空头</option></select>
          <input aria-label="入场价" inputMode="decimal" placeholder="入场价" value={tradeInputs.entry} onChange={event => setTradeInputs(value => ({ ...value, entry: event.target.value }))}/>
          <input aria-label="止损价" inputMode="decimal" placeholder={templateId === 'risk-reward' ? '止损价*' : '止损价'} value={tradeInputs.stop} onChange={event => setTradeInputs(value => ({ ...value, stop: event.target.value }))}/>
          <input aria-label="目标价" inputMode="decimal" placeholder={templateId === 'risk-reward' ? '目标价*' : '目标价'} value={tradeInputs.target} onChange={event => setTradeInputs(value => ({ ...value, target: event.target.value }))}/>
        </div>}
        <textarea
          value={draft}
          placeholder={conversation?.status === 'archived' ? '归档会话只读' : available ? '就本轮形态结果继续分析…' : capabilities?.codex.error ?? 'Codex不可用'}
          disabled={!canSend || Boolean(activeTurnId) || Boolean(pendingPrompt)}
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
          : <button title="发送" aria-label="发送" disabled={!canSend || !draft.trim() || !tradeInputsValid || Boolean(pendingPrompt)} onClick={() => void send()}><Send size={13}/></button>}
      </div>
    </div>
  </section>
}

type TradeInputs = { direction: 'long' | 'short'; entry: string; stop: string; target: string }

function validateTradeInputs(templateId: string | undefined, value: TradeInputs): boolean {
  if (templateId !== 'position-tracking' && templateId !== 'risk-reward') return true
  const entry = Number(value.entry)
  if (!(entry > 0)) return false
  if (templateId === 'position-tracking') return true
  const stop = Number(value.stop)
  const target = Number(value.target)
  return value.direction === 'long'
    ? stop > 0 && stop < entry && target > entry
    : target > 0 && target < entry && stop > entry
}

function buildTradeInputs(templateId: string | undefined, value: TradeInputs) {
  if (templateId !== 'position-tracking' && templateId !== 'risk-reward') return undefined
  const base = { direction: value.direction, entry_price: Number(value.entry) }
  if (templateId === 'risk-reward') return { risk_reward: {
    ...base, stop_price: Number(value.stop), target_price: Number(value.target),
  }}
  return { position: {
    ...base,
    ...(Number(value.stop) > 0 ? { stop_price: Number(value.stop) } : {}),
    ...(Number(value.target) > 0 ? { target_price: Number(value.target) } : {}),
  }}
}

function ContextDetail({ value, onClose }: { value: ChatTurnContextSummary; onClose: () => void }) {
  return <div className="chat-context-detail" role="dialog" aria-label="本轮证据快照">
    <header><span>本轮证据快照</span><button title="关闭证据快照" aria-label="关闭证据快照" onClick={onClose}><X size={11}/></button></header>
    <dl>
      <dt>截至</dt><dd>{value.as_of_date} · {value.preview ? '盘中预览' : '正式结果'}</dd>
      <dt>输入范围</dt><dd>{value.input_start_date} 至 {value.input_end_date}</dd>
      <dt>算法</dt><dd>{value.algorithm_version} / {value.config_version}</dd>
      <dt>数据源</dt><dd>{value.sources.join('、') || '未记录'}</dd>
      <dt>价格口径</dt><dd>{value.price_basis ?? '未记录'} / {value.volume_semantics ?? '未记录'}</dd>
      <dt>证据</dt><dd>{value.evidence_codes.join('、') || '无'}{value.truncated ? ' · 已截断' : ''}</dd>
      <dt>指纹</dt><dd title={value.input_digest}>{value.input_digest.slice(0, 16)}…</dd>
      <dt>状态</dt><dd>{value.stale ? `已过期：${value.stale_reasons.join('、')}` : value.completion_state}</dd>
      <dt>请求</dt><dd>{value.tool_request_id ?? '本地快照，无工具请求'}</dd>
    </dl>
  </div>
}

function CodexDiagnostics({ capabilities }: { capabilities: CodexCapabilities }) {
  const value = capabilities.codex
  return <div className="codex-diagnostics" role="status">
    <span>Codex {value.version ?? 'unknown'}</span>
    <dl>
      <dt>进程</dt><dd>{value.process_running ? '运行中' : '未运行'}</dd>
      <dt>传输</dt><dd>{value.transport ?? '未知'}</dd>
      <dt>沙箱</dt><dd>{value.sandbox ?? '未知'}</dd>
      <dt>审批</dt><dd>{value.approval_policy ?? '未知'}</dd>
      <dt>MCP</dt><dd>{value.mcp_enabled ? '启用' : '禁用'}</dd>
      <dt>工具熔断</dt><dd>{value.tool_event_tripwire ? '启用' : '禁用'}</dd>
      <dt>重启</dt><dd>{value.restart_count ?? 0}</dd>
    </dl>
  </div>
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

function ChatMarkdown({
  text, references, onHighlight,
}: {
  text: string
  references: Map<string, string>
  onHighlight: (itemId?: string) => void
}) {
  return <MarkdownPreview
    content={text}
    className="analysis-chat-markdown"
    renderText={(value, keyPrefix) => <ReferenceText
      key={keyPrefix}
      text={value}
      references={references}
      onHighlight={onHighlight}
    />}
  />
}

function ReferenceText({
  text, references, onHighlight,
}: {
  text: string
  references: Map<string, string>
  onHighlight: (itemId?: string) => void
}) {
  const parts = text.split(/(\[[KLP]\d+\])/g)
  return <>{parts.map((part, index) => {
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
  })}</>
}
