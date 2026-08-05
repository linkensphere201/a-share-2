import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { Edit3, Minus, NotebookPen, Save, StickyNote, X } from 'lucide-react'
import { loadDailyNote, loadDailyNoteTop, saveDailyNote, saveDailyNoteTop } from './dailyNoteStore'
import { MarkdownPreview } from './MarkdownPreview'

export function DailyNote() {
  const [content, setContent] = useState(loadDailyNote)
  const [draft, setDraft] = useState(content)
  const [editing, setEditing] = useState(false)
  const [minimized, setMinimized] = useState(false)
  const [minimizedTop, setMinimizedTop] = useState(() => clamp(loadDailyNoteTop(), 8, window.innerHeight - 48))
  const [minimizedLeft, setMinimizedLeft] = useState(10)
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number; startTop: number; currentTop: number; moved: boolean } | undefined>(undefined)
  const suppressExpandRef = useRef(false)

  useEffect(() => {
    const keepLauncherVisible = () => setMinimizedTop(current => clamp(current, 8, window.innerHeight - 48))
    window.addEventListener('resize', keepLauncherVisible)
    return () => window.removeEventListener('resize', keepLauncherVisible)
  }, [])

  const handlePointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return
    event.currentTarget.setPointerCapture?.(event.pointerId)
    dragRef.current = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, startTop: minimizedTop, currentTop: minimizedTop, moved: false }
  }

  const handlePointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    const deltaX = event.clientX - drag.startX
    const deltaY = event.clientY - drag.startY
    if (!drag.moved && Math.hypot(deltaX, deltaY) < 4) return
    drag.moved = true
    setDragging(true)
    const nextTop = clamp(drag.startTop + deltaY, 8, window.innerHeight - 48)
    drag.currentTop = nextTop
    setMinimizedLeft(clamp(10 + deltaX, 0, window.innerWidth - 40))
    setMinimizedTop(nextTop)
  }

  const finishDrag = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    event.currentTarget.releasePointerCapture?.(event.pointerId)
    dragRef.current = undefined
    suppressExpandRef.current = drag.moved
    setDragging(false)
    setMinimizedLeft(10)
    if (drag.moved) saveDailyNoteTop(drag.currentTop)
  }

  if (minimized) {
    return (
      <button
        className={`daily-note-minimized${dragging ? ' dragging' : ''}`}
        style={{ top: minimizedTop, left: minimizedLeft }}
        title="展开或拖动每日便签"
        aria-label="展开每日便签"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishDrag}
        onPointerCancel={finishDrag}
        onClick={() => {
          if (suppressExpandRef.current) suppressExpandRef.current = false
          else setMinimized(false)
        }}
      >
        <NotebookPen size={18}/>
      </button>
    )
  }

  return (
    <aside className="daily-note" aria-label="每日便签">
      <header>
        <span className="daily-note-title"><StickyNote size={14}/><strong>每日便签</strong></span>
        <div className="daily-note-actions">
          {!editing && <button title="编辑" aria-label="编辑每日便签" onClick={() => { setDraft(content); setEditing(true) }}><Edit3 size={13}/></button>}
          <button title="最小化" aria-label="最小化每日便签" onClick={() => setMinimized(true)}><Minus size={13}/></button>
        </div>
      </header>
      <div className="daily-note-body">
        {editing
          ? <textarea aria-label="每日便签内容" value={draft} onChange={event => setDraft(event.target.value)} autoFocus spellCheck={false}/>
          : <MarkdownPreview content={content}/>
        }
      </div>
      {editing && (
        <footer>
          <button onClick={() => { setDraft(content); setEditing(false) }}><X size={12}/>取消</button>
          <button className="primary" onClick={() => { saveDailyNote(draft); setContent(draft); setEditing(false) }}><Save size={12}/>保存</button>
        </footer>
      )}
    </aside>
  )
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}
