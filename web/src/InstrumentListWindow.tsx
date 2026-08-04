import { useEffect, useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, ArrowUpDown, Maximize2, Minimize2, Pencil, X } from 'lucide-react'
import type { Instrument, InstrumentListWindowState } from './workspace'
import { logWarning } from './eventLogger'

type MarketSnapshot = {
  symbol: string
  trade_date: string
  change_percent: number
  total_market_cap?: number
}

type ListInstrument = Instrument & { available?: boolean }

type InstrumentListWindowProps = {
  windowState: InstrumentListWindowState
  focused: boolean
  maximized: boolean
  removable: boolean
  onFocus: () => void
  onToggleMaximize: () => void
  onRemoveWindow: () => void
  onSelect: (instrument: Instrument) => void
  onEdit: () => void
  derived: boolean
  memberSource?: Instrument
  onSortChange: (sort: NonNullable<InstrumentListWindowState['sort']>) => void
  onReferencedSymbolsChange: (id: string, symbols: string[]) => void
}

export function InstrumentListWindow({
  windowState,
  focused,
  maximized,
  removable,
  onFocus,
  onToggleMaximize,
  onRemoveWindow,
  onSelect,
  onEdit,
  derived,
  memberSource,
  onSortChange,
  onReferencedSymbolsChange,
}: InstrumentListWindowProps) {
  const [members, setMembers] = useState<ListInstrument[]>([])
  const [snapshots, setSnapshots] = useState<Record<string, MarketSnapshot>>({})
  const [memberMeta, setMemberMeta] = useState<{ asOf?: string; source?: string }>({})
  const [membersLoading, setMembersLoading] = useState(false)
  const [memberRefresh, setMemberRefresh] = useState(0)

  useEffect(() => {
    const refresh = (event: Event) => {
      const symbol = (event as CustomEvent<{ symbol?: string }>).detail?.symbol
      if (symbol === memberSource?.symbol) setMemberRefresh(value => value + 1)
    }
    window.addEventListener('stock-harness:custom-groups-changed', refresh)
    return () => window.removeEventListener('stock-harness:custom-groups-changed', refresh)
  }, [memberSource?.symbol])

  useEffect(() => {
    if (!derived || !memberSource) {
      setMembers([])
      setMemberMeta({})
      if (derived) setSnapshots({})
      return
    }
    const controller = new AbortController()
    setMembersLoading(true)
    fetch(`/api/instruments/${encodeURIComponent(memberSource.symbol)}/members`, {
      signal: controller.signal,
    }).then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      return response.json() as Promise<{
        as_of_date?: string
        source?: string
        items: Array<ListInstrument & MarketSnapshot>
      }>
    }).then(body => {
      setMembers(body.items.map(item => ({
        ...item,
        kind: item.kind ?? 'stock',
        exchange: item.exchange ?? item.symbol.split('.').at(-1) ?? '',
        rows: item.rows ?? 0,
      })))
      setSnapshots(Object.fromEntries(body.items
        .filter(item => item.change_percent !== null && item.change_percent !== undefined)
        .map(item => [item.symbol, item])))
      setMemberMeta({ asOf: body.as_of_date, source: body.source })
    }).catch(error => {
      if ((error as Error).name !== 'AbortError') {
        setMembers([])
        setMemberMeta({})
        logWarning('members', '加载派生列表成分失败', { source: memberSource.symbol, error })
      }
    }).finally(() => setMembersLoading(false))
    return () => controller.abort()
  }, [derived, memberSource?.symbol, memberRefresh])

  const manualInstruments = windowState.content.instruments
  useEffect(() => {
    if (derived || manualInstruments.length === 0) {
      if (!derived) setSnapshots({})
      return
    }
    const controller = new AbortController()
    const params = new URLSearchParams()
    manualInstruments.forEach(item => params.append('symbol', item.symbol))
    fetch(`/api/market-snapshots?${params}`, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<{ items: MarketSnapshot[] }>
      })
      .then(body => setSnapshots(Object.fromEntries(body.items.map(item => [item.symbol, item]))))
      .catch(error => {
        if ((error as Error).name !== 'AbortError') {
          setSnapshots({})
          logWarning('snapshots', '加载列表行情快照失败', { symbols: manualInstruments.length, error })
        }
      })
    return () => controller.abort()
  }, [derived, manualInstruments.map(item => item.symbol).join('|')])

  const sourceItems: ListInstrument[] = derived ? members : manualInstruments
  useEffect(() => {
    onReferencedSymbolsChange(windowState.id, sourceItems.map(item => item.symbol))
    return () => onReferencedSymbolsChange(windowState.id, [])
  }, [onReferencedSymbolsChange, windowState.id, sourceItems.map(item => item.symbol).join('|')])
  const displayedItems = useMemo(
    () => sortListInstruments(sourceItems, snapshots, windowState.sort),
    [sourceItems, snapshots, windowState.sort],
  )
  const detail = derived
    ? memberSource
      ? memberMeta.asOf ? `${memberMeta.asOf} · ${memberMeta.source ?? '成分'}` : '暂无成分数据'
      : '等待上游选择'
    : `${sourceItems.length} 个标的`

  return (
    <section className={focused ? 'instrument-window list-window focused' : 'instrument-window list-window'}>
      <header className="instrument-window-header">
        <button className="instrument-window-title" onClick={onFocus}>
          <strong>{windowState.title}</strong><small>{detail}</small>
        </button>
        <div className="instrument-window-actions">
          {!derived && <button title="编辑标的" aria-label={`编辑 ${windowState.title} 标的`} onClick={onEdit}><Pencil size={13}/></button>}
          <button
            title={maximized ? '还原窗口' : '最大化窗口'}
            aria-label={maximized ? '还原窗口' : `最大化 ${windowState.title} 窗口`}
            onClick={onToggleMaximize}
          >{maximized ? <Minimize2 size={13}/> : <Maximize2 size={13}/>}</button>
          <button
            title="移除窗口"
            aria-label={`移除 ${windowState.title} 窗口`}
            disabled={!removable}
            onClick={onRemoveWindow}
          ><X size={14}/></button>
        </div>
      </header>
      <div className={derived ? 'list-window-body derived' : 'list-window-body'} onPointerDown={onFocus}>
        <div className="list-window-table-header">
          <SortButton label="名称" field="name" sort={windowState.sort} onChange={onSortChange}/>
          <SortButton label="总市值" field="total_market_cap" sort={windowState.sort} onChange={onSortChange}/>
          <SortButton label="涨跌幅" field="change_percent" sort={windowState.sort} onChange={onSortChange}/>
          <span/>
        </div>
        <div className="list-window-items">
          {membersLoading && <div className="list-window-empty">加载成分...</div>}
          {!membersLoading && sourceItems.length === 0 && <div className="list-window-empty">
            {derived && !memberSource ? '上游列表尚未选择标的' : '列表为空'}
          </div>}
          {displayedItems.map(item => {
            const snapshot = snapshots[item.symbol]
            return <div className={windowState.selectedSymbol === item.symbol ? 'list-window-row selected' : 'list-window-row'} key={item.symbol}>
              <button
                className="list-window-select"
                aria-label={`选择 ${item.name}`}
                disabled={item.available === false}
                onClick={() => onSelect(item)}
              >
                <span><strong>{item.name}</strong><small>{item.symbol}</small></span>
              </button>
              <span className="list-market-cap">{formatMarketCap(snapshot?.total_market_cap)}</span>
              <span className={changeClass(snapshot?.change_percent)}>{formatChange(snapshot?.change_percent)}</span>
              <span/>
            </div>
          })}
        </div>
      </div>
    </section>
  )
}

