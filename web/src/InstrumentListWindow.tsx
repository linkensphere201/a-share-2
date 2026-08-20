import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { ArrowDown, ArrowUp, ArrowUpDown, Columns3, Maximize2, Minimize2, Pencil, X } from 'lucide-react'
import { allListColumns, type Instrument, type InstrumentListWindowState, type ListColumnKey } from './workspace'
import { logWarning } from './eventLogger'
import { instrumentSecondaryLabel } from './InstrumentBrowser'
import { CustomGroupMindMap, type MindMapAnchor } from './CustomGroupMindMap'

type MarketSnapshot = {
  symbol: string
  trade_date: string
  change_percent?: number
  settlement_change_percent?: number
  total_market_cap?: number
  close?: number
  volume?: number
  amount?: number
  open_interest?: number
  open_interest_change?: number
  source_state?: string
  contract_month?: string
  last_trading_date?: string
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
  onVisibleColumnsChange: (columns: ListColumnKey[]) => void
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
  onVisibleColumnsChange,
  onReferencedSymbolsChange,
}: InstrumentListWindowProps) {
  const [members, setMembers] = useState<ListInstrument[]>([])
  const [snapshots, setSnapshots] = useState<Record<string, MarketSnapshot>>({})
  const [memberMeta, setMemberMeta] = useState<{ asOf?: string; source?: string }>({})
  const [membersLoading, setMembersLoading] = useState(false)
  const [memberRefresh, setMemberRefresh] = useState(0)
  const [columnEditorOpen, setColumnEditorOpen] = useState(false)
  const [mindMap, setMindMap] = useState<{ group: Instrument; anchor: MindMapAnchor }>()

  useEffect(() => {
    const closeOtherMap = (event: Event) => {
      const sourceWindowId = (event as CustomEvent<{ sourceWindowId?: string }>).detail?.sourceWindowId
      if (sourceWindowId !== windowState.id) setMindMap(undefined)
    }
    window.addEventListener('stock-harness:custom-group-map-open', closeOtherMap)
    return () => window.removeEventListener('stock-harness:custom-group-map-open', closeOtherMap)
  }, [windowState.id])

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
  const visibleColumns = allListColumns.filter(column => windowState.visibleColumns.includes(column))
  const gridStyle = { gridTemplateColumns: visibleColumns.map(columnWidth).join(' ') } satisfies CSSProperties
  const tableStyle = { minWidth: Math.max(96, 96 + (visibleColumns.length - 1) * 48) } satisfies CSSProperties

  return (
    <section className={focused ? 'instrument-window list-window focused' : 'instrument-window list-window'}>
      <header className="instrument-window-header">
        <button className="instrument-window-title" onClick={onFocus}>
          <strong>{windowState.title}</strong><small>{detail}</small>
        </button>
        <div className="instrument-window-actions">
          <button
            className={columnEditorOpen ? 'active' : ''}
            title="编辑表头"
            aria-label={`编辑 ${windowState.title} 表头`}
            aria-expanded={columnEditorOpen}
            onClick={() => setColumnEditorOpen(value => !value)}
          ><Columns3 size={13}/></button>
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
      {columnEditorOpen && <div className="list-column-editor" onPointerDown={event => event.stopPropagation()}>
        <strong>显示列</strong>
        {listColumnOptions.map(option => {
          const checked = visibleColumns.includes(option.key)
          return <label key={option.key}>
            <input
              type="checkbox"
              checked={checked}
              disabled={option.key === 'name'}
              onChange={() => onVisibleColumnsChange(checked
                ? visibleColumns.filter(column => column !== option.key)
                : allListColumns.filter(column => [...visibleColumns, option.key].includes(column)))}
            />
            <span>{option.label}</span>
          </label>
        })}
      </div>}
      <div className={derived ? 'list-window-body derived' : 'list-window-body'} onPointerDown={onFocus}>
        <div className="list-window-table" style={tableStyle}>
          <div className="list-window-table-header" style={gridStyle}>
            {visibleColumns.map(column => <SortButton
              key={column}
              label={listColumnOptions.find(option => option.key === column)!.label}
              field={column}
              sort={windowState.sort}
              onChange={onSortChange}
            />)}
          </div>
          <div className="list-window-items">
          {membersLoading && <div className="list-window-empty">加载成分...</div>}
          {!membersLoading && sourceItems.length === 0 && <div className="list-window-empty">
            {derived && !memberSource ? '上游列表尚未选择标的' : '列表为空'}
          </div>}
          {displayedItems.map(item => {
            const snapshot = snapshots[item.symbol]
            return <div className={windowState.selectedSymbol === item.symbol ? 'list-window-row selected' : 'list-window-row'} key={item.symbol} style={gridStyle}>
              {visibleColumns.includes('name') && <button
                className="list-window-select"
                aria-label={`选择 ${item.name}`}
                disabled={item.available === false}
                onClick={event => {
                  onSelect(item)
                  if (item.kind === 'custom-group') {
                    window.dispatchEvent(new CustomEvent('stock-harness:custom-group-map-open', {
                      detail: { sourceWindowId: windowState.id },
                    }))
                    const rect = event.currentTarget.getBoundingClientRect()
                    setMindMap({
                      group: item,
                      anchor: {
                        left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom,
                        width: rect.width, height: rect.height,
                      },
                    })
                  }
                }}
              >
                <span><strong>{item.name}</strong><small>{instrumentSecondaryLabel(item)}</small></span>
              </button>}
              {visibleColumns.includes('close') && <span className="list-price">{formatPrice(snapshot?.close)}</span>}
              {visibleColumns.includes('change_percent') && <span className={changeClass(snapshot?.change_percent)}>{formatChange(snapshot?.change_percent)}</span>}
              {visibleColumns.includes('volume') && <span className="list-volume">{formatQuantity(snapshot?.volume)}</span>}
              {visibleColumns.includes('amount') && <span className="list-amount">{formatMoney(snapshot?.amount)}</span>}
              {visibleColumns.includes('total_market_cap') && <span className="list-market-cap">{formatMoney(snapshot?.total_market_cap)}</span>}
              {visibleColumns.includes('settlement_change_percent') && <span className={changeClass(snapshot?.settlement_change_percent)}>{formatChange(snapshot?.settlement_change_percent)}</span>}
              {visibleColumns.includes('open_interest') && <span>{formatQuantity(snapshot?.open_interest)}</span>}
              {visibleColumns.includes('open_interest_change') && <span className={changeClass(snapshot?.open_interest_change)}>{formatSignedQuantity(snapshot?.open_interest_change)}</span>}
              {visibleColumns.includes('source_state') && <span>{formatSourceState(snapshot?.source_state)}</span>}
              {visibleColumns.includes('contract_month') && <span>{snapshot?.contract_month ?? '—'}</span>}
              {visibleColumns.includes('last_trading_date') && <span>{snapshot?.last_trading_date ?? '—'}</span>}
            </div>
          })}
          </div>
        </div>
      </div>
      {mindMap && <CustomGroupMindMap
        group={mindMap.group}
        anchor={mindMap.anchor}
        onSelect={onSelect}
        onClose={() => setMindMap(undefined)}
      />}
    </section>
  )
}

