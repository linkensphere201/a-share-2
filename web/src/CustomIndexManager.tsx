import { useEffect, useMemo, useState } from 'react'
import { Gauge, Plus, RefreshCw, Save, Trash2, X } from 'lucide-react'
import { InstrumentBrowser } from './InstrumentBrowser'
import type { Instrument } from './workspace'

type WeightingMethod = 'equal' | 'manual'

type CustomIndexSummary = {
  id: string
  symbol: string
  name: string
  description: string
  base_date: string
  base_value: number
  status: 'pending' | 'building' | 'ready' | 'error'
  last_error?: string | null
  member_count: number
  rows: number
}

type CustomIndexMember = {
  symbol: string
  name: string
  raw_weight: number
  normalized_weight?: number
  first_trade_date?: string
}

type CustomIndexDraft = {
  id?: string
  symbol?: string
  name: string
  description: string
  base_date: string
  base_value: number
  weighting_method: WeightingMethod
  effective_from?: string
  revision_number?: number
  status?: CustomIndexSummary['status']
  last_error?: string | null
  rows?: number
  members: CustomIndexMember[]
}

export function CustomIndexManager({ onClose }: { onClose: () => void }) {
  const [indices, setIndices] = useState<CustomIndexSummary[]>([])
  const [draft, setDraft] = useState<CustomIndexDraft>()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const openIndex = async (id: string) => {
    setError('')
    const response = await fetch(`/api/custom-indices/${encodeURIComponent(id)}`)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const body = await response.json() as CustomIndexDraft
    setDraft({
      ...body,
      effective_from: maxDate(body.base_date, new Date().toISOString().slice(0, 10)),
    })
  }

  const reload = async (selectId?: string | null) => {
    const response = await fetch('/api/custom-indices')
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const body = await response.json() as { items: CustomIndexSummary[] }
    setIndices(body.items)
    const id = selectId === null ? body.items[0]?.id : selectId ?? draft?.id ?? body.items[0]?.id
    if (id) await openIndex(id)
  }

  useEffect(() => {
    reload().catch(() => setError('自定义指数加载失败'))
  }, [])

  const createDraft = () => setDraft({
    name: `自定义指数${indices.length + 1}`,
    description: '',
    base_date: defaultBaseDate(),
    base_value: 1000,
    weighting_method: 'equal',
    members: [],
  })

  const addMember = (instrument: Instrument) => {
    if (!draft || draft.members.some(item => item.symbol === instrument.symbol)) return
    const members = [...draft.members, {
      symbol: instrument.symbol,
      name: instrument.name,
      raw_weight: 1,
      first_trade_date: instrument.first_trade_date,
    }]
    const latestListing = members.reduce(
      (latest, item) => item.first_trade_date && item.first_trade_date > latest
        ? item.first_trade_date
        : latest,
      draft.base_date,
    )
    setDraft({ ...draft, members, base_date: latestListing })
  }

  const save = async () => {
    if (!draft || !draft.name.trim() || draft.members.length === 0) return
    setSaving(true)
    setError('')
    try {
      const response = await fetch(
        draft.id ? `/api/custom-indices/${encodeURIComponent(draft.id)}` : '/api/custom-indices',
        {
          method: draft.id ? 'PUT' : 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: draft.name,
            description: draft.description,
            base_date: draft.base_date,
            base_value: draft.base_value,
            weighting_method: draft.weighting_method,
            effective_from: draft.id ? draft.effective_from : undefined,
            members: draft.members.map(item => ({
              symbol: item.symbol,
              weight: draft.weighting_method === 'manual' ? item.raw_weight : undefined,
            })),
          }),
        },
      )
      const body = await response.json().catch(() => ({})) as CustomIndexDraft & { detail?: string }
      if (!response.ok) throw new Error(body.detail ?? '自定义指数保存失败')
      window.dispatchEvent(new CustomEvent('stock-harness:custom-indices-changed', {
        detail: { symbol: body.symbol },
      }))
      await reload(body.id)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const rebuild = async () => {
    if (!draft?.id) return
    setSaving(true)
    setError('')
    try {
      const response = await fetch(
        `/api/custom-indices/${encodeURIComponent(draft.id)}/rebuild`,
        { method: 'POST' },
      )
      const body = await response.json().catch(() => ({})) as CustomIndexDraft & { detail?: string }
      if (!response.ok) throw new Error(body.detail ?? '历史结果回算失败')
      await reload(draft.id)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!draft?.id || !window.confirm(`删除自定义指数“${draft.name}”？`)) return
    const response = await fetch(`/api/custom-indices/${encodeURIComponent(draft.id)}`, {
      method: 'DELETE',
    })
    if (!response.ok) {
      setError('自定义指数删除失败')
      return
    }
    window.dispatchEvent(new CustomEvent('stock-harness:custom-indices-changed'))
    setDraft(undefined)
    await reload(null)
  }

  const selectedSymbols = useMemo(
    () => new Set(draft?.members.map(item => item.symbol) ?? []),
    [draft?.members],
  )

  return <div className="modal-backdrop" role="presentation" onMouseDown={event => {
    if (event.target === event.currentTarget && !saving) onClose()
  }}>
    <section className="custom-index-modal" role="dialog" aria-modal="true" aria-label="自定义指数管理">
      <header>
        <div><Gauge size={17}/><span>自定义指数</span></div>
        <button className="icon-button" title="关闭" aria-label="关闭自定义指数管理" disabled={saving} onClick={onClose}><X size={17}/></button>
      </header>
      <aside>
        <button className="primary-button" onClick={createDraft}><Plus size={15}/>新建指数</button>
        <div className="custom-index-list">
          {indices.map(item => <button
            key={item.id}
            className={draft?.id === item.id ? 'active' : ''}
            onClick={() => openIndex(item.id).catch(() => setError('自定义指数加载失败'))}
          >
            <span>{item.name}<small>{item.member_count} 只成分 · {item.rows} 根日线</small></span>
            <i className={`index-status ${item.status}`} title={item.status}/>
          </button>)}
        </div>
      </aside>
      <div className="custom-index-editor">
        {!draft ? <div className="custom-index-placeholder">新建或选择一个自定义指数</div> : <>
          <div className="custom-index-fields">
            <label>指数名称<input value={draft.name} maxLength={80} onChange={event => setDraft({ ...draft, name: event.target.value })}/></label>
            <label>基准日期<input type="date" disabled={Boolean(draft.id)} value={draft.base_date} onChange={event => setDraft({ ...draft, base_date: event.target.value })}/></label>
            <label>基点<input type="number" min="1" step="100" disabled={Boolean(draft.id)} value={draft.base_value} onChange={event => setDraft({ ...draft, base_value: Number(event.target.value) })}/></label>
            <label>权重方式<select value={draft.weighting_method} onChange={event => setDraft({ ...draft, weighting_method: event.target.value as WeightingMethod })}>
              <option value="equal">等权</option><option value="manual">手工权重</option>
            </select></label>
            <label className="index-description">说明<input value={draft.description} maxLength={500} onChange={event => setDraft({ ...draft, description: event.target.value })}/></label>
            {draft.id && <label>变更生效日<input type="date" min={draft.base_date} value={draft.effective_from} onChange={event => setDraft({ ...draft, effective_from: event.target.value })}/></label>}
            <div className="custom-index-actions">
              {draft.id && <button className="danger-button" disabled={saving} onClick={remove}><Trash2 size={14}/>删除</button>}
              {draft.id && <button className="command-button" disabled={saving} onClick={rebuild}><RefreshCw size={14}/>重新回算</button>}
              <button className="primary-button" disabled={saving || !draft.name.trim() || draft.members.length === 0} onClick={save}><Save size={14}/>{saving ? '回算并保存中' : '回算并保存'}</button>
            </div>
          </div>
          <InstrumentBrowser
            selectedSymbols={selectedSymbols}
            onSelect={addMember}
            excludeCustomGroups
            stockOnly
            searchLabel="搜索指数成分股"
            placeholder="输入股票代码、名称或拼音首字母"
          />
          <div className="custom-index-member-table">
            <div className="custom-index-member-header"><span>成分股</span><span>原始权重</span><span>归一权重</span><span/></div>
            {draft.members.map((member, index) => {
              const total = draft.members.reduce((sum, item) => sum + item.raw_weight, 0)
              const normalizedWeight = draft.weighting_method === 'equal'
                ? 1 / draft.members.length
                : member.raw_weight / total
              return <div className="custom-index-member-row" key={member.symbol}>
                <span>{member.name}<small>{member.symbol}</small></span>
                <input
                  type="number" min="0.000001" step="0.1"
                  aria-label={`${member.name} 权重`}
                  disabled={draft.weighting_method === 'equal'}
                  value={draft.weighting_method === 'equal' ? 1 : member.raw_weight}
                  onChange={event => {
                    const members = [...draft.members]
                    members[index] = { ...member, raw_weight: Number(event.target.value) }
                    setDraft({ ...draft, members })
                  }}
                />
                <span>{Number.isFinite(normalizedWeight) ? `${(normalizedWeight * 100).toFixed(2)}%` : '--'}</span>
                <button title="移除成分股" aria-label={`移除 ${member.name}`} onClick={() => setDraft({
                  ...draft, members: draft.members.filter(item => item.symbol !== member.symbol),
                })}><X size={13}/></button>
              </div>
            })}
          </div>
          <div className="custom-index-footnote">
            {draft.status && <span>状态 {statusLabel(draft.status)}</span>}
            {draft.revision_number && <span>版本 {draft.revision_number}</span>}
            {draft.rows !== undefined && <span>{draft.rows} 根已物化日线</span>}
          </div>
          {(error || draft.last_error) && <div className="form-error">{error || draft.last_error}</div>}
        </>}
      </div>
    </section>
  </div>
}

function defaultBaseDate(): string {
  return '1990-01-01'
}

function statusLabel(status: CustomIndexSummary['status']): string {
  return { pending: '待回算', building: '回算中', ready: '可用', error: '失败' }[status]
}

function maxDate(left: string, right: string): string {
  return left > right ? left : right
}
