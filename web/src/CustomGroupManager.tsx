import { useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, FolderPlus, Plus, Save, Search, Trash2, X } from 'lucide-react'
import type { Instrument } from './workspace'

type CustomGroupSummary = {
  id: string
  symbol: string
  name: string
  description: string
  member_count: number
}

type CustomGroupMember = Instrument & {
  tags: string[]
  note: string
  available?: boolean
}

type CustomGroupDraft = {
  id?: string
  name: string
  description: string
  members: CustomGroupMember[]
}

export function CustomGroupManager({ onClose }: { onClose: () => void }) {
  const [groups, setGroups] = useState<CustomGroupSummary[]>([])
  const [draft, setDraft] = useState<CustomGroupDraft>()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Instrument[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [draggedSymbol, setDraggedSymbol] = useState<string>()

  const reloadGroups = async (selectId?: string | null) => {
    const response = await fetch('/api/custom-groups')
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const body = await response.json() as { items: CustomGroupSummary[] }
    setGroups(body.items)
    const nextId = selectId === null ? body.items[0]?.id : selectId ?? draft?.id ?? body.items[0]?.id
    if (nextId) await openGroup(nextId)
  }

  const openGroup = async (id: string) => {
    setError('')
    const response = await fetch(`/api/custom-groups/${encodeURIComponent(id)}`)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const body = await response.json() as CustomGroupDraft & { id: string }
    setDraft(body)
    setQuery('')
    setResults([])
  }

  useEffect(() => {
    reloadGroups().catch(() => setError('自定义分组加载失败'))
  }, [])

  useEffect(() => {
    if (!query.trim()) {
      setResults([])
      return
    }
    const controller = new AbortController()
    const handle = window.setTimeout(() => {
      const params = new URLSearchParams({ query, limit: '16' })
      fetch(`/api/instruments?${params}`, { signal: controller.signal })
        .then(response => response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`)))
        .then((body: { items: Instrument[] }) => setResults(body.items.filter(item => item.kind !== 'custom-group')))
        .catch(cause => {
          if ((cause as Error).name !== 'AbortError') setResults([])
        })
    }, 120)
    return () => {
      window.clearTimeout(handle)
      controller.abort()
    }
  }, [query])

  const save = async () => {
    if (!draft?.name.trim()) return
    setSaving(true)
    setError('')
    const response = await fetch(
      draft.id ? `/api/custom-groups/${encodeURIComponent(draft.id)}` : '/api/custom-groups',
      {
        method: draft.id ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: draft.name,
          description: draft.description,
          members: draft.members.map(member => ({
            symbol: member.symbol, tags: member.tags, note: member.note,
          })),
        }),
      },
    )
    setSaving(false)
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as { detail?: string }
      setError(body.detail ?? '保存失败')
      return
    }
    const saved = await response.json() as CustomGroupDraft & { id: string }
    window.dispatchEvent(new CustomEvent('stock-harness:custom-groups-changed', {
      detail: { symbol: `CUSTOM:${saved.id}` },
    }))
    await reloadGroups(saved.id)
  }

  const removeGroup = async () => {
    if (!draft?.id) return
    const symbol = `CUSTOM:${draft.id}`
    const workspaceReferences = (window.localStorage.getItem('stock-harness.workspace.v3')?.match(
      new RegExp(symbol.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'),
    ) ?? []).length
    const impact = workspaceReferences > 0
      ? `\n当前工作区保存了 ${workspaceReferences} 处引用；删除后窗口会保留，但该分组将显示为不可用。`
      : ''
    if (!window.confirm(`删除自定义分组“${draft.name}”？${impact}`)) return
    const response = await fetch(`/api/custom-groups/${encodeURIComponent(draft.id)}`, { method: 'DELETE' })
    if (!response.ok) {
      setError('删除失败')
      return
    }
    window.dispatchEvent(new CustomEvent('stock-harness:custom-groups-changed', {
      detail: { symbol },
    }))
    setDraft(undefined)
    await reloadGroups(null)
  }

  const addMember = (instrument: Instrument) => {
    if (!draft || draft.members.some(member => member.symbol === instrument.symbol)) return
    setDraft({ ...draft, members: [...draft.members, { ...instrument, tags: [], note: '' }] })
    setQuery('')
    setResults([])
  }

  const moveMember = (index: number, offset: -1 | 1) => {
    if (!draft) return
    const target = index + offset
    if (target < 0 || target >= draft.members.length) return
    const members = [...draft.members]
    ;[members[index], members[target]] = [members[target], members[index]]
    setDraft({ ...draft, members })
  }

  const moveMemberBefore = (sourceSymbol: string, targetSymbol: string) => {
    if (!draft || sourceSymbol === targetSymbol) return
    const sourceIndex = draft.members.findIndex(member => member.symbol === sourceSymbol)
    const targetIndex = draft.members.findIndex(member => member.symbol === targetSymbol)
    if (sourceIndex < 0 || targetIndex < 0) return
    const members = [...draft.members]
    const [source] = members.splice(sourceIndex, 1)
    members.splice(sourceIndex < targetIndex ? targetIndex - 1 : targetIndex, 0, source)
    setDraft({ ...draft, members })
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose()
  }}>
    <section className="custom-group-modal" role="dialog" aria-modal="true" aria-label="自定义分组管理">
      <header>
        <div><FolderPlus size={17}/><strong>自定义分组</strong></div>
        <button className="icon-button" title="关闭" aria-label="关闭自定义分组" onClick={onClose}><X size={17}/></button>
      </header>
      <aside>
        <button className="primary-button" onClick={() => setDraft({ name: `分组${groups.length + 1}`, description: '', members: [] })}>
          <Plus size={15}/>新建分组
        </button>
        <div className="custom-group-list">
          {groups.map(group => <button
            key={group.id}
            className={draft?.id === group.id ? 'active' : ''}
            onClick={() => openGroup(group.id).catch(() => setError('分组加载失败'))}
          ><span><strong>{group.name}</strong><small>{group.member_count} 个标的</small></span></button>)}
        </div>
      </aside>
      <div className="custom-group-editor">
        {!draft ? <div className="custom-group-placeholder">新建或选择一个分组</div> : <>
          <div className="custom-group-fields">
            <label>集合名<input value={draft.name} maxLength={80} onChange={event => setDraft({ ...draft, name: event.target.value })}/></label>
            <label>说明<input value={draft.description} maxLength={500} onChange={event => setDraft({ ...draft, description: event.target.value })}/></label>
            <div className="custom-group-actions">
              {draft.id && <button className="danger-button" onClick={removeGroup}><Trash2 size={14}/>删除</button>}
              <button className="primary-button" disabled={saving || !draft.name.trim()} onClick={save}><Save size={14}/>{saving ? '保存中' : '保存'}</button>
            </div>
          </div>
          <div className="custom-member-search">
            <label><Search size={14}/><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索代码或名称添加标的" aria-label="搜索分组成员"/></label>
            {query.trim() && <div className="custom-member-results">
              {results.map(item => <button key={item.symbol} disabled={draft.members.some(member => member.symbol === item.symbol)} onClick={() => addMember(item)}>
                <span><strong>{item.name}</strong><small>{item.symbol}</small></span><Plus size={14}/>
              </button>)}
            </div>}
          </div>
          <div className="custom-member-table">
            <div className="custom-member-header"><span>标的</span><span>标签</span><span>备注</span><span/></div>
            {draft.members.map((member, index) => <div
              className={draggedSymbol === member.symbol ? 'custom-member-row dragging' : 'custom-member-row'}
              key={member.symbol}
              draggable
              onDragStart={() => setDraggedSymbol(member.symbol)}
              onDragOver={event => event.preventDefault()}
              onDrop={() => {
                if (draggedSymbol) moveMemberBefore(draggedSymbol, member.symbol)
                setDraggedSymbol(undefined)
              }}
              onDragEnd={() => setDraggedSymbol(undefined)}
            >
              <span><strong>{member.name}</strong><small>{member.symbol}</small></span>
              <input value={member.tags.join(', ')} aria-label={`${member.name} 标签`} onChange={event => {
                const members = [...draft.members]
                members[index] = { ...member, tags: event.target.value.split(',').map(tag => tag.trim()).filter(Boolean) }
                setDraft({ ...draft, members })
              }}/>
              <input value={member.note} aria-label={`${member.name} 备注`} onChange={event => {
                const members = [...draft.members]
                members[index] = { ...member, note: event.target.value }
                setDraft({ ...draft, members })
              }}/>
              <div>
                <button title="上移" disabled={index === 0} onClick={() => moveMember(index, -1)}><ArrowUp size={13}/></button>
                <button title="下移" disabled={index === draft.members.length - 1} onClick={() => moveMember(index, 1)}><ArrowDown size={13}/></button>
                <button title="移除" onClick={() => setDraft({ ...draft, members: draft.members.filter(item => item.symbol !== member.symbol) })}><X size={13}/></button>
              </div>
            </div>)}
          </div>
          {error && <div className="form-error">{error}</div>}
        </>}
      </div>
    </section>
  </div>
}