const listColumnOptions: Array<{ key: ListColumnKey; label: string }> = [
  { key: 'name', label: '名称' },
  { key: 'close', label: '价格' },
  { key: 'change_percent', label: '涨跌幅' },
  { key: 'volume', label: '成交量' },
  { key: 'amount', label: '成交额' },
  { key: 'total_market_cap', label: '总市值' },
  { key: 'settlement_change_percent', label: '结算涨跌' },
  { key: 'open_interest', label: '持仓量' },
  { key: 'open_interest_change', label: '持仓变化' },
  { key: 'source_state', label: '数据状态' },
  { key: 'contract_month', label: '合约月份' },
  { key: 'last_trading_date', label: '最后交易日' },
]

function columnWidth(column: ListColumnKey): string {
  if (column === 'name') return 'minmax(64px, 1fr)'
  if (column === 'last_trading_date') return '78px'
  if (column === 'source_state' || column === 'contract_month') return '64px'
  return '56px'
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
    if (typeof leftValue === 'number' && typeof rightValue === 'number') {
      return (leftValue - rightValue) * direction
    }
    return String(leftValue).localeCompare(String(rightValue), 'zh-CN') * direction
  })
}

function formatPrice(value?: number): string {
  if (value === undefined || value === null) return '—'
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 3 })
}

function formatQuantity(value?: number): string {
  if (value === undefined || value === null) return '—'
  if (value >= 100_000_000) return `${(value / 100_000_000).toFixed(1)}亿`
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)}万`
  return value.toLocaleString('zh-CN')
}

function formatSignedQuantity(value?: number): string {
  if (value === undefined || value === null) return '—'
  const formatted = formatQuantity(Math.abs(value))
  return `${value > 0 ? '+' : value < 0 ? '-' : ''}${formatted}`
}

function formatSourceState(value?: string): string {
  if (!value) return '—'
  return ({ final: '正式', provisional: '盘中', 'provisional-stale': '盘中停更' })[value] ?? value
}

function formatMoney(value?: number): string {
  if (value === undefined || value === null) return '—'
  if (value >= 100_000_000) return `${(value / 100_000_000).toFixed(value >= 10_000_000_000 ? 0 : 1)}亿`
  if (value >= 10_000) return `${(value / 10_000).toFixed(0)}万`
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

function formatChange(value?: number): string {
  if (value === undefined || value === null) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function changeClass(value?: number): string {
  if (value === undefined || value === null) return 'list-change'
  return `list-change ${value >= 0 ? 'rise' : 'fall'}`
}
