import { useEffect, useRef, useState } from 'react'
import { Bot, Plus, Send, Trash2 } from 'lucide-react'
import { ChatMarkdown } from './AnalysisChatPanel'
import {
  deleteChatConversation, listLearningConversations, loadChatConversation,
  loadCodexCapabilities, openLearningConversation, startChatTurn, streamChatTurn,
  type ChatConversation, type ChatConversationSummary, type CodexCapabilities,
} from './aiChatClient'
import type { LearningSystem } from './learningClient'
import { reportLearningWarning } from './learningDiagnostics'

export type LearningPageFocus = { assetPath: string; title: string }

export function LearningChatPanel({
  system, page,
}: { system: LearningSystem; page: LearningPageFocus }) {
  const [capabilities, setCapabilities] = useState<CodexCapabilities>()
  const [conversation, setConversation] = useState<ChatConversation>()
  const [history, setHistory] = useState<ChatConversationSummary[]>([])
  const [templateId, setTemplateId] = useState<string>()
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState('')
  const [live, setLive] = useState('')
  const [activeTurnId, setActiveTurnId] = useState<string>()
  const [error, setError] = useState('')
  const streamRef = useRef<EventSource | null>(null)
  const systemIdRef = useRef(system.system_id)
  systemIdRef.current = system.system_id

  const refresh = async (conversationId?: string, systemId = system.system_id) => {
    const result = await listLearningConversations(systemId)
    if (systemIdRef.current !== systemId) return
    setHistory(result.items)
    if (conversationId) setConversation(await loadChatConversation(conversationId))
  }

  useEffect(() => {
    let cancelled = false
    streamRef.current?.close()
    setConversation(undefined)
    setHistory([])
    setPending('')
    setLive('')
    setActiveTurnId(undefined)
    setError('')
    Promise.all([loadCodexCapabilities(), openLearningConversation(system.system_id)])
      .then(async ([nextCapabilities, nextConversation]) => {
        if (cancelled) return
        const nextHistory = (await listLearningConversations(system.system_id)).items
        if (cancelled || systemIdRef.current !== system.system_id) return
        setCapabilities(nextCapabilities)
        setConversation(nextConversation)
        setHistory(nextHistory)
      })
      .catch(reason => {
        if (cancelled) return
        const message = messageOf(reason)
        setError(message)
        reportLearningWarning(`chat-open:${system.system_id}`, '课程 Codex 会话建立失败', { error: message })
      })
    return () => { cancelled = true; streamRef.current?.close() }
  }, [system.system_id])

  const connect = (turnId: string, conversationId: string, systemId: string) => {
    streamRef.current?.close()
    setActiveTurnId(turnId)
    streamRef.current = streamChatTurn(turnId, {
      onDelta: delta => setLive(value => value + delta),
      onTerminal: async (type, data) => {
        if (systemIdRef.current !== systemId) return
        setActiveTurnId(undefined)
        setPending('')
        setLive('')
        if (type === 'failed') setError(
          typeof data.message === 'string' ? data.message : 'Codex 对话失败，请查看应用日志。',
        )
        try {
          await refresh(conversationId, systemId)
        } catch (reason) {
          const message = messageOf(reason)
          setError(message)
          reportLearningWarning(`chat-refresh:${systemId}`, '课程 Codex 会话刷新失败', { error: message })
        }
      },
      onError: () => {
        setError('Codex 流式连接中断，服务端结果仍会保留。')
        reportLearningWarning(`chat-stream:${systemId}`, '课程 Codex 流式连接中断')
      },
    })
  }

  const send = async () => {
    const content = draft.trim()
    if (!conversation || !content || activeTurnId) return
    setDraft('')
    setPending(content)
    setLive('')
    setError('')
    try {
      const turn = await startChatTurn(
        conversation.conversation_id, content, templateId, undefined,
        undefined, undefined, { assetPath: page.assetPath, title: page.title },
      )
      connect(turn.turn_id, conversation.conversation_id, system.system_id)
    } catch (reason) {
      setDraft(content)
      setPending('')
      const message = messageOf(reason)
      setError(message)
      reportLearningWarning(`chat-send:${system.system_id}`, '课程 Codex 对话发送失败', { error: message })
    }
  }

  const createNew = async () => {
    try {
      const next = await openLearningConversation(system.system_id, true)
      setConversation(next)
      await refresh(undefined, system.system_id)
    } catch (reason) {
      const message = messageOf(reason)
      setError(message)
      reportLearningWarning(`chat-create:${system.system_id}`, '课程 Codex 新建会话失败', { error: message })
    }
  }

  const selectConversation = async (conversationId: string) => {
    try {
      setError('')
      await refresh(conversationId)
    } catch (reason) {
      const message = messageOf(reason)
      setError(message)
      reportLearningWarning(`chat-select:${system.system_id}`, '课程 Codex 历史会话加载失败', { error: message })
    }
  }

  const remove = async () => {
    if (!conversation || activeTurnId) return
    if (!window.confirm(`删除会话“${conversation.title}”及其全部消息？课程内容不会被删除。`)) return
    try {
      await deleteChatConversation(conversation.conversation_id)
      const remaining = (await listLearningConversations(system.system_id)).items
      setHistory(remaining)
      if (remaining[0]) setConversation(await loadChatConversation(remaining[0].conversation_id))
      else setConversation(await openLearningConversation(system.system_id, true))
    } catch (reason) {
      const message = messageOf(reason)
      setError(message)
      reportLearningWarning(`chat-delete:${system.system_id}`, '课程 Codex 会话删除失败', { error: message })
    }
  }

  const available = Boolean(capabilities?.codex.available && capabilities.codex.authenticated)
  return <aside className="learning-chat-pane">
    <header><span><Bot size={14}/>Codex 课程讨论</span><div>
      <small className={available ? 'available' : 'unavailable'}>{available ? '已连接' : '不可用'}</small>
      <button title="新建会话" aria-label="新建课程会话" onClick={() => void createNew()}><Plus size={13}/></button>
      <button title="删除当前会话" aria-label="删除当前课程会话" disabled={!conversation || Boolean(activeTurnId)} onClick={() => void remove()}><Trash2 size={12}/></button>
    </div></header>
    <div className="learning-chat-context">
      <select aria-label="课程会话历史" value={conversation?.conversation_id ?? ''} onChange={event => void selectConversation(event.target.value)}>
        {history.map(item => <option key={item.conversation_id} value={item.conversation_id}>{item.title} · {item.turn_count}</option>)}
      </select>
      <span>{page.title || '课程首页'}</span>
      <small title={page.assetPath}>本轮提问将引用当前页面正文</small>
    </div>
    <div className="learning-chat-messages">
      {conversation?.turns.flatMap(turn => turn.messages.map(message => <article key={message.message_id} className={`analysis-chat-message ${message.role}`}>
        <small>{message.role === 'user' ? '你' : 'Codex'}</small>
        <ChatMarkdown text={message.content} references={new Map()} onHighlight={() => {}}/>
      </article>))}
      {pending && <article className="analysis-chat-message user pending"><small>你 · 已发送</small><ChatMarkdown text={pending} references={new Map()} onHighlight={() => {}}/></article>}
      {live && <article className="analysis-chat-message assistant streaming"><small>Codex · 生成中</small><ChatMarkdown text={live} references={new Map()} onHighlight={() => {}}/></article>}
      {(pending || activeTurnId) && !live && <div className="analysis-chat-working" role="status"><span>Working</span><i/><i/><i/></div>}
      {!conversation && !error && <p className="analysis-chat-empty">正在载入课程会话...</p>}
      {error && <p className="analysis-chat-error">{error}</p>}
    </div>
    <div className="learning-chat-compose">
      <div className="analysis-chat-templates">{capabilities?.learning_templates?.map(template => <button key={template.id} className={templateId === template.id ? 'active' : ''} onClick={() => setTemplateId(value => value === template.id ? undefined : template.id)}>{template.label}</button>)}</div>
      <div className="analysis-chat-input"><textarea aria-label="课程讨论输入" value={draft} disabled={!available || Boolean(activeTurnId)} placeholder="就当前章节提问、提炼规则或讨论实操..." onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send() } }}/><button title="发送" aria-label="发送课程问题" disabled={!available || !draft.trim() || Boolean(activeTurnId)} onClick={() => void send()}><Send size={13}/></button></div>
    </div>
  </aside>
}

function messageOf(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason)
}
