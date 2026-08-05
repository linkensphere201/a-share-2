import { useState } from 'react'
import { ChevronLeft, ChevronRight, Edit3, Minus, Save, StickyNote, X } from 'lucide-react'
import { loadDailyNote, localDateKey, saveDailyNote, shiftDate } from './dailyNoteStore'
import { MarkdownPreview } from './MarkdownPreview'

export function DailyNote() {
  const today = localDateKey()
  const [dateKey, setDateKey] = useState(today)
  const [content, setContent] = useState(() => loadDailyNote(today))
  const [draft, setDraft] = useState(content)
  const [editing, setEditing] = useState(false)
  const [minimized, setMinimized] = useState(false)

  const selectDate = (nextDate: string) => {
    const nextContent = loadDailyNote(nextDate)
    setDateKey(nextDate)
    setContent(nextContent)
    setDraft(nextContent)
  }

  if (minimized) {
    return (
      <button className="daily-note-minimized" title="展开每日便签" aria-label="展开每日便签" onClick={() => setMinimized(false)}>
        <StickyNote size={16}/>
      </button>
    )
  }

  return (
    <aside className="daily-note" aria-label="每日便签">
      <header>
        <span className="daily-note-title"><StickyNote size={14}/><strong>每日便签</strong></span>
        <div className="daily-note-date-controls">
          <button title="前一天" aria-label="前一天便签" disabled={editing} onClick={() => selectDate(shiftDate(dateKey, -1))}><ChevronLeft size={13}/></button>
          <input
            type="date"
            aria-label="便签日期"
            max={today}
            value={dateKey}
            disabled={editing}
            onChange={event => selectDate(event.target.value)}
          />
          <button title="后一天" aria-label="后一天便签" disabled={editing || dateKey >= today} onClick={() => selectDate(shiftDate(dateKey, 1))}><ChevronRight size={13}/></button>
        </div>
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
          <button className="primary" onClick={() => { saveDailyNote(dateKey, draft); setContent(draft); setEditing(false) }}><Save size={12}/>保存</button>
        </footer>
      )}
    </aside>
  )
}