function SortButton({ label, field, sort, onChange }: {
  label: string
  field: NonNullable<InstrumentListWindowState['sort']>['key']
  sort?: InstrumentListWindowState['sort']
  onChange: (sort: NonNullable<InstrumentListWindowState['sort']>) => void
}) {
  const active = sort?.key === field
  const direction = active && sort?.direction === 'asc' ? 'desc' : 'asc'
  const Icon = !active ? ArrowUpDown : sort.direction === 'asc' ? ArrowUp : ArrowDown
  return <button onClick={() => onChange({ key: field, direction })}>{label}<Icon size={10}/></button>
}

export function sortListInstruments(
  instruments: ListInstrument[],
  snapshots: Record<string, MarketSnapshot>,
  sort?: InstrumentListWindowState['sort'],
): ListInstrument[] {
  if (!sort) return instruments
  const direction = sort.direction === 'asc' ? 1 : -1
  return [...instruments].sort((left, right) => {
    if (sort.key === 'name') return left.name.localeCompare(right.name, 'zh-CN') * direction
    const leftValue = snapshots[left.symbol]?.[sort.key]
    const rightValue = snapshots[right.symbol]?.[sort.key]
    if (leftValue === undefined || leftValue === null) return rightValue === undefined || rightValue === null ? 0 : 1
    if (rightValue === undefined || rightValue === null) return -1
    return (leftValue - rightValue) * direction
  })
}

function formatMarketCap(value?: number): string {
  if (value === undefined || value === null) return '—'
  if (value >= 100_000_000) return `${(value / 100_000_000).toFixed(value >= 10_000_000_000 ? 0 : 1)}亿`
  return `${(value / 10_000).toFixed(0)}万`
}

function formatChange(value?: number): string {
  if (value === undefined || value === null) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function changeClass(value?: number): string {
  if (value === undefined || value === null) return 'list-change'
  return `list-change ${value >= 0 ? 'rise' : 'fall'}`
}
