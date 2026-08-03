import { useCallback, useEffect, useState } from 'react'
import {
  BarChart3,
  ChevronRight,
  LayoutGrid,
  MessageSquare,
  PanelRightClose,
  Plus,
  Search,
} from 'lucide-react'
import type { PriceMode, VisibleRange } from './ChartCanvas'
import { WindowGroup } from './WindowGroup'
import {
  chartRanges,
  loadWorkspace,
  saveWorkspace,
  type Instrument,
  type InstrumentWindowState,
  type WindowGroupState,
  type WorkspaceState,
} from './workspace'

type Scope = 'all' | 'stock' | 'etf' | 'index' | 'sector'

const scopes: Array<{ value: Scope; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'stock', label: '股票' },
  { value: 'etf', label: 'ETF' },
  { value: 'index', label: '指数' },
  { value: 'sector', label: '板块' },
]

export function StockWorkspace() {
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<Scope>('all')
  const [items, setItems] = useState<Instrument[]>([])
  const [workspace, setWorkspace] = useState<WorkspaceState>(loadWorkspace)
  const [chatOpen, setChatOpen] = useState(true)
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'error'>('loading')

  const activeGroup = workspace.groups.find(group => group.id === workspace.activeGroupId) ?? workspace.groups[0]
  const focusedWindow = activeGroup.windows.find(item => item.id === activeGroup.focusedWindowId) ?? activeGroup.windows[0]

  useEffect(() => saveWorkspace(workspace), [workspace])

  useEffect(() => {
    const controller = new AbortController()
    setLoadState('loading')
    const handle = window.setTimeout(async () => {
      const params = new URLSearchParams({ query, limit: '30' })
      if (scope !== 'all') params.append('kind', scope)
      try {
        const response = await fetch(`/api/instruments?${params}`, { signal: controller.signal })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const body = await response.json() as { items: Instrument[] }
        setItems(body.items)
        setLoadState('ready')
      } catch (error) {
        if ((error as Error).name !== 'AbortError') setLoadState('error')
      }
    }, 120)
    return () => {
      window.clearTimeout(handle)
      controller.abort()
    }
  }, [query, scope])

  const updateActiveGroup = useCallback((update: (group: WindowGroupState) => WindowGroupState) => {
    setWorkspace(current => ({
      ...current,
      groups: current.groups.map(group => group.id === current.activeGroupId ? update(group) : group),
    }))
  }, [])

  const updateWindow = useCallback((id: string, update: (item: InstrumentWindowState) => InstrumentWindowState) => {
    updateActiveGroup(group => ({
      ...group,
      windows: group.windows.map(item => item.id === id ? update(item) : item),
    }))
  }, [updateActiveGroup])

  const replaceFocused = (instrument: Instrument) => {
    updateWindow(focusedWindow.id, item => ({
      ...item,
      instrument,
      chart: { ...item.chart, visibleRange: undefined },
    }))
  }

  const addWindow = (instrument: Instrument) => {
    const existing = activeGroup.windows.find(item => item.instrument.symbol === instrument.symbol)
    if (existing) {
      updateActiveGroup(group => ({ ...group, focusedWindowId: existing.id, maximizedWindowId: undefined }))
      return
    }
    if (activeGroup.windows.length >= 4) return
    const id = `window-${Date.now()}`
    updateActiveGroup(group => ({
      ...group,
      windows: [...group.windows, {
        id,
        instrument,
        chart: { range: focusedWindow.chart.range, priceMode: focusedWindow.chart.priceMode },
      }],
      focusedWindowId: id,
      maximizedWindowId: undefined,
    }))
  }

  const removeWindow = (id: string) => {
    if (activeGroup.windows.length === 1) return
    updateActiveGroup(group => {
      const index = group.windows.findIndex(item => item.id === id)
      const windows = group.windows.filter(item => item.id !== id)
      const focusedWindowId = group.focusedWindowId === id
        ? windows[Math.min(index, windows.length - 1)].id
        : group.focusedWindowId
      return {
        ...group,
        windows,
        focusedWindowId,
        maximizedWindowId: group.maximizedWindowId === id ? undefined : group.maximizedWindowId,
      }
    })
  }

  const handleCoverage = useCallback((id: string, symbol: string, rows: number, first?: string, last?: string) => {
    updateWindow(id, item => item.instrument.symbol !== symbol ? item : ({
      ...item,
      instrument: { ...item.instrument, rows, first_trade_date: first, last_trade_date: last },
    }))
  }, [updateWindow])

  const handleVisibleRange = useCallback((id: string, value: VisibleRange) => {
    updateWindow(id, item => (
      item.chart.visibleRange?.from === value.from && item.chart.visibleRange.to === value.to
        ? item
        : { ...item, chart: { ...item.chart, visibleRange: value } }
    ))
  }, [updateWindow])

  const setPriceMode = (priceMode: PriceMode) => {
    updateWindow(focusedWindow.id, item => ({ ...item, chart: { ...item.chart, priceMode } }))
  }

  return (
    <main className={chatOpen ? 'workstation' : 'workstation chat-closed'}>
      <aside className="symbol-rail">
        <header className="brand"><BarChart3 size={19}/><strong>StockHarness</strong></header>
        <label className="search">
          <Search size={15}/>
          <input value={query} onChange={event => setQuery(event.target.value)} placeholder="代码、名称、板块"/>
        </label>
        <nav className="scope-tabs" aria-label="标的类型">
          {scopes.map(item => (
            <button key={item.value} className={scope === item.value ? 'active' : ''} onClick={() => setScope(item.value)}>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="instrument-list">
          {loadState === 'loading' && <div className="list-state">加载中</div>}
          {loadState === 'error' && <div className="list-state error">数据服务不可用</div>}
          {loadState === 'ready' && items.length === 0 && <div className="list-state">没有匹配标的</div>}
          {loadState === 'ready' && items.map(item => (
            <div key={item.symbol} className={focusedWindow.instrument.symbol === item.symbol ? 'instrument-row active' : 'instrument-row'}>
              <button className="instrument-main" onClick={() => replaceFocused(item)}>
                <span><strong>{item.name}</strong><small>{item.symbol}</small></span>
                <ChevronRight size={14}/>
              </button>
              <button
                className="instrument-add"
                title="添加窗口"
                aria-label={`添加 ${item.name} 窗口`}
                disabled={activeGroup.windows.length >= 4 && !activeGroup.windows.some(window => window.instrument.symbol === item.symbol)}
                onClick={() => addWindow(item)}
              ><Plus size={14}/></button>
            </div>
          ))}
        </div>
      </aside>

      <section className="workspace">
        <header className="toolbar">
          <div className="security">
            <LayoutGrid size={17}/>
            <span><strong>{focusedWindow.instrument.name}</strong><small>{focusedWindow.instrument.symbol} · {focusedWindow.instrument.category ?? focusedWindow.instrument.kind}</small></span>
          </div>
          <div className="range-tabs" aria-label="时间范围">
            {chartRanges.map(range => (
              <button
                key={range}
                className={focusedWindow.chart.range === range ? 'active' : ''}
                onClick={() => updateWindow(focusedWindow.id, item => ({
                  ...item,
                  chart: { ...item.chart, range, visibleRange: undefined },
                }))}
              >{range}</button>
            ))}
          </div>
          <button
            className="icon-button"
            title={chatOpen ? '收起对话栏' : '展开对话栏'}
            aria-label={chatOpen ? '收起对话栏' : '展开对话栏'}
            onClick={() => setChatOpen(!chatOpen)}
          >{chatOpen ? <PanelRightClose size={17}/> : <MessageSquare size={17}/>}</button>
        </header>
        <div className="market-strip">
          <span>日线</span><span>不复权</span>
          <div className="coordinate-tabs" aria-label="价格坐标">
            <button className={focusedWindow.chart.priceMode === 'normal' ? 'active' : ''} onClick={() => setPriceMode('normal')}>普通</button>
            <button className={focusedWindow.chart.priceMode === 'log' ? 'active' : ''} onClick={() => setPriceMode('log')}>对数</button>
          </div>
          <span className="ma ma-short">MA 5</span><span className="ma ma-mid">MA 20</span><span className="ma ma-long">MA 60</span>
          <span className="window-count">{activeGroup.windows.length}/4</span>
        </div>
        <WindowGroup
          group={activeGroup}
          onFocusWindow={id => updateActiveGroup(group => ({ ...group, focusedWindowId: id }))}
          onToggleMaximize={id => updateActiveGroup(group => ({
            ...group,
            focusedWindowId: id,
            maximizedWindowId: group.maximizedWindowId === id ? undefined : id,
          }))}
          onRemoveWindow={removeWindow}
          onCoverageChange={handleCoverage}
          onVisibleRangeChange={handleVisibleRange}
        />
        <footer className="statusbar">
          <span>{focusedWindow.instrument.first_trade_date ?? '—'} → {focusedWindow.instrument.last_trade_date ?? '—'}</span>
          <span>{focusedWindow.instrument.rows.toLocaleString()} 根日线</span>
          <span className="sync-state"><i/>数据已同步</span>
        </footer>
      </section>

      {chatOpen && (
        <aside className="chat-panel">
          <header><MessageSquare size={16}/><strong>Chat</strong></header>
          <div className="chat-empty"><MessageSquare size={22}/></div>
          <div className="chat-input"><input disabled aria-label="消息"/><button disabled aria-label="发送"><ChevronRight size={16}/></button></div>
        </aside>
      )}
    </main>
  )
}
