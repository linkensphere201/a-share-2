import { memo, useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, ChevronLeft, ChevronRight, Filter, ListPlus, Play, RefreshCw, Trash2 } from 'lucide-react'
import { ChartCanvas } from './ChartCanvas'
import { MarketBoardBadge } from './MarketBoardBadge'
import { TradingSystemControls } from './TradingSystemControls'
import { TrendExplanationPanel } from './TrendExplanationPanel'
import { logError, logInfo } from './eventLogger'
import {
  deleteScreenerRun, listScreenerCandidates, listScreenerRuns, loadScreenerRun, startScreenerRun,
  type ScreenerCandidate, type ScreenerPeriod, type ScreenerRun, type ScreenerState,
} from './screenerClient'
import { loadExactTrendAnalysis, type TrendAnalysisRun } from './trendAnalysisClient'
import {
  createTradingSystemWindowState,
  tradingSystemRegistry,
  type TradingSystemWindowState,
} from './tradingSystems'
import type { ThemeDefinition } from './themeStore'

const periodLabels: Record<ScreenerPeriod, string> = { '3m': '3个月', '6m': '半年', '1y': '1年' }
const stateLabels: Record<ScreenerState, string> = {
  'critical-breakout': '临界突破', 'breakout-retest': '突破回踩', 'broken-out': '已突破',
}
const selectedRunKey = 'stock-harness.screener.selected-run.v1'
const selectedCandidateKey = 'stock-harness.screener.selected-candidate.v1'
type ResultStateFilter = ScreenerState | 'all'
export type ScreenerTargetList = { id: string; title: string; instrumentCount: number }
type ScreenerContextMenu =
  | { kind: 'run'; x: number; y: number; run: ScreenerRun }
  | { kind: 'candidate'; x: number; y: number; candidate: ScreenerCandidate; selectingTarget: boolean }

