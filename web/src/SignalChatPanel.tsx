import { useEffect, useMemo, useRef, useState } from 'react'
import { Bot, PanelRightClose, Plus, Send, Trash2 } from 'lucide-react'
import { ChatMarkdown } from './AnalysisChatPanel'
import {
  deleteChatConversation, listSignalChatConversations, loadChatConversation, loadCodexCapabilities,
  openSignalChatConversation, startChatTurn, streamChatTurn,
  type ChatConversation, type ChatConversationSummary, type CodexCapabilities,
} from './aiChatClient'
import type { ObservationPoolItem, SignalItem, SignalRun } from './signalReviewClient'

export function SignalChatPanel({
  run, items, selectedItem, selectedPoolItem, onReferencePreview, onReferenceActivate, onClose,
}: {
  run: SignalRun
  items: SignalItem[]
  selectedItem?: SignalItem
  selectedPoolItem?: ObservationPoolItem
  onReferencePreview: (itemId?: string, evidenceId?: string) => void
  onReferenceActivate: (itemId: string, evidenceId: string) => void
  onClose: () => void
}) {
  const [capabilities, setCapabilities] = useState<CodexCapabilities>()
  const [conversation, setConversation] = useState<ChatConversation>()
  const [history, setHistory] = useState<ChatConversationSummary[]>([])
  const [templateId, setTemplateId] = useState<string>()
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<string>()
  const [live, setLive] = useState('')
  const [activeTurnId, setActiveTurnId] = useState<string>()
  const [error, setError] = useState('')
  const streamRef = useRef<EventSource | null>(null)
  const runIdRef = useRef(run.run_id)
  runIdRef.current = run.run_id
  const referenceMap = useMemo(
    () => buildSignalReferenceMap(items, selectedPoolItem),
    [items, selectedPoolItem],
  )
  const previewReference = (reference?: string) => {
    if (!reference) { onReferencePreview(); return }
    const [itemId, evidenceId] = reference.split('\u0000', 2)
    onReferencePreview(itemId, evidenceId)
  }
  const activateReference = (reference: string) => {
    const [itemId, evidenceId] = reference.split('\u0000', 2)
    onReferenceActivate(itemId, evidenceId)
  }

  const refreshHistory = async (
    conversationId?: string, expectedRunId = run.run_id,
  ) => {
    const value = await listSignalChatConversations(expectedRunId)
    if (runIdRef.current !== expectedRunId) return
    setHistory(value.items)
    if (conversationId) {
      const loaded = await loadChatConversation(conversationId)
      if (runIdRef.current === expectedRunId) setConversation(loaded)
    }
  }

  useEffect(() => {
    let cancelled = false
    streamRef.current?.close()
    streamRef.current = null
    setConversation(undefined)
    setHistory([])
    setPending(undefined)
    setLive('')
    setActiveTurnId(undefined)
    setError('')
    Promise.all([loadCodexCapabilities(), openSignalChatConversation(run.run_id)])
      .then(async ([nextCapabilities, nextConversation]) => {
        if (cancelled) return
        setCapabilities(nextCapabilities)
        setConversation(nextConversation)
        setHistory((await listSignalChatConversations(run.run_id)).items)
      }).catch(reason => { if (!cancelled) setError(String(reason)) })
    return () => { cancelled = true; streamRef.current?.close() }
  }, [run.run_id])

  const connect = (turnId: string, conversationId: string, expectedRunId: string) => {
    streamRef.current?.close()
    setActiveTurnId(turnId)
    streamRef.current = streamChatTurn(turnId, {
      onDelta: delta => setLive(value => value + delta),
      onTerminal: async (type, data) => {
        if (runIdRef.current !== expectedRunId) return
        setActiveTurnId(undefined)
        setPending(undefined)
        setLive('')
        if (type === 'failed') {
          const message = typeof data.message === 'string' && data.message.trim()
            ? data.message.trim()
            : 'Codex 对话失败，请查看应用日志。'
          setError(message)
        }
        await refreshHistory(conversationId, expectedRunId)
      },
      onError: () => setError('Codex 流式连接中断，服务端结果已保留。'),
    })
  }

  const send = async () => {
    const content = draft.trim()
    if (!conversation || conversation.context_id !== run.run_id || !content || activeTurnId) return
    setDraft('')
    setPending(content)
    setLive('')
    setError('')
    try {
      const turn = await startChatTurn(
        conversation.conversation_id, content, templateId, undefined,
        selectedItem
          ? [selectedItem.item_id]
          : selectedPoolItem
            ? [`pool:${selectedPoolItem.kind === 'sector' ? 'board' : 'stock'}:${selectedPoolItem.symbol}`]
            : [],
      )
      connect(turn.turn_id, conversation.conversation_id, run.run_id)
    } catch (reason) {
      setDraft(content)
      setPending(undefined)
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const removeConversation = async () => {
    if (!conversation || activeTurnId) return
    if (!window.confirm(`删除会话“${conversation.title}”及其全部消息？复盘结果不会被删除。`)) return
    try {
      setError('')
      await deleteChatConversation(conversation.conversation_id)
      const remaining = (await listSignalChatConversations(run.run_id)).items
      setHistory(remaining)
      if (remaining[0]) setConversation(await loadChatConversation(remaining[0].conversation_id))
      else {
        const created = await openSignalChatConversation(run.run_id, true)
        setConversation(created)
        setHistory((await listSignalChatConversations(run.run_id)).items)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const available = Boolean(capabilities?.codex.available && capabilities.codex.authenticated)
  return <section className="signal-chat-pane">
    <header><span><Bot size={13}/>Codex 信号讨论</span><div>
      <small className={available ? 'available' : 'unavailable'}>{available ? '已连接' : '不可用'}</small>
      <button title="新建会话" aria-label="新建信号会话" onClick={() => void openSignalChatConversation(run.run_id, true).then(value => { setConversation(value); void refreshHistory() })}><Plus size={12}/></button>
      <button title="删除当前会话" aria-label="删除当前信号会话" disabled={!conversation || Boolean(activeTurnId)} onClick={() => void removeConversation()}><Trash2 size={11}/></button>
      <button title="关闭对话" aria-label="关闭信号对话" onClick={onClose}><PanelRightClose size={12}/></button>
    </div></header>
    <div className="signal-chat-context">
      <select aria-label="信号会话历史" value={conversation?.conversation_id ?? ''} onChange={event => void refreshHistory(event.target.value)}>
        {history.map(item => <option key={item.conversation_id} value={item.conversation_id}>{item.title} · {item.turn_count}</option>)}
      </select>
      <small>{run.effective_date} R{run.revision} · {selectedItem
        ? `${selectedItem.name} ${selectedItem.symbol}`
        : selectedPoolItem ? `${selectedPoolItem.name} ${selectedPoolItem.symbol}` : '未选择标的'}</small>
    </div>
    <div className="signal-chat-messages">
      {conversation?.turns.flatMap(turn => turn.messages.map(message => <article key={message.message_id} className={`analysis-chat-message ${message.role}`}>
        <small>{message.role === 'user' ? '你' : 'Codex'}</small>
        <ChatMarkdown text={message.content} references={referenceMap} onHighlight={previewReference} onActivate={activateReference}/>
      </article>))}
      {pending && <article className="analysis-chat-message user pending"><small>你 · 已发送</small><ChatMarkdown text={pending} references={referenceMap} onHighlight={previewReference} onActivate={activateReference}/></article>}
      {live && <article className="analysis-chat-message assistant streaming"><small>Codex · 生成中</small><ChatMarkdown text={live} references={referenceMap} onHighlight={previewReference} onActivate={activateReference}/></article>}
      {(pending || activeTurnId) && !live && <div className="analysis-chat-working" role="status"><span>Working</span><i/><i/><i/></div>}
      {error && <p className="analysis-chat-error">{error}</p>}
    </div>
    <div className="signal-chat-compose">
      <div className="signal-chat-templates">{capabilities?.signal_templates?.map(template => <button key={template.id} className={templateId === template.id ? 'active' : ''} onClick={() => setTemplateId(value => value === template.id ? undefined : template.id)}>{template.label}</button>)}</div>
      <div><textarea aria-label="信号讨论输入" value={draft} disabled={!available || Boolean(activeTurnId)} placeholder="就本轮信号结果继续分析..." onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send() } }}/><button title="发送" aria-label="发送信号问题" disabled={!available || !draft.trim() || Boolean(activeTurnId)} onClick={() => void send()}><Send size={13}/></button></div>
    </div>
  </section>
}

export function buildSignalReferenceMap(
  items: SignalItem[], selectedPoolItem?: ObservationPoolItem,
): Map<string, string> {
  const references = new Map<string, string>()
  const ambiguous = new Set<string>()
  for (const item of items) for (const evidence of item.evidence) {
    if (references.has(evidence.alias)) ambiguous.add(evidence.alias)
    else references.set(evidence.alias, `${item.item_id}\u0000${evidence.evidence_id}`)
  }
  for (const alias of ambiguous) references.delete(alias)
  selectedPoolItem?.sources.forEach((source, index) => references.set(
    `O${index + 1}`,
    `pool:${selectedPoolItem.kind === 'sector' ? 'board' : 'stock'}:${selectedPoolItem.symbol}\u0000${source.source_type}:${source.source_entity_key}`,
  ))
  return references
}
