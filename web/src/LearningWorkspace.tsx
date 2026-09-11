import { useEffect, useRef, useState } from 'react'
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react'
import { ArrowLeft, BookOpen, RefreshCw } from 'lucide-react'
import { LearningChatPanel, type LearningPageFocus } from './LearningChatPanel'
import { learningSystemUrl, listLearningSystems, type LearningSystem } from './learningClient'
import { logInfo } from './eventLogger'
import { reportLearningWarning } from './learningDiagnostics'

const CHAT_WIDTH_KEY = 'stock-harness.learning-chat-width.v1'

export function LearningWorkspace({ onClose }: { onClose: () => void }) {
  const [systems, setSystems] = useState<LearningSystem[]>([])
  const [system, setSystem] = useState<LearningSystem>()
  const [page, setPage] = useState<LearningPageFocus>()
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)
  const [chatWidth, setChatWidth] = useState(() => {
    const stored = Number(window.localStorage.getItem(CHAT_WIDTH_KEY))
    return Number.isFinite(stored) ? Math.min(620, Math.max(300, stored)) : 380
  })
  const frameRef = useRef<HTMLIFrameElement>(null)

  useEffect(() => {
    listLearningSystems().then(items => {
      setSystems(items)
      const selected = items.find(item => item.default) ?? items[0]
      setSystem(selected)
      if (selected) setPage({ assetPath: selected.index_path, title: selected.title })
      else setError('尚未发布可用的交易系统课程。')
    }).catch(reason => {
      const message = reason instanceof Error ? reason.message : String(reason)
      setError(message)
      reportLearningWarning('catalog', '交易系统课程目录加载失败', { error: message })
    })
  }, [])

  const syncPage = () => {
    if (!system || !frameRef.current) return
    try {
      const frame = frameRef.current
      const path = decodeURIComponent(frame.contentWindow?.location.pathname ?? '')
        .replace(/^\/learning\//, '')
      const assetPath = path.startsWith(`systems/${system.system_id}/site/`)
        ? path : system.index_path
      const title = frame.contentDocument?.title?.trim() || system.title
      setPage({ assetPath, title })
      logInfo('learning', '课程页面已载入', { system_id: system.system_id, asset_path: assetPath })
    } catch (reason) {
      reportLearningWarning(`page:${system.system_id}`, '课程页面上下文读取失败', {
        system_id: system.system_id,
        error: reason instanceof Error ? reason.message : String(reason),
      })
    }
  }

  const startResize = (event: ReactPointerEvent) => {
    event.preventDefault()
    const startX = event.clientX
    const startWidth = chatWidth
    const move = (next: PointerEvent) => setChatWidth(
      Math.min(620, Math.max(300, startWidth + startX - next.clientX)),
    )
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setChatWidth(value => {
        window.localStorage.setItem(CHAT_WIDTH_KEY, String(Math.round(value)))
        return value
      })
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return <main className="learning-workspace" style={{ '--learning-chat-width': `${chatWidth}px` } as CSSProperties}>
    <header className="learning-toolbar">
      <button className="icon-button" title="返回工作台" aria-label="返回工作台" onClick={onClose}><ArrowLeft size={16}/></button>
      <span><BookOpen size={16}/>交易系统学习</span>
      <select aria-label="选择交易系统课程" value={system?.system_id ?? ''} onChange={event => {
        const next = systems.find(item => item.system_id === event.target.value)
        setSystem(next)
        if (next) setPage({ assetPath: next.index_path, title: next.title })
      }}>
        {systems.map(item => <option key={item.system_id} value={item.system_id}>{item.title}</option>)}
      </select>
      {system && <small>{system.methodology} · {system.publication_version}</small>}
      <button className="icon-button" title="刷新课程页面" aria-label="刷新课程页面" disabled={!system} onClick={() => setReloadKey(value => value + 1)}><RefreshCw size={14}/></button>
    </header>
    {error ? <section className="learning-unavailable"><BookOpen size={28}/><span>{error}</span></section> : system && page ? <section className="learning-body">
      <iframe key={`${system.system_id}:${reloadKey}`} ref={frameRef} title={system.title} src={learningSystemUrl(system)} onLoad={syncPage}/>
      <div className="learning-resizer" role="separator" aria-orientation="vertical" aria-label="调整课程对话栏宽度" onPointerDown={startResize}/>
      <LearningChatPanel key={system.system_id} system={system} page={page}/>
    </section> : <section className="learning-unavailable"><span>正在加载课程目录...</span></section>}
  </main>
}
