import { useEffect, useRef, useState } from 'react'
import { Plus, Search } from 'lucide-react'
import type { Instrument } from './workspace'

type BrowseClass = 'all' | 'custom-group' | 'stock' | 'etf' | 'index' | 'concept' | 'industry' | 'sector'

type InstrumentBrowserProps = {
  selectedSymbols: Set<string>
  onSelect: (instrument: Instrument) => void
  excludeCustomGroups?: boolean
  searchLabel: string
  placeholder: string
}

const browseClasses: { value: BrowseClass; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'custom-group', label: '自选集合' },
  { value: 'concept', label: '概念板块' },
  { value: 'industry', label: '行业板块' },
  { value: 'etf', label: 'ETF' },
  { value: 'index', label: '指数' },
  { value: 'stock', label: '个股' },
  { value: 'sector', label: '其他板块' },
]

export function InstrumentBrowser({
  selectedSymbols,
  onSelect,
  excludeCustomGroups = false,
  searchLabel,
  placeholder,
}: InstrumentBrowserProps) {
  const [classification, setClassification] = useState<BrowseClass>('all')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Instrument[]>([])
  const [nextOffset, setNextOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)
  const generationRef = useRef(0)
  const loadMoreControllerRef = useRef<AbortController | undefined>(undefined)
  const visibleBrowseClasses = excludeCustomGroups
    ? browseClasses.filter(item => item.value !== 'custom-group')
    : browseClasses

  useEffect(() => {
    if (excludeCustomGroups && classification === 'custom-group') setClassification('all')
  }, [classification, excludeCustomGroups])

  useEffect(() => {
    const generation = ++generationRef.current
    loadMoreControllerRef.current?.abort()
    loadMoreControllerRef.current = undefined
    const controller = new AbortController()
    const handle = window.setTimeout(() => {
      setLoading(true)
      setFailed(false)
        fetchInstrumentPage(query, classification, 0, controller.signal)
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
  }, [classification, excludeCustomGroups, query])

  const loadMore = () => {
    if (loading || !hasMore) return
    const generation = generationRef.current
    const controller = new AbortController()
    loadMoreControllerRef.current?.abort()
    loadMoreControllerRef.current = controller
    setLoading(true)
    setFailed(false)
    fetchInstrumentPage(query, classification, nextOffset, controller.signal)
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
    <div className="instrument-editor-results">
      {loading && results.length === 0 && <div className="instrument-editor-empty">正在加载标的</div>}
      {!loading && failed && <div className="instrument-editor-empty error">标的加载失败</div>}
      {!loading && !failed && results.length === 0 && <div className="instrument-editor-empty">没有匹配标的</div>}
      {results.map(item => <button
        key={item.symbol}
        disabled={selectedSymbols.has(item.symbol)}
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
        <Plus size={14}/>
      </button>)}
      {hasMore && <button className="instrument-browser-more" disabled={loading} onClick={loadMore}>
        {loading ? '加载中' : '加载更多'}
      </button>}
    </div>
  </section>
}

type InstrumentPage = { items: Instrument[]; has_more: boolean; next_offset: number }

async function fetchInstrumentPage(
  query: string,
  classification: BrowseClass,
  offset: number,
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
  if (classification !== 'all') params.set('classification', classification)
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
