import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  BarChart3,
  ChevronRight,
  LayoutGrid,
  Maximize2,
  MessageSquare,
  Minimize2,
  PanelRightClose,
  Plus,
  Search,
  X,
} from 'lucide-react'
import {
  ChartCanvas,
  type ChartRange,
  type PriceMode,
  type VisibleRange,
} from './ChartCanvas'

export type Instrument = {
  symbol: string
  name: string
  kind: string
  exchange: string
  category?: string
  first_trade_date?: string
  last_trade_date?: string
  rows: number
}

type Scope = 'all' | 'stock' | 'etf' | 'index' | 'sector'
type CanvasState = {
  id: string
  instrument: Instrument
  range: ChartRange
  priceMode: PriceMode
  visibleRange?: VisibleRange
}

const storageKey = 'stock-harness.workspace.v1'
const scopes: Array<{ value: Scope; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'stock', label: '股票' },
  { value: 'etf', label: 'ETF' },
  { value: 'index', label: '指数' },
  { value: 'sector', label: '板块' },
]
const ranges: ChartRange[] = ['1Y', '3Y', '10Y', 'ALL']

const fallback: Instrument = {
  symbol: 'BK1128.DC',
  name: 'CPO概念',
  kind: 'sector',
  exchange: 'DC',
  category: '概念板块',
  rows: 843,
  first_trade_date: '2023-02-10',
  last_trade_date: '2026-07-31',
}

