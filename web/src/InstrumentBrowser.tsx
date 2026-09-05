import { useEffect, useRef, useState } from 'react'
import { Plus, Search, Tags } from 'lucide-react'
import type { Instrument } from './workspace'

type BrowseClass = 'all' | 'custom-group' | 'stock' | 'etf' | 'index' | 'custom-index' | 'concept' | 'industry' | 'sector' | 'futures'
type FuturesType = 'all' | 'futures-contract' | 'futures-continuous'
type FuturesLifecycle = 'all' | 'pending' | 'listed' | 'trading' | 'expired' | 'delivered' | 'delisted'
type FuturesSeriesKind = 'all' | 'main' | 'continuous'

type InstrumentBrowserProps = {
  selectedSymbols: Set<string>
  onSelect: (instrument: Instrument) => void
  excludeCustomGroups?: boolean
  stockOnly?: boolean
  searchLabel: string
  placeholder: string
  actionMode?: 'add' | 'edit-tags'
}

const browseClasses: { value: BrowseClass; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'custom-group', label: '自选集合' },
  { value: 'concept', label: '概念板块' },
  { value: 'industry', label: '行业板块' },
  { value: 'etf', label: 'ETF' },
  { value: 'index', label: '指数' },
  { value: 'custom-index', label: '自定义指数' },
  { value: 'futures', label: '期货' },
  { value: 'stock', label: '个股' },
  { value: 'sector', label: '其他板块' },
]

