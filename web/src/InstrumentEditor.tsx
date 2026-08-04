import { useEffect, useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, ListPlus, Save, Trash2, X } from 'lucide-react'
import { CustomGroupManager } from './CustomGroupManager'
import { InstrumentBrowser, instrumentClassLabel } from './InstrumentBrowser'
import type { ChartWindowState, Instrument, InstrumentListWindowState } from './workspace'

type EditableWindow = ChartWindowState | InstrumentListWindowState
type EditorTab = 'instruments' | 'groups'

type InstrumentEditorProps = {
  target?: EditableWindow
  initialTab?: EditorTab
  onSave: (windowId: string, instruments: Instrument[]) => void
  onClose: () => void
}

export function InstrumentEditor({
  target,
  initialTab = 'instruments',
  onSave,
  onClose,
}: InstrumentEditorProps) {
  const [tab, setTab] = useState<EditorTab>(initialTab)
  const [draft, setDraft] = useState<Instrument[]>(() => instrumentsFor(target))
  const [savedDraft, setSavedDraft] = useState<Instrument[]>(() => instrumentsFor(target))

  useEffect(() => {
    const instruments = instrumentsFor(target)
    setDraft(instruments)
    setSavedDraft(instruments)
  }, [target?.id])

  const selectedSymbols = useMemo(() => new Set(draft.map(item => item.symbol)), [draft])
  const dirty = JSON.stringify(draft) !== JSON.stringify(savedDraft)
  const canSave = Boolean(target) && (target?.type !== 'chart' || draft.length === 1)

  const addInstrument = (instrument: Instrument) => {
    if (target?.type === 'chart') {
      setDraft([instrument])
    } else if (!selectedSymbols.has(instrument.symbol)) {
      setDraft(items => [...items, instrument])
    }
  }

  const moveInstrument = (index: number, offset: -1 | 1) => {
    const targetIndex = index + offset
    if (targetIndex < 0 || targetIndex >= draft.length) return
    const next = [...draft]
    ;[next[index], next[targetIndex]] = [next[targetIndex], next[index]]
    setDraft(next)
  }

  const save = (close: boolean) => {
    if (!target || !canSave) return
    onSave(target.id, draft)
    setSavedDraft(draft)
    if (close) onClose()
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose()
  }}>
    <section className="instrument-editor-modal" role="dialog" aria-modal="true" aria-label="标的编辑">
      <header>
        <div><ListPlus size={17}/><strong>标的编辑</strong>{target && <span>{target.title}</span>}</div>
        <button className="icon-button" title="退出" aria-label="退出标的编辑" onClick={onClose}><X size={17}/></button>
      </header>
      <nav className="instrument-editor-tabs" aria-label="编辑内容">
        <button className={tab === 'instruments' ? 'active' : ''} disabled={!target} onClick={() => setTab('instruments')}>窗口标的</button>
        <button className={tab === 'groups' ? 'active' : ''} onClick={() => setTab('groups')}>自选集合</button>
      </nav>
      <div className="instrument-editor-content">
        {tab === 'groups'
          ? <CustomGroupManager embedded onClose={onClose}/>
          : target && <div className="instrument-target-editor">
            <InstrumentBrowser
              selectedSymbols={selectedSymbols}
              onSelect={addInstrument}
              excludeCustomGroups={target.type === 'chart'}
              searchLabel="搜索可添加标的"
              placeholder={target.type === 'chart' ? '在当前分类中搜索并替换图表标的' : '在当前分类中搜索代码、名称或拼音'}
            />
            <div className="instrument-editor-summary">
              <strong>{target.type === 'chart' ? '图表标的' : '列表成员'}</strong>
              <span>{target.type === 'chart' ? '固定图表只能保存一个可绘制标的' : `${draft.length} 个标的，保存后统一生效`}</span>
            </div>
            <div className="instrument-editor-members">
              {draft.length === 0 && <div className="instrument-editor-empty">
                {target.type === 'chart' ? '请选择一个图表标的' : '固定列表可以为空'}
              </div>}
              {draft.map((item, index) => <div className="instrument-editor-member" key={item.symbol}>
                <span><strong>{item.name}</strong><small>{item.symbol} · {instrumentClassLabel(item)}</small></span>
                {target.type === 'instrument-list' && <div>
                  <button title="上移" aria-label={`上移 ${item.name}`} disabled={index === 0} onClick={() => moveInstrument(index, -1)}><ArrowUp size={13}/></button>
                  <button title="下移" aria-label={`下移 ${item.name}`} disabled={index === draft.length - 1} onClick={() => moveInstrument(index, 1)}><ArrowDown size={13}/></button>
                  <button title="删除" aria-label={`删除 ${item.name}`} onClick={() => setDraft(items => items.filter(existing => existing.symbol !== item.symbol))}><Trash2 size={13}/></button>
                </div>}
              </div>)}
            </div>
          </div>
        }
      </div>
      {tab === 'instruments' && <footer>
        <span>{dirty ? '有未保存改动' : '已保存'}</span>
        <button className="command-button" onClick={onClose}>退出</button>
        <button className="command-button" disabled={!canSave || !dirty} onClick={() => save(false)}><Save size={14}/>保存</button>
        <button className="primary-button" disabled={!canSave} onClick={() => save(true)}><Save size={14}/>保存并退出</button>
      </footer>}
    </section>
  </div>
}

function instrumentsFor(target?: EditableWindow): Instrument[] {
  if (!target) return []
  return target.type === 'chart' ? [target.instrument] : [...target.content.instruments]
}