export function App() {
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<Scope>('all')
  const [items, setItems] = useState<Instrument[]>([])
  const [canvases, setCanvases] = useState<CanvasState[]>(loadWorkspace)
  const [focusedId, setFocusedId] = useState('')
  const [maximizedId, setMaximizedId] = useState<string | null>(null)
  const [chatOpen, setChatOpen] = useState(true)
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'error'>('loading')

  const focused = canvases.find(item => item.id === focusedId) ?? canvases[0]
  const visibleCanvases = useMemo(
    () => maximizedId ? canvases.filter(item => item.id === maximizedId) : canvases,
    [canvases, maximizedId],
  )

  useEffect(() => {
    if (!canvases.some(item => item.id === focusedId)) setFocusedId(canvases[0].id)
    window.localStorage.setItem(storageKey, JSON.stringify(canvases))
  }, [canvases, focusedId])

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

  const updateCanvas = useCallback((id: string, update: (item: CanvasState) => CanvasState) => {
    setCanvases(current => current.map(item => item.id === id ? update(item) : item))
  }, [])

  const replaceFocused = (instrument: Instrument) => {
    updateCanvas(focused.id, item => ({ ...item, instrument, visibleRange: undefined }))
  }

  const addCanvas = (instrument: Instrument) => {
    const existing = canvases.find(item => item.instrument.symbol === instrument.symbol)
    if (existing) {
      setFocusedId(existing.id)
      setMaximizedId(null)
      return
    }
    if (canvases.length >= 4) return
    const id = `canvas-${Date.now()}`
    setCanvases(current => [
      ...current,
      { id, instrument, range: focused.range, priceMode: focused.priceMode },
    ])
    setFocusedId(id)
    setMaximizedId(null)
  }

  const removeCanvas = (id: string) => {
    if (canvases.length === 1) return
    const index = canvases.findIndex(item => item.id === id)
    const next = canvases.filter(item => item.id !== id)
    setCanvases(next)
    if (focused.id === id) setFocusedId(next[Math.min(index, next.length - 1)].id)
    if (maximizedId === id) setMaximizedId(null)
  }

  const handleCoverage = useCallback((id: string, symbol: string, rows: number, first?: string, last?: string) => {
    updateCanvas(id, item => item.instrument.symbol !== symbol ? item : ({
      ...item,
      instrument: {
        ...item.instrument,
        rows,
        first_trade_date: first,
        last_trade_date: last,
      },
    }))
  }, [updateCanvas])

  const handleVisibleRange = useCallback((id: string, value: VisibleRange) => {
    updateCanvas(id, item => (
      item.visibleRange?.from === value.from && item.visibleRange.to === value.to
        ? item
        : { ...item, visibleRange: value }
    ))
  }, [updateCanvas])

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
            <button
              key={item.value}
              className={scope === item.value ? 'active' : ''}
              onClick={() => setScope(item.value)}
            >{item.label}</button>
          ))}
        </nav>
        <div className="instrument-list">
          {loadState === 'loading' && <div className="list-state">加载中</div>}
          {loadState === 'error' && <div className="list-state error">数据服务不可用</div>}
          {loadState === 'ready' && items.length === 0 && <div className="list-state">没有匹配标的</div>}
          {loadState === 'ready' && items.map(item => (
            <div key={item.symbol} className={focused.instrument.symbol === item.symbol ? 'instrument-row active' : 'instrument-row'}>
              <button className="instrument-main" onClick={() => replaceFocused(item)}>
                <span><strong>{item.name}</strong><small>{item.symbol}</small></span>
                <ChevronRight size={14}/>
              </button>
              <button
                className="instrument-add"
                title="添加画布"
                aria-label={`添加 ${item.name} 画布`}
                disabled={canvases.length >= 4 && !canvases.some(canvas => canvas.instrument.symbol === item.symbol)}
                onClick={() => addCanvas(item)}
              ><Plus size={14}/></button>
            </div>
          ))}
        </div>
      </aside>

      <section className="workspace">
        <header className="toolbar">
          <div className="security">
            <LayoutGrid size={17}/>
            <span><strong>{focused.instrument.name}</strong><small>{focused.instrument.symbol} · {focused.instrument.category ?? focused.instrument.kind}</small></span>
          </div>
          <div className="range-tabs" aria-label="时间范围">
            {ranges.map(item => (
              <button
                key={item}
                className={focused.range === item ? 'active' : ''}
                onClick={() => updateCanvas(focused.id, canvas => ({ ...canvas, range: item, visibleRange: undefined }))}
              >{item}</button>
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
            <button
              className={focused.priceMode === 'normal' ? 'active' : ''}
              onClick={() => updateCanvas(focused.id, canvas => ({ ...canvas, priceMode: 'normal' }))}
            >普通</button>
            <button
              className={focused.priceMode === 'log' ? 'active' : ''}
              onClick={() => updateCanvas(focused.id, canvas => ({ ...canvas, priceMode: 'log' }))}
            >对数</button>
          </div>
          <span className="ma ma-short">MA 5</span><span className="ma ma-mid">MA 20</span><span className="ma ma-long">MA 60</span>
          <span className="canvas-count">{canvases.length}/4</span>
        </div>
        <div className={`canvas-shell canvas-grid canvas-count-${visibleCanvases.length}`}>
          {visibleCanvases.map(canvas => (
            <section
              key={canvas.id}
              className={focused.id === canvas.id ? 'chart-panel focused' : 'chart-panel'}
            >
              <header className="chart-panel-header">
                <button className="chart-panel-title" onClick={() => setFocusedId(canvas.id)}>
                  <strong>{canvas.instrument.name}</strong><small>{canvas.instrument.symbol}</small>
                </button>
                <div className="chart-panel-actions">
                  <button
                    title={maximizedId === canvas.id ? '还原画布' : '最大化画布'}
                    aria-label={maximizedId === canvas.id ? '还原画布' : `最大化 ${canvas.instrument.name} 画布`}
                    onClick={() => setMaximizedId(maximizedId === canvas.id ? null : canvas.id)}
                  >{maximizedId === canvas.id ? <Minimize2 size={13}/> : <Maximize2 size={13}/>}</button>
                  <button
                    title="移除画布"
                    aria-label={`移除 ${canvas.instrument.name} 画布`}
                    disabled={canvases.length === 1}
                    onClick={() => removeCanvas(canvas.id)}
                  ><X size={14}/></button>
                </div>
              </header>
              <div className="chart-panel-body" onPointerDown={() => setFocusedId(canvas.id)}>
                <ChartCanvas
                  symbol={canvas.instrument.symbol}
                  range={canvas.range}
                  priceMode={canvas.priceMode}
                  initialVisibleRange={canvas.visibleRange}
                  onCoverageChange={(rows, first, last) => handleCoverage(canvas.id, canvas.instrument.symbol, rows, first, last)}
                  onVisibleRangeChange={value => handleVisibleRange(canvas.id, value)}
                />
              </div>
            </section>
          ))}
        </div>
        <footer className="statusbar">
          <span>{focused.instrument.first_trade_date ?? '—'} → {focused.instrument.last_trade_date ?? '—'}</span>
          <span>{focused.instrument.rows.toLocaleString()} 根日线</span>
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

function loadWorkspace(): CanvasState[] {
  try {
    const value = window.localStorage.getItem(storageKey)
    const parsed = value ? JSON.parse(value) as CanvasState[] : []
    const valid = parsed.filter(item => (
      item?.id && item.instrument?.symbol && ranges.includes(item.range)
      && (item.priceMode === 'normal' || item.priceMode === 'log')
    )).slice(0, 4)
    if (valid.length > 0) return valid
  } catch {
    // Ignore corrupt local workspace state and restore the known default canvas.
  }
  return [{ id: 'canvas-primary', instrument: fallback, range: '3Y', priceMode: 'normal' }]
}