export function InstrumentBrowser({
  selectedSymbols,
  onSelect,
  excludeCustomGroups = false,
  stockOnly = false,
  searchLabel,
  placeholder,
  actionMode = 'add',
}: InstrumentBrowserProps) {
  const [classification, setClassification] = useState<BrowseClass>(stockOnly ? 'stock' : 'all')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Instrument[]>([])
  const [nextOffset, setNextOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const [futuresType, setFuturesType] = useState<FuturesType>('futures-contract')
  const [futuresExchange, setFuturesExchange] = useState('')
  const [futuresProduct, setFuturesProduct] = useState('')
  const [futuresLifecycle, setFuturesLifecycle] = useState<FuturesLifecycle>('trading')
  const [futuresSeriesKind, setFuturesSeriesKind] = useState<FuturesSeriesKind>('all')
  const [futuresFacets, setFuturesFacets] = useState<FuturesFacets>({ exchanges: [], products: [] })
  const generationRef = useRef(0)
  const loadMoreControllerRef = useRef<AbortController | undefined>(undefined)
  const visibleBrowseClasses = stockOnly
    ? browseClasses.filter(item => item.value === 'stock')
    : excludeCustomGroups
      ? browseClasses.filter(item => item.value !== 'custom-group')
      : browseClasses

  useEffect(() => {
    if (stockOnly && classification !== 'stock') setClassification('stock')
    else if (excludeCustomGroups && classification === 'custom-group') setClassification('all')
  }, [classification, excludeCustomGroups, stockOnly])

  useEffect(() => {
    if (classification !== 'futures' || futuresFacets.products.length > 0) return
    const controller = new AbortController()
    fetch('/api/futures/search-facets', { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<FuturesFacets>
      })
      .then(setFuturesFacets)
      .catch(error => {
        if ((error as Error).name !== 'AbortError') setFailed(true)
      })
    return () => controller.abort()
  }, [classification, futuresFacets.products.length])

  useEffect(() => {
    const generation = ++generationRef.current
    loadMoreControllerRef.current?.abort()
    loadMoreControllerRef.current = undefined
    const controller = new AbortController()
    const handle = window.setTimeout(() => {
      setLoading(true)
      setFailed(false)
        fetchInstrumentPage(query, classification, 0, futuresFilters(), controller.signal)
        .then(body => {
          if (generation !== generationRef.current) return
          setResults(filterResults(body.items, excludeCustomGroups))
          setNextOffset(body.next_offset)
          setHasMore(body.has_more)
        })
        .catch(error => {
          if ((error as Error).name !== 'AbortError' && generation === generationRef.current) {
            setResults([])
            setFailed(true)
          }
        })
        .finally(() => {
          if (!controller.signal.aborted && generation === generationRef.current) setLoading(false)
        })
    }, query.trim() ? 120 : 0)
    return () => {
      window.clearTimeout(handle)
      controller.abort()
    }
  }, [
    classification, excludeCustomGroups, query, futuresType, futuresExchange,
    futuresProduct, futuresLifecycle, futuresSeriesKind,
  ])

  const loadMore = () => {
    if (loading || !hasMore) return
    const generation = generationRef.current
    const controller = new AbortController()
    loadMoreControllerRef.current?.abort()
    loadMoreControllerRef.current = controller
    setLoading(true)
    setFailed(false)
    fetchInstrumentPage(query, classification, nextOffset, futuresFilters(), controller.signal)
      .then(body => {
        if (generation !== generationRef.current) return
        setResults(current => uniqueBySymbol([...current, ...filterResults(body.items, excludeCustomGroups)]))
        setNextOffset(body.next_offset)
        setHasMore(body.has_more)
      })
      .catch(error => {
        if ((error as Error).name !== 'AbortError' && generation === generationRef.current) setFailed(true)
      })
      .finally(() => {
        if (loadMoreControllerRef.current === controller) loadMoreControllerRef.current = undefined
        if (!controller.signal.aborted && generation === generationRef.current) setLoading(false)
      })
  }

  const futuresFilters = (): FuturesFilters => ({
    type: futuresType,
    exchange: futuresExchange,
    product: futuresProduct,
    lifecycle: futuresLifecycle,
    seriesKind: futuresSeriesKind,
  })

  return <section className="instrument-browser" aria-label="标的分类浏览">
    <div className="instrument-editor-search">
      <label><Search size={15}/><input
        value={query}
        onChange={event => setQuery(event.target.value)}
        placeholder={placeholder}
        aria-label={searchLabel}
      /></label>
    </div>
    <div className="instrument-browser-filters" role="tablist" aria-label="标的分类">
      {visibleBrowseClasses.map(item => <button
        key={item.value}
        role="tab"
        aria-selected={classification === item.value}
        className={classification === item.value ? 'active' : ''}
        onClick={() => setClassification(item.value)}
      >{item.label}</button>)}
    </div>
    {classification === 'futures' && <div className="instrument-browser-futures-filters">
      <label>类型<select aria-label="期货类型" value={futuresType} onChange={event => setFuturesType(event.target.value as FuturesType)}>
        <option value="all">全部</option><option value="futures-contract">真实合约</option><option value="futures-continuous">连续合约</option>
      </select></label>
      <label>交易所<select aria-label="期货交易所" value={futuresExchange} onChange={event => { setFuturesExchange(event.target.value); setFuturesProduct('') }}>
        <option value="">全部</option>{futuresFacets.exchanges.map(value => <option key={value} value={value}>{value}</option>)}
      </select></label>
      <label>品种<select aria-label="期货品种" value={futuresProduct} onChange={event => setFuturesProduct(event.target.value)}>
        <option value="">全部</option>{futuresFacets.products.filter(item => !futuresExchange || item.exchange === futuresExchange).map(item => <option key={item.symbol} value={item.code}>{item.name} {item.code}</option>)}
      </select></label>
      {futuresType !== 'futures-continuous' && <label>状态<select aria-label="期货合约状态" value={futuresLifecycle} onChange={event => setFuturesLifecycle(event.target.value as FuturesLifecycle)}>
        <option value="all">全部</option><option value="trading">交易中</option><option value="listed">已上市</option><option value="pending">待上市</option><option value="expired">已到期</option><option value="delivered">已交割</option><option value="delisted">已退市</option>
      </select></label>}
      {futuresType !== 'futures-contract' && <label>连续类型<select aria-label="期货连续类型" value={futuresSeriesKind} onChange={event => setFuturesSeriesKind(event.target.value as FuturesSeriesKind)}>
        <option value="all">全部</option><option value="main">主力</option><option value="continuous">连续</option>
      </select></label>}
    </div>}
    <div className="instrument-editor-results">
      {loading && results.length === 0 && <div className="instrument-editor-empty">正在加载标的</div>}
      {!loading && failed && <div className="instrument-editor-empty error">标的加载失败</div>}
      {!loading && !failed && results.length === 0 && <div className="instrument-editor-empty">没有匹配标的</div>}
      {results.map(item => {
        const noDailyHistory = (
          item.kind === 'futures-contract' || item.kind === 'futures-continuous'
        ) && item.rows <= 0
        const unavailableReason = item.kind === 'futures-product'
          ? '品种目录不可直接加入窗口，请选择真实或连续合约'
          : noDailyHistory ? '暂无日线数据，暂不可加入窗口' : undefined
        return <button
          key={item.symbol}
          disabled={selectedSymbols.has(item.symbol) || item.kind === 'futures-product' || noDailyHistory}
          title={unavailableReason}
          onClick={() => onSelect(item)}
        >
        <span className="instrument-result-identity">
          <strong>{item.name}</strong>
          <small>{instrumentSecondaryLabel(item)}</small>
        </span>
        <span className="instrument-result-meta">
          <b className={`instrument-type-badge type-${item.classification ?? item.kind}`}>{instrumentClassLabel(item)}</b>
          <small>{item.source_label ?? instrumentSourceLabel(item)}</small>
        </span>
        {actionMode === 'edit-tags' ? <Tags size={14}/> : <Plus size={14}/>}
        </button>
      })}
      {hasMore && <button className="instrument-browser-more" disabled={loading} onClick={loadMore}>
        {loading ? '加载中' : '加载更多'}
      </button>}
    </div>
  </section>
}

type InstrumentPage = { items: Instrument[]; has_more: boolean; next_offset: number }
type FuturesProductFacet = { symbol: string; code: string; name: string; exchange: string; active: boolean }
type FuturesFacets = { exchanges: string[]; products: FuturesProductFacet[] }
type FuturesFilters = {
  type: FuturesType
  exchange: string
  product: string
  lifecycle: FuturesLifecycle
  seriesKind: FuturesSeriesKind
}

async function fetchInstrumentPage(
  query: string,
  classification: BrowseClass,
  offset: number,
  futures: FuturesFilters,
  signal?: AbortSignal,
): Promise<InstrumentPage> {
  if (classification === 'custom-group') {
    const params = new URLSearchParams({ query })
    const response = await fetch(`/api/custom-groups?${params}`, { signal })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const body = await response.json() as { items: CustomGroupSummary[] }
    return { items: body.items.map(customGroupInstrument), has_more: false, next_offset: body.items.length }
  }
  const params = new URLSearchParams({ query, limit: '80', offset: String(offset) })
  if (classification === 'futures') {
    params.set('classification', futures.type === 'all' ? 'futures' : futures.type)
    if (futures.exchange) params.set('exchange', futures.exchange)
    if (futures.product) params.set('futures_product', futures.product)
    if (futures.type !== 'futures-continuous' && futures.lifecycle !== 'all') params.set('futures_lifecycle', futures.lifecycle)
    if (futures.type !== 'futures-contract' && futures.seriesKind !== 'all') params.set('futures_series_kind', futures.seriesKind)
  } else if (classification !== 'all') params.set('classification', classification)
  const response = await fetch(`/api/instruments?${params}`, { signal })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const body = await response.json() as Partial<InstrumentPage> & { items: Instrument[] }
  return {
    items: body.items,
    has_more: body.has_more ?? body.items.length >= 80,
    next_offset: body.next_offset ?? offset + body.items.length,
  }
}

type CustomGroupSummary = {
  id: string
  symbol: string
  name: string
  member_count: number
  average_change_percent?: number | null
}

function customGroupInstrument(group: CustomGroupSummary): Instrument {
  return {
    symbol: group.symbol,
    name: group.name,
    kind: 'custom-group',
    exchange: 'LOCAL',
    rows: group.member_count,
    member_count: group.member_count,
    average_change_percent: group.average_change_percent,
    classification: 'custom-group',
    classification_label: '自选集合',
    source_label: '本地',
  }
}

export function instrumentSecondaryLabel(item: Instrument): string {
  if (item.kind !== 'custom-group') return item.symbol
  const count = item.member_count ?? item.rows
  const average = item.average_change_percent
  const change = average === null || average === undefined
    ? '--'
    : `${average > 0 ? '+' : ''}${average.toFixed(2)}%`
  return `${count} 只标的 · 平均涨跌幅 ${change}`
}

function filterResults(items: Instrument[], excludeCustomGroups: boolean): Instrument[] {
  return excludeCustomGroups ? items.filter(item => item.kind !== 'custom-group') : items
}

function uniqueBySymbol(items: Instrument[]): Instrument[] {
  return items.filter((item, index) => items.findIndex(candidate => candidate.symbol === item.symbol) === index)
}

export function instrumentClassLabel(item: Instrument): string {
  if (item.classification_label) return item.classification_label
  if (item.kind === 'custom-group') return '自选集合'
  if (item.kind === 'stock') return '个股'
  if (item.kind === 'etf') return 'ETF'
  if (item.kind === 'index') return '指数'
  if (item.kind === 'custom-index') return '自定义指数'
  if (item.category === '概念板块') return '概念板块'
  if (item.category === '行业板块') return '行业板块'
  return item.kind === 'sector' ? '其他板块' : item.kind
}

function instrumentSourceLabel(item: Instrument): string {
  if (item.source_system === 'eastmoney') return '东财'
  if (item.source_system === 'ths') return '同花顺'
  if (item.exchange === 'SI') return '申万'
  return item.exchange
}