export function ScreenerWorkspace({
  theme,
  onClose,
  targetLists,
  onAddCandidateToList,
}: {
  theme: ThemeDefinition
  onClose: () => void
  targetLists: ScreenerTargetList[]
  onAddCandidateToList: (windowId: string, candidate: ScreenerCandidate) => boolean
}) {
  const [runs, setRuns] = useState<ScreenerRun[]>([])
  const [selectedRun, setSelectedRun] = useState<ScreenerRun>()
  const [candidates, setCandidates] = useState<ScreenerCandidate[]>([])
  const [selected, setSelected] = useState<ScreenerCandidate>()
  const [analysis, setAnalysis] = useState<TrendAnalysisRun | null>(null)
  const [periods, setPeriods] = useState<ScreenerPeriod[]>(['6m', '1y'])
  const [states, setStates] = useState<ScreenerState[]>(['critical-breakout', 'breakout-retest', 'broken-out'])
  const [maxResults, setMaxResults] = useState(200)
  const [resultStateFilter, setResultStateFilter] = useState<ResultStateFilter>('all')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [contextMenu, setContextMenu] = useState<ScreenerContextMenu>()

  const filteredCandidates = useMemo(() => resultStateFilter === 'all'
    ? candidates
    : candidates.filter(item => item.state === resultStateFilter), [candidates, resultStateFilter])
  const resultStateCounts = useMemo(() => Object.fromEntries(
    (['critical-breakout', 'breakout-retest', 'broken-out'] as ScreenerState[]).map(state => [
      state,
      candidates.filter(item => item.state === state).length,
    ]),
  ) as Record<ScreenerState, number>, [candidates])

  const refreshRuns = useCallback(async (preferredId?: string) => {
    const values = await listScreenerRuns()
    setRuns(values)
    setSelectedRun(current => values.find(item => item.run_id === (
      preferredId ?? current?.run_id ?? window.localStorage.getItem(selectedRunKey)
    )) ?? values[0])
  }, [])

  useEffect(() => { refreshRuns().catch(value => setError(String(value))) }, [refreshRuns])

  useEffect(() => {
    if (!selectedRun) { setCandidates([]); setSelected(undefined); return }
    setCandidates([])
    setSelected(undefined)
    setAnalysis(null)
    const controller = new AbortController()
    listScreenerCandidates(selectedRun.run_id, controller.signal).then(values => {
      setCandidates(values)
      setSelected(current => values.find(item => item.symbol === (
        current?.symbol ?? window.localStorage.getItem(selectedCandidateKey)
      )) ?? values[0])
    }).catch(value => { if ((value as Error).name !== 'AbortError') setError(String(value)) })
    return () => controller.abort()
  }, [selectedRun?.run_id, selectedRun?.status])

  useEffect(() => {
    if (selectedRun) window.localStorage.setItem(selectedRunKey, selectedRun.run_id)
  }, [selectedRun?.run_id])

  useEffect(() => {
    if (selected) window.localStorage.setItem(selectedCandidateKey, selected.symbol)
  }, [selected?.symbol])

  useEffect(() => {
    if (selected && filteredCandidates.some(item => item.symbol === selected.symbol)) return
    setSelected(filteredCandidates[0])
  }, [filteredCandidates, selected?.symbol])

  useEffect(() => {
    if (!selectedRun || selectedRun.status !== 'running') return
    const handle = window.setInterval(async () => {
      try {
        const current = await loadScreenerRun(selectedRun.run_id)
        setSelectedRun(current)
        setRuns(values => values.map(item => item.run_id === current.run_id ? current : item))
        if (current.status !== 'running') await refreshRuns(current.run_id)
      } catch (value) { setError(String(value)) }
    }, 1000)
    return () => window.clearInterval(handle)
  }, [selectedRun?.run_id, selectedRun?.status, refreshRuns])

  useEffect(() => {
    if (!selected) { setAnalysis(null); return }
    const controller = new AbortController()
    loadExactTrendAnalysis(selected.analysis_run_id, controller.signal)
      .then(setAnalysis)
      .catch(value => { if ((value as Error).name !== 'AbortError') setError(String(value)) })
    return () => controller.abort()
  }, [selected?.analysis_run_id])

  useEffect(() => {
    if (!contextMenu) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setContextMenu(undefined)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [contextMenu])

  const toggle = <T extends string>(value: T, values: T[], setValues: (values: T[]) => void) => {
    setValues(values.includes(value) ? values.filter(item => item !== value) : [...values, value])
  }

  const start = async () => {
    setError('')
    try {
      const run = await startScreenerRun({ periods, states, max_results: maxResults })
      setRuns(values => [run, ...values].slice(0, 10))
      setSelectedRun(run)
      logInfo('screener', '大斜边选股任务已启动', { runId: run.run_id })
    } catch (value) {
      const message = value instanceof Error ? value.message : String(value)
      setError(message)
      logError('screener', '大斜边选股任务启动失败', { error: message })
    }
  }

  const removeRun = async (run: ScreenerRun) => {
    if (run.status === 'running') return
    if (!window.confirm(`删除“${formatRunDate(run.as_of_date)}”？删除后无法恢复。`)) return
    setError('')
    try {
      await deleteScreenerRun(run.run_id)
      const remaining = runs.filter(item => item.run_id !== run.run_id)
      setRuns(remaining)
      if (selectedRun?.run_id === run.run_id) {
        setSelectedRun(remaining[0])
        if (remaining[0]) window.localStorage.setItem(selectedRunKey, remaining[0].run_id)
        else window.localStorage.removeItem(selectedRunKey)
      }
      setContextMenu(undefined)
      setNotice(`已删除 ${formatRunDate(run.as_of_date)}`)
      logInfo('screener', '选股结果已删除', { runId: run.run_id })
    } catch (value) {
      const message = value instanceof Error ? value.message : String(value)
      setError(message)
      logError('screener', '选股结果删除失败', { runId: run.run_id, error: message })
    }
  }

  const addCandidate = (target: ScreenerTargetList, candidate: ScreenerCandidate) => {
    const added = onAddCandidateToList(target.id, candidate)
    setContextMenu(undefined)
    setNotice(added
      ? `已将 ${candidate.name} 添加到 ${target.title}`
      : `${candidate.name} 已在 ${target.title} 中`)
  }

  const progress = selectedRun?.universe_count
    ? Math.round(selectedRun.scanned_count / selectedRun.universe_count * 100)
    : 0
  return <main className="screener-workspace">
    <header className="screener-toolbar">
      <button className="icon-button" title="返回工作台" aria-label="返回工作台" onClick={onClose}><ArrowLeft size={16}/></button>
      <span className="screener-title"><Filter size={17}/>选股器 <small>大斜边突破一期</small></span>
      <fieldset><legend>周期</legend>{(['6m', '1y'] as ScreenerPeriod[]).map(item =>
        <label key={item}><input type="checkbox" checked={periods.includes(item)} onChange={() => toggle(item, periods, setPeriods)}/>{periodLabels[item]}</label>)}</fieldset>
      <fieldset><legend>状态</legend>{(['critical-breakout', 'breakout-retest', 'broken-out'] as ScreenerState[]).map(item =>
        <label key={item}><input type="checkbox" checked={states.includes(item)} onChange={() => toggle(item, states, setStates)}/>{stateLabels[item]}</label>)}</fieldset>
      <label className="screener-limit">上限<select value={maxResults} onChange={event => setMaxResults(Number(event.target.value))}>
        {[50, 100, 200, 500].map(value => <option key={value}>{value}</option>)}
      </select></label>
      <button className="primary-button" disabled={!periods.length || !states.length || selectedRun?.status === 'running'} onClick={start}>
        {selectedRun?.status === 'running' ? <RefreshCw size={14} className="spin"/> : <Play size={14}/>}开始选股
      </button>
    </header>
    {error && <div className="screener-error">{error}</div>}
    {notice && <button className="screener-notice" onClick={() => setNotice('')}>{notice}</button>}
    <section className="screener-grid">
      <aside className="screener-runs">
        <header>每轮选股结果 <span>{runs.length}/10</span></header>
        <div className="screener-scroll">{runs.length === 0 && <div className="screener-empty compact">暂无历史结果</div>}{runs.map(run => <button key={run.run_id} className={selectedRun?.run_id === run.run_id ? 'active' : ''} onClick={() => setSelectedRun(run)} onContextMenu={event => {
          event.preventDefault()
          setContextMenu({ kind: 'run', ...menuPosition(event.clientX, event.clientY), run })
        }}>
          <span>{formatRunDate(run.as_of_date)}</span><small>{run.status === 'running' ? `${run.scanned_count}/${run.universe_count}` : run.status === 'failed' ? '失败' : `${run.candidate_count} 个标的`}</small>
          <i className={run.status}/>
        </button>)}</div>
      </aside>
      <section className="screener-results">
        <header><span>选股结果</span><small>{selectedRun?.as_of_date ?? '尚未运行'} · {selectedRun?.status === 'running' ? `扫描 ${progress}%` : `${candidates.length} 个`}</small></header>
        {selectedRun?.status === 'running' && <div className="screener-progress"><i style={{ width: `${progress}%` }}/></div>}
        <div className="screener-quick-filters" role="group" aria-label="结果快速过滤">
          <button className={resultStateFilter === 'all' ? 'active' : ''} aria-label="快速过滤：全部" onClick={() => setResultStateFilter('all')}>全部 <small>{candidates.length}</small></button>
          {(['critical-breakout', 'breakout-retest', 'broken-out'] as ScreenerState[]).map(state => <button
            key={state}
            className={resultStateFilter === state ? 'active' : ''}
            aria-label={`快速过滤：${stateLabels[state]}`}
            onClick={() => setResultStateFilter(state)}
          >{stateLabels[state]} <small>{resultStateCounts[state]}</small></button>)}
        </div>
        <div className="screener-result-head"><span>#</span><span>标的</span><span>周期/状态</span><span>得分</span></div>
        <div className="screener-scroll">{selectedRun?.status === 'failed' && <div className="screener-empty compact error">{selectedRun.error ?? '选股任务失败'}</div>}{selectedRun?.status === 'succeeded' && candidates.length === 0 && <div className="screener-empty compact">本轮没有符合条件的标的</div>}{candidates.length > 0 && filteredCandidates.length === 0 && <div className="screener-empty compact">当前状态没有符合条件的标的</div>}{filteredCandidates.map(item => <button key={item.symbol} className={selected?.symbol === item.symbol ? 'active' : ''} onClick={() => setSelected(item)} onContextMenu={event => {
          event.preventDefault()
          setSelected(item)
          setContextMenu({ kind: 'candidate', ...menuPosition(event.clientX, event.clientY), candidate: item, selectingTarget: false })
        }}>
          <span>{item.rank}</span><span><span className="instrument-name-line"><b>{item.name}</b><MarketBoardBadge instrument={item}/></span><small>{item.symbol}</small></span><span><b>{periodLabels[item.evidence.period]}</b><small>{stateLabels[item.state]}</small></span><span>{item.score.toFixed(1)}</span>
        </button>)}</div>
      </section>
      <section className="screener-chart-pane">
        <header>{selected ? <><span className="instrument-name-line"><span>{selected.name}</span><MarketBoardBadge instrument={selected}/></span><small>{selected.line_code} · {periodLabels[selected.evidence.period]} · {stateLabels[selected.state]}</small></> : <span>个股 K 线</span>}</header>
        <div className="screener-chart-body">{selected && analysis
          ? <ScreenerChart key={selected.analysis_run_id} candidate={selected} analysis={analysis} asOfDate={selectedRun?.as_of_date} theme={theme}/>
          : <div className="screener-empty">选择一条结果查看 K 线与形态分析</div>}</div>
        {selected && <footer className="screener-evidence">
          <span><small>边界</small>{selected.evidence.projected_price.toFixed(2)}</span>
          <span><small>收盘</small>{latestClose(selected).toFixed(2)}</span>
          <span><small>距斜边</small>{signed(selected.evidence.distance_percent)}%</span>
          <span><small>失效位</small>{selected.evidence.invalidation_price.toFixed(2)}</span>
          <span><small>目标位</small>{selected.evidence.first_target_price.toFixed(2)}</span>
          <span><small>盈亏比</small>{selected.evidence.first_risk_reward?.toFixed(2) ?? '—'}</span>
          <span><small>小周期 14</small>{signed(selected.evidence.small_14.return_percent)}%</span>
          <span><small>中周期 28</small>{signed(selected.evidence.medium_28.return_percent)}%</span>
        </footer>}
      </section>
    </section>
    {contextMenu && <div className="screener-context-layer" onPointerDown={event => {
      if (event.target === event.currentTarget) setContextMenu(undefined)
    }} onContextMenu={event => event.preventDefault()}>
      <div className="screener-context-menu" role="menu" style={{ left: contextMenu.x, top: contextMenu.y }}>
        {contextMenu.kind === 'run' ? <>
          <header>{formatRunDate(contextMenu.run.as_of_date)}</header>
          <button role="menuitem" className="danger" disabled={contextMenu.run.status === 'running'} onClick={() => void removeRun(contextMenu.run)}>
            <Trash2 size={14}/>删除本轮结果
          </button>
        </> : !contextMenu.selectingTarget ? <>
          <header><span className="instrument-name-line"><span>{contextMenu.candidate.name}</span><MarketBoardBadge instrument={contextMenu.candidate}/></span></header>
          <button role="menuitem" onClick={() => setContextMenu({ ...contextMenu, selectingTarget: true })}>
            <ListPlus size={14}/>添加到…<ChevronRight size={13}/>
          </button>
        </> : <>
          <button role="menuitem" className="context-back" onClick={() => setContextMenu({ ...contextMenu, selectingTarget: false })}>
            <ChevronLeft size={13}/>选择目标列表
          </button>
          {targetLists.length === 0 && <span className="context-empty">当前窗体组没有可写的固定列表</span>}
          {targetLists.map(target => <button role="menuitem" key={target.id} onClick={() => addCandidate(target, contextMenu.candidate)}>
            <span>{target.title}</span><small>{target.instrumentCount} 个标的</small>
          </button>)}
        </>}
      </div>
    </div>}
  </main>
}

const ScreenerChart = memo(function ScreenerChart({
  candidate,
  analysis,
  asOfDate,
  theme,
}: {
  candidate: ScreenerCandidate
  analysis: TrendAnalysisRun
  asOfDate?: string
  theme: ThemeDefinition
}) {
  const [trendState, setTrendState] = useState<TradingSystemWindowState>(() => ({
    ...createTradingSystemWindowState(tradingSystemRegistry.get('trend')!),
    enabled: true,
    analysisStatus: 'current',
  }))
  const [explanationOpen, setExplanationOpen] = useState(false)
  const [highlightedItemId, setHighlightedItemId] = useState<string | undefined>(candidate.line_item_id)
  const layers = trendState.layers
  return <div className={explanationOpen ? 'screener-chart-runtime explanation-open' : 'screener-chart-runtime'}>
    <ChartCanvas
      symbol={candidate.symbol}
      instrumentName={candidate.name}
      instrumentKind="stock"
      focused
      theme={theme}
      range="1Y"
      priceMode="normal"
      volumeVisible
      indicator="none"
      settlementVisible={false}
      openInterestVisible={false}
      toolbarContent={<TradingSystemControls
        embedded
        instrumentKind="stock"
        state={trendState}
        analysisRun={analysis}
        onChange={setTrendState}
        onRecalculate={() => undefined}
        recalculationAvailable={false}
        recalculationDisabledReason="历史选股结果使用当轮固化分析，不支持重新测算"
        explanationOpen={explanationOpen}
        onExplanationOpenChange={open => {
          setExplanationOpen(open)
          if (!open) setHighlightedItemId(candidate.line_item_id)
        }}
      />}
      trendAnalysisEnabled={trendState.enabled}
      shortTrendLinesVisible={layers['short-trend-lines'] !== false}
      mediumTrendLinesVisible={layers['medium-trend-lines'] !== false}
      longTrendLinesVisible={layers['long-trend-lines'] !== false}
      keyLevelsVisible={layers['key-levels'] !== false}
      volumeZonesVisible={layers['volume-zones'] !== false}
      patternsVisible={layers.patterns !== false}
      breakoutStateVisible={layers['breakout-state'] !== false}
      trendIsolation={trendState.isolate}
      asOfDate={asOfDate}
      trendAnalysisOverride={trendState.enabled ? analysis : null}
      highlightedAnalysisItemId={highlightedItemId}
    />
    {explanationOpen && <TrendExplanationPanel
      run={analysis}
      onHighlightItemChange={itemId => setHighlightedItemId(itemId ?? candidate.line_item_id)}
      onClose={() => {
        setExplanationOpen(false)
        setHighlightedItemId(candidate.line_item_id)
      }}
    />}
  </div>
})

function formatRunDate(value: string) { return value.replaceAll('-', '').slice(4) + ' 选股结果' }
function signed(value: number) { return `${value > 0 ? '+' : ''}${value.toFixed(2)}` }
function latestClose(value: ScreenerCandidate) {
  return value.evidence.projected_price * (1 + value.evidence.distance_percent / 100)
}

function menuPosition(x: number, y: number) {
  return {
    x: Math.max(8, Math.min(x, window.innerWidth - 230)),
    y: Math.max(8, Math.min(y, window.innerHeight - 260)),
  }
}
