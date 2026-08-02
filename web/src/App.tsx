import { useCallback, useEffect, useState } from 'react'
import {
  BarChart3,
  ChevronRight,
  MessageSquare,
  PanelRightClose,
  Search,
  Star,
} from 'lucide-react'
import { ChartCanvas, type ChartRange, type PriceMode } from './ChartCanvas'

type Instrument = {
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
  const [range, setRange] = useState<ChartRange>('3Y')
  const [priceMode, setPriceMode] = useState<PriceMode>('normal')
  const [items, setItems] = useState<Instrument[]>([])
  const [selected, setSelected] = useState<Instrument>(fallback)
  const [chatOpen, setChatOpen] = useState(true)
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'error'>('loading')

  const handleCoverageChange = useCallback((rows: number, first?: string, last?: string) => {
    setSelected(current => ({
      ...current,
      rows,
      first_trade_date: first,
      last_trade_date: last,
    }))
  }, [])

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
            <button
              key={item.symbol}
              className={selected.symbol === item.symbol ? 'instrument active' : 'instrument'}
              onClick={() => setSelected(item)}
            >
              <span><strong>{item.name}</strong><small>{item.symbol}</small></span>
              <ChevronRight size={14}/>
            </button>
          ))}
        </div>
      </aside>

      <section className="workspace">
        <header className="toolbar">
          <div className="security">
            <button className="icon-button" title="加入自选" aria-label="加入自选"><Star size={16}/></button>
            <span><strong>{selected.name}</strong><small>{selected.symbol} · {selected.category ?? selected.kind}</small></span>
          </div>
          <div className="range-tabs" aria-label="时间范围">
            {ranges.map(item => (
              <button key={item} className={range === item ? 'active' : ''} onClick={() => setRange(item)}>{item}</button>
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
            <button className={priceMode === 'normal' ? 'active' : ''} onClick={() => setPriceMode('normal')}>普通</button>
            <button className={priceMode === 'log' ? 'active' : ''} onClick={() => setPriceMode('log')}>对数</button>
          </div>
          <span className="ma ma-short">MA 5</span><span className="ma ma-mid">MA 20</span><span className="ma ma-long">MA 60</span>
        </div>
        <div className="canvas-shell" data-range={range}>
          <ChartCanvas
            symbol={selected.symbol}
            range={range}
            priceMode={priceMode}
            onCoverageChange={handleCoverageChange}
          />
        </div>
        <footer className="statusbar">
          <span>{selected.first_trade_date ?? '—'} → {selected.last_trade_date ?? '—'}</span>
          <span>{selected.rows.toLocaleString()} 根日线</span>
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
