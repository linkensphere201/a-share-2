import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ChevronLeft, ChevronRight, Filter, ListPlus, Play, RefreshCw, Trash2, X } from 'lucide-react'
import { ChartCanvas } from './ChartCanvas'
import { MarketBoardBadge } from './MarketBoardBadge'
import { fetchInstrumentBoardMemberships, type InstrumentBoardMembership } from './boardTags'
import { TradingSystemControls } from './TradingSystemControls'
import { TrendExplanationPanel } from './TrendExplanationPanel'
import { accumulationStageLabel, accumulationPatternLabel, accumulationStyleLabel, firstPullbackStageLabel, firstPullbackVolumeLabel, lowBaseMaturityLabel } from './trendExplanation'
import { useAnalysisOverlayVisibility, useAnalysisLayers } from './AnalysisOverlayToggle'
import { logError, logInfo } from './eventLogger'
import {
  deleteScreenerRun, listScreenerCandidates, listScreenerRuns,
  loadScreenerRun, startScreenerRun,
  type ScreenerCandidate, type ScreenerPeriod, type ScreenerRun, type ScreenerState,
  type ScreenerStrategyId,
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
  accumulating: '堆量蓄势',
  'pullback-observation': firstPullbackStageLabel('pullback-observation'),
  'pullback-confirmed': firstPullbackStageLabel('pullback-confirmed'),
}
const strategyLabels: Record<ScreenerStrategyId, string> = {
  'major-descending-breakout': '大斜边突破',
  'volume-accumulation-20d': '20日堆量蓄势',
  'strong-first-pullback': '强势股首次回踩',
  'low-base-platform-pullback': '低位平台回踩',
}
const trendStates: ScreenerState[] = ['critical-breakout', 'breakout-retest', 'broken-out']
const pullbackStates: ScreenerState[] = ['pullback-confirmed', 'pullback-observation']
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
  const [strategyId, setStrategyId] = useState<ScreenerStrategyId>('major-descending-breakout')
  const [periods, setPeriods] = useState<ScreenerPeriod[]>(['6m', '1y'])
  const [states, setStates] = useState<ScreenerState[]>(['critical-breakout', 'breakout-retest', 'broken-out'])
  const [maxResults, setMaxResults] = useState(200)
  const [resultStateFilter, setResultStateFilter] = useState<ResultStateFilter>('all')
  const [platformStyle, setPlatformStyle] = useState('all')
  const [maturity, setMaturity] = useState('all')
  useEffect(() => {
    setMaturity(['low-base-platform-pullback-v3', 'low-base-platform-pullback-v4'].includes(selectedRun?.strategy_version ?? '') ? 'platform-retest' : 'all')
  }, [selectedRun?.run_id, selectedRun?.strategy_version])
  useEffect(() => {
    setPlatformStyle(selectedRun?.strategy_version === 'volume-accumulation-20d-v7' ? 'compact-platform' : 'all')
  }, [selectedRun?.run_id, selectedRun?.strategy_version])
  const [historicalOnly, setHistoricalOnly] = useState(false)
  const [boards, setBoards] = useState<Record<string, InstrumentBoardMembership[]>>({})
  const [boardFilter, setBoardFilter] = useState<string[]>([])
  const [boardQuery, setBoardQuery] = useState('')
  const [boardStatus, setBoardStatus] = useState<'loading' | 'ready' | 'error'>('ready')
  const [boardRetry, setBoardRetry] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [contextMenu, setContextMenu] = useState<ScreenerContextMenu>()
  const [startingStrategy, setStartingStrategy] = useState<ScreenerStrategyId | null>(null)
  const startingRef = useRef(false)
  const runListEpoch = useRef(0)
  const runningRunId = runs.find(run => run.status === 'running')?.run_id
  const hasRunningRun = runningRunId !== undefined

  const stateCandidates = useMemo(() => candidates.filter(item =>
    (resultStateFilter === 'all' || item.state === resultStateFilter)
    && (platformStyle === 'all' || item.evidence.platform_style === platformStyle)
    && (maturity === 'all' || item.evidence.shape_maturity === maturity)),
  [candidates, resultStateFilter, platformStyle, maturity])
  const historicalCandidates = useMemo(() => stateCandidates.filter(
    item => item.recognition?.tags.includes('historical'),
  ), [stateCandidates])
  const taggedCandidates = historicalOnly ? historicalCandidates : stateCandidates
  const filteredCandidates = useMemo(() => boardFilter.length === 0 ? taggedCandidates
    : taggedCandidates.filter(item => boards[item.symbol]?.some(board => boardFilter.includes(board.board_symbol))),
  [taggedCandidates, boards, boardFilter])
  const boardOptions = useMemo(() => {
    const options = new Map<string, InstrumentBoardMembership & { count: number }>()
    candidates.forEach(item => boards[item.symbol]?.forEach(board => {
      if (!options.has(board.board_symbol)) options.set(board.board_symbol, { ...board, count: 0 })
    }))
    taggedCandidates.forEach(item => new Set(boards[item.symbol]?.map(board => board.board_symbol)).forEach(symbol => {
      const option = options.get(symbol)
      if (option) option.count++
    }))
    return [...options.values()].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, 'zh-CN') || a.board_symbol.localeCompare(b.board_symbol))
  }, [candidates, taggedCandidates, boards])
  const duplicateBoardNames = useMemo(() => {
    const seen = new Set<string>(), duplicates = new Set<string>()
    boardOptions.forEach(board => {
      if (seen.has(board.name)) duplicates.add(board.name)
      seen.add(board.name)
    })
    return duplicates
  }, [boardOptions])
  const visibleBoards = boardOptions.filter(board => `${board.name} ${board.board_symbol}`.toLowerCase().includes(boardQuery.trim().toLowerCase()) || boardFilter.includes(board.board_symbol))
  const recognition = candidates[0]?.recognition
  const resultStateCounts = useMemo(() => Object.fromEntries(
    ([...trendStates, ...pullbackStates, 'accumulating'] as ScreenerState[]).map(state => [
      state,
      candidates.filter(item => item.state === state).length,
    ]),
  ) as Record<ScreenerState, number>, [candidates])

  const refreshRuns = useCallback(async (preferredId?: string) => {
    const epoch = runListEpoch.current
    const values = await listScreenerRuns()
    if (epoch !== runListEpoch.current) return
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
      if (controller.signal.aborted) return
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

  useEffect(() => { setResultStateFilter('all') }, [selectedRun?.strategy_id])
  useEffect(() => { setHistoricalOnly(false) }, [selectedRun?.run_id])
  useEffect(() => { setBoardFilter([]); setBoardQuery('') }, [selectedRun?.run_id])

  useEffect(() => {
    const controller = new AbortController()
    setBoards({})
    if (!candidates.length) { setBoardStatus('ready'); return () => controller.abort() }
    setBoardStatus('loading')
    fetchInstrumentBoardMemberships(candidates.map(item => item.symbol), controller.signal)
      .then(values => {
        if (controller.signal.aborted) return
        setBoards(values)
        setBoardStatus('ready')
      }).catch(() => { if (!controller.signal.aborted) setBoardStatus('error') })
    return () => controller.abort()
  }, [candidates, boardRetry])

  useEffect(() => {
    if (selected) window.localStorage.setItem(selectedCandidateKey, selected.symbol)
  }, [selected?.symbol])

  useEffect(() => {
    if (selected && filteredCandidates.some(item => item.symbol === selected.symbol)) return
    setSelected(filteredCandidates[0])
  }, [filteredCandidates, selected?.symbol])

  useEffect(() => {
    if (!runningRunId) return
    const controller = new AbortController()
    let handle: number
    const poll = async () => {
      try {
        const current = await loadScreenerRun(runningRunId, controller.signal)
        if (controller.signal.aborted) return
        setSelectedRun(selected => selected?.run_id === current.run_id ? current : selected)
        setRuns(values => values.map(item => item.run_id === current.run_id ? current : item))
        if (current.status !== 'running') { await refreshRuns(); return }
      } catch (value) { if (!controller.signal.aborted) setError(String(value)) }
      if (!controller.signal.aborted) handle = window.setTimeout(poll, 1000)
    }
    handle = window.setTimeout(poll, 1000)
    return () => { controller.abort(); window.clearTimeout(handle) }
  }, [runningRunId, refreshRuns])

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
    if (startingRef.current || hasRunningRun) return
    startingRef.current = true
    setStartingStrategy(strategyId)
    setError('')
    try {
      const run = await startScreenerRun({ strategy_id: strategyId, periods, states, max_results: maxResults })
      runListEpoch.current++
      setRuns(values => [run, ...values].slice(0, 10))
      setSelectedRun(run)
      logInfo('screener', '选股任务已启动', { runId: run.run_id, strategyId })
    } catch (value) {
      const message = value instanceof Error ? value.message : String(value)
      setError(message)
      logError('screener', '选股任务启动失败', { error: message, strategyId })
    } finally {
      startingRef.current = false
      setStartingStrategy(null)
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
      <span className="screener-title"><Filter size={17}/>选股器 <small>{strategyLabels[strategyId]}</small></span>
      <label className="screener-limit">策略<select value={strategyId} onChange={event => {
        setStrategyId(event.target.value as ScreenerStrategyId)
        setResultStateFilter('all')
      }}>
        {Object.entries(strategyLabels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select></label>
      {strategyId === 'major-descending-breakout' && <fieldset><legend>周期</legend>{(['6m', '1y'] as ScreenerPeriod[]).map(item =>
        <label key={item}><input type="checkbox" checked={periods.includes(item)} onChange={() => toggle(item, periods, setPeriods)}/>{periodLabels[item]}</label>)}</fieldset>}
      {strategyId === 'major-descending-breakout' && <fieldset><legend>状态</legend>{trendStates.map(item =>
        <label key={item}><input type="checkbox" checked={states.includes(item)} onChange={() => toggle(item, states, setStates)}/>{stateLabels[item]}</label>)}</fieldset>}
      <label className="screener-limit">上限<select value={maxResults} onChange={event => setMaxResults(Number(event.target.value))}>
        {[50, 100, 200, 500].map(value => <option key={value}>{value}</option>)}
      </select></label>
      <button className="primary-button" aria-busy={startingStrategy !== null} disabled={startingStrategy !== null || (strategyId === 'major-descending-breakout' && (!periods.length || !states.length)) || hasRunningRun} onClick={start}>
        {startingStrategy !== null || hasRunningRun ? <RefreshCw size={14} className="spin"/> : <Play size={14}/>}开始选股
      </button>
    </header>
    {error && <div className="screener-error">{error}</div>}
    {notice && <button className="screener-notice" onClick={() => setNotice('')}>{notice}</button>}
    <section className="screener-grid">
      <aside className="screener-runs">
        <header>每轮选股结果 <span>{runs.length}/10</span></header>
        <div className="screener-scroll">
          {startingStrategy !== null && <div className="screener-starting" role="status">
            <RefreshCw size={14} className="spin"/><span>正在创建选股任务<small>{strategyLabel(startingStrategy)}</small></span>
          </div>}
          {runs.length === 0 && startingStrategy === null && <div className="screener-empty compact">暂无历史结果</div>}{runs.map(run => <button key={run.run_id} className={selectedRun?.run_id === run.run_id ? 'active' : ''} onClick={() => setSelectedRun(run)} onContextMenu={event => {
          event.preventDefault()
          setContextMenu({ kind: 'run', ...menuPosition(event.clientX, event.clientY), run })
        }}>
          <span>{formatRunDate(run.as_of_date)}</span><small>{strategyLabel(run.strategy_id)} · {run.status === 'running' ? `${run.scanned_count}/${run.universe_count}` : run.status === 'failed' ? '失败' : `${run.candidate_count} 个标的`}</small>
          <i className={run.status}/>
        </button>)}</div>
      </aside>
      <section className="screener-results">
        <header><span>选股结果</span><small>{selectedRun?.as_of_date ?? '尚未运行'} · {selectedRun?.status === 'running' ? `扫描 ${progress}%` : `${candidates.length} 个`}</small></header>
        {selectedRun?.status === 'running' && <div className="screener-progress"><i style={{ width: `${progress}%` }}/></div>}
        <div className="screener-quick-filters" role="group" aria-label="结果快速过滤">
          <button className={resultStateFilter === 'all' ? 'active' : ''} aria-label="快速过滤：全部" onClick={() => setResultStateFilter('all')}>全部 <small>{candidates.length}</small></button>
          {(selectedRun?.strategy_id === 'volume-accumulation-20d' ? ['accumulating'] as ScreenerState[] : ['strong-first-pullback', 'low-base-platform-pullback'].includes(selectedRun?.strategy_id ?? '') ? pullbackStates : trendStates).map(state => <button
            key={state}
            className={resultStateFilter === state ? 'active' : ''}
            aria-label={`快速过滤：${stateLabels[state]}`}
            onClick={() => setResultStateFilter(state)}
          >{stateLabels[state]} <small>{resultStateCounts[state]}</small></button>)}
        </div>
        {candidates.some(item => item.evidence.shape_maturity) && <div className="screener-quick-filters" role="group" aria-label="形态成熟度过滤">
          {['platform-retest', 'platform-established', 'forming', 'all'].map(value => <button key={value}
            className={maturity === value ? 'active' : ''} aria-pressed={maturity === value}
            onClick={() => setMaturity(value)}>{value === 'all' ? '全部成熟度' : lowBaseMaturityLabel(value)}
            <small>{candidates.filter(item => value === 'all' || item.evidence.shape_maturity === value).length}</small>
          </button>)}
        </div>}
        {candidates.some(item => item.evidence.platform_style) && <div className="screener-quick-filters" role="group" aria-label="平台类型过滤">
          {['compact-platform', 'secondary-retest', 'broad-base', 'all'].map(style => <button key={style}
            className={platformStyle === style ? 'active' : ''} aria-pressed={platformStyle === style}
            onClick={() => setPlatformStyle(style)}>{style === 'all' ? '全部形态' : accumulationStyleLabel(style)}
            <small>{candidates.filter(item => style === 'all' || item.evidence.platform_style === style).length}</small>
          </button>)}
        </div>}
        <div className="screener-quick-filters" role="group" aria-label="辨识度标签过滤">
          <button className={!historicalOnly ? 'active' : ''} aria-pressed={!historicalOnly} onClick={() => setHistoricalOnly(false)}>不限标签 <small>{stateCandidates.length}</small></button>
          <button className={historicalOnly ? 'active' : ''} aria-pressed={historicalOnly} disabled={!recognition?.available} onClick={() => setHistoricalOnly(true)}>历史辨识度 <small>{recognition?.available ? historicalCandidates.length : '—'}</small></button>
        </div>
        {candidates.length > 0 && <div className="screener-tag-source">{recognition?.available ? `辨识度来源 ${recognition.source_date}` : '截至选股日期暂无辨识度复盘数据'} · 显示 {filteredCandidates.length}/{candidates.length}</div>}
        {candidates.length > 0 && <>
          <div className="screener-board-search">
            <label>当前板块<input type="search" aria-label="搜索板块" placeholder="搜索板块" value={boardQuery} onChange={event => setBoardQuery(event.target.value)}/></label>
            {boardFilter.length > 0 && <button className="icon-button" title="清除板块筛选" aria-label="清除板块筛选" onClick={() => setBoardFilter([])}><X size={13}/></button>}
          </div>
          <div className="screener-quick-filters screener-board-filters" role="group" aria-label="板块筛选（多选取并集）" tabIndex={0}>
            <button className={boardFilter.length === 0 ? 'active' : ''} aria-pressed={boardFilter.length === 0} onClick={() => setBoardFilter([])}>不限板块 <small>{taggedCandidates.length}</small></button>
            {boardStatus === 'loading' && <span role="status">加载中</span>}
            {boardStatus === 'error' && <button onClick={() => setBoardRetry(value => value + 1)}><RefreshCw size={12}/>板块加载失败，重试</button>}
            {boardStatus === 'ready' && visibleBoards.map(board => <button key={board.board_symbol}
              className={boardFilter.includes(board.board_symbol) ? 'active' : ''}
              aria-pressed={boardFilter.includes(board.board_symbol)}
              aria-label={`${board.name}${duplicateBoardNames.has(board.name) ? ` ${board.board_symbol}` : ''} ${board.count}`}
              title={`${board.name} · ${board.source_system} · ${board.board_symbol}`}
              onClick={() => toggle(board.board_symbol, boardFilter, setBoardFilter)}>
              <i className={`board-filter-dot ${board.classification}`}/>{board.name}
              {duplicateBoardNames.has(board.name) && <small>{board.board_symbol}</small>}
              <small>{board.count}</small>
            </button>)}
            {boardStatus === 'ready' && !visibleBoards.length && <span role="status">{boardOptions.length ? '无匹配板块' : '暂无板块数据'}</span>}
          </div>
        </>}
        <div className="screener-result-head"><span>#</span><span>标的</span><span>周期/状态</span><span>得分</span></div>
        <div className="screener-scroll">{selectedRun?.status === 'failed' && <div className="screener-empty compact error">{selectedRun.error ?? '选股任务失败'}</div>}{selectedRun?.status === 'succeeded' && candidates.length === 0 && <div className="screener-empty compact">本轮没有符合条件的标的</div>}{candidates.length > 0 && filteredCandidates.length === 0 && <div className="screener-empty compact">当前筛选组合没有符合条件的标的</div>}{filteredCandidates.map(item => <button key={item.symbol} className={selected?.symbol === item.symbol ? 'active' : ''} onClick={() => setSelected(item)} onContextMenu={event => {
          event.preventDefault()
          setSelected(item)
          setContextMenu({ kind: 'candidate', ...menuPosition(event.clientX, event.clientY), candidate: item, selectingTarget: false })
        }}>
          <span>{item.rank}</span><span><span className="instrument-name-line screener-tagged-name"><b>{item.name}</b><MarketBoardBadge instrument={item}/>
            {item.recognition?.tags.filter(tag => tag === 'recent' || tag === 'historical').map(tag => <i key={tag} className="recognition-name-tag"
              title={`辨识度来源 ${item.recognition?.source_date ?? '--'} · ${item.evidence.recognition_rank_bonus ? `排序加分 ${item.evidence.recognition_rank_bonus}` : '历史轮次不改变原排名'}`}>
              {tag === 'recent' ? '近期辨识度' : '历史辨识度'}</i>)}
          </span><small>{item.symbol}</small></span><span><b>{candidatePeriodLabel(item)}</b><small>{candidateStageLabel(item)}</small></span><span>{item.score.toFixed(1)}</span>
        </button>)}</div>
      </section>
      <section className="screener-chart-pane">
        <header>{selected ? <><span className="instrument-name-line"><span>{selected.name}</span><MarketBoardBadge instrument={selected}/></span><small>{selected.line_code} · {candidatePeriodLabel(selected)} · {candidateStageLabel(selected)}</small></> : <span>个股 K 线</span>}</header>
        <div className="screener-chart-body">{selected && analysis
          ? <ScreenerChart key={selected.analysis_run_id} candidate={selected} analysis={analysis} asOfDate={selectedRun?.as_of_date} theme={theme}/>
          : <div className="screener-empty">选择一条结果查看 K 线与形态分析</div>}</div>
        {selected && selected.state === 'accumulating' && <footer className="screener-evidence">
          <span><small>形态阶段</small>{accumulationStageLabel(selected.evidence.stage)}</span>
          {selected.evidence.compact_platform && <>
            <span><small>平台分类 / 紧凑度</small>{accumulationStyleLabel(selected.evidence.platform_style)} / {selected.evidence.compact_platform.score.toFixed(1)}</span>
            <span><small>近10日区间</small>{selected.evidence.compact_platform.last10?.range_percent.toFixed(2) ?? '--'}%</span>
            <span><small>小实体占比</small>{((selected.evidence.compact_platform.last10?.small_body_fraction ?? 0) * 100).toFixed(0)}%</span>
            <span><small>辨识度排序加分</small>+{selected.evidence.recognition_rank_bonus ?? 0}</span>
          </>}
          <span><small>结构类型</small>{accumulationPatternLabel(selected.evidence.pattern_type)}{selected.evidence.gentle_retest ? ' · 温和回踩' : ''}</span>
          {selected.evidence.pattern_type === 'secondary-base' && <span><small>回踩 / 反弹均量</small>{selected.evidence.pullback_volume_ratio?.toFixed(2) ?? '--'}x</span>}
          <span><small>前期阴跌</small>{signed(selected.evidence.decline_return_percent ?? 0)}%</span>
          <span><small>均线发散</small>{selected.evidence.ma_divergence_percent?.toFixed(2)}%</span>
          <span><small>底部抬升</small>{signed(selected.evidence.bottom_lift_percent ?? 0)}%</span>
          <span><small>平台振幅</small>{selected.evidence.platform_range_percent?.toFixed(2)}%</span>
          <span><small>平台涨跌</small>{signed(selected.evidence.platform_return_percent ?? 0)}%</span>
          <span><small>小实体 K 线</small>{selected.evidence.small_body_sessions ?? 0}/{selected.evidence.platform_sessions ?? 10}</span>
          <span><small>下跌减速</small>{selected.evidence.decline_slowing === undefined ? '--' : selected.evidence.decline_slowing ? '已确认' : '待确认'}</span>
          <span><small>{selected.evidence.demand_regime === 'dry-up-retest' ? '缩量承接' : '温和放量'}</small>{selected.evidence.platform_volume_ratio?.toFixed(2)}x</span>
          <span><small>红绿均量比</small>{selected.evidence.average_up_down_volume_ratio?.toFixed(2) ?? '--'}</span>
          <span><small>去最大量日</small>{selected.evidence.robust_up_down_volume_ratio?.toFixed(2) ?? '--'}</span>
          <span><small>期间涨停</small>{selected.evidence.limit_up_count ?? '--'} 次（允许）</span>
          <span><small>结构失效位</small>{selected.evidence.invalidation_price?.toFixed(2) ?? '--'}</span>
          {selected.evidence.score_components && <span><small>下跌 / 抬升 / 平台 / 承接</small>{Object.values(selected.evidence.score_components).map(value => value.toFixed(1)).join(' / ')}</span>}
          {selected.evidence.missing_evidence?.includes('turnover-unavailable') && <span><small>辅助证据</small>换手率暂缺</span>}
        </footer>}
        {selected && pullbackStates.includes(selected.state) && <footer className="screener-evidence">
          <span><small>形态阶段</small>{candidateStageLabel(selected)}</span>
          {selected.evidence.flag_window && <>
            <span><small>滚动观察窗口</small>{selected.evidence.observation_window_sessions} 个交易日</span>
            <span><small>整理区间</small>{selected.evidence.flag_window.start_date} ~ {selected.evidence.flag_window.end_date}</span>
            <span><small>整理阶段</small>{selected.evidence.launch_type === 'low-base-platform' ? '低位平台' : selected.evidence.flag_window.phase === 'early' ? '早期旗形观察' : '旗形整理观察'} · {selected.evidence.flag_window.sessions} 日</span>
          </>}
          <span><small>启动日期</small>{selected.evidence.launch_date ?? '--'}</span>
          <span><small>确认日期</small>{selected.evidence.confirmation_date ?? '尚未确认'}</span>
          <span><small>启动涨幅</small>{selected.evidence.impulse_gain_percent?.toFixed(2) ?? '--'}%</span>
          <span><small>回踩幅度</small>{selected.evidence.pullback_depth_percent?.toFixed(2) ?? '--'}%</span>
          <span><small>回踩 / 启动均量</small>{selected.evidence.pullback_volume_ratio?.toFixed(2) ?? '--'}x</span>
          {selected.evidence.launch_type === 'low-base-platform' && <>
            {selected.evidence.platform_shape && <>
              <span><small>独立平台</small>{selected.evidence.platform_shape.start_date} ~ {selected.evidence.platform_shape.end_date} · {selected.evidence.platform_shape.sessions} 日</span>
              <span><small>平台价格漂移</small>{selected.evidence.platform_shape.close_drift_percent.toFixed(2)}%</span>
              <span><small>小实体占比</small>{(selected.evidence.platform_shape.small_body_fraction * 100).toFixed(0)}%</span>
            </>}
            <span><small>启动前距60日低点</small>{selected.evidence.origin_above_context_low_percent?.toFixed(2) ?? '--'}%</span>
            <span><small>平台振幅</small>{selected.evidence.platform_range_percent?.toFixed(2) ?? '--'}%</span>
            <span><small>回踩 / 平台均量</small>{selected.evidence.pullback_platform_volume_ratio?.toFixed(2) ?? '--'}x</span>
          </>}
          {selected.evidence.launch_type === 'strong-momentum' && <>
            <span><small>启动类型</small>连续强阳启动</span>
            <span><small>量能类型</small>{firstPullbackVolumeLabel(selected.evidence.volume_regime)}</span>
            <span><small>回踩均量 / 启动至峰值最大日量</small>{selected.evidence.pullback_turnover_ratio?.toFixed(2) ?? '--'}x</span>
          </>}
          <span><small>失效位（收盘口径）</small>{selected.evidence.invalidation_price?.toFixed(2) ?? '--'}</span>
          {selected.evidence.launch_type !== 'low-base-platform' && <>
            <span><small>前高参考</small>{selected.evidence.first_target_price?.toFixed(2) ?? '--'}</span>
            <span><small>参考盈亏比</small>{selected.evidence.first_risk_reward?.toFixed(2) ?? '--'}</span>
          </>}
          <span><small>证据边界</small>仅日线量价；板块共振、分时承接未验证</span>
        </footer>}
        {selected && selected.state !== 'accumulating' && !pullbackStates.includes(selected.state) && <footer className="screener-evidence">
          <span><small>边界</small>{selected.evidence.projected_price?.toFixed(2)}</span>
          <span><small>收盘</small>{latestClose(selected).toFixed(2)}</span>
          <span><small>距斜边</small>{signed(selected.evidence.distance_percent ?? 0)}%</span>
          <span><small>失效位</small>{selected.evidence.invalidation_price?.toFixed(2) ?? '—'}</span>
          <span><small>目标位</small>{selected.evidence.first_target_price?.toFixed(2) ?? '—'}</span>
          <span><small>盈亏比</small>{selected.evidence.first_risk_reward?.toFixed(2) ?? '—'}</span>
          <span><small>小周期 14</small>{signed(selected.evidence.small_14?.return_percent ?? 0)}%</span>
          <span><small>中周期 28</small>{signed(selected.evidence.medium_28?.return_percent ?? 0)}%</span>
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
  const [selectedScenarioTarget, setSelectedScenarioTarget] = useState<string>()
  const [scenarioVisible, setScenarioVisible] = useAnalysisOverlayVisibility(
    JSON.stringify([candidate.symbol, analysis.run_id]),
  )
  const overlayControls = useAnalysisLayers(JSON.stringify([candidate.symbol, analysis.run_id]))
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
        externalLayerControls
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
      trendIsolation={trendState.isolate}
      asOfDate={asOfDate}
      trendAnalysisOverride={trendState.enabled ? analysis : null}
      highlightedAnalysisItemId={highlightedItemId}
      selectedScenarioTarget={selectedScenarioTarget}
      riskRewardVisible={scenarioVisible}
      {...overlayControls.layers}
    />
    {explanationOpen && <TrendExplanationPanel
      run={analysis}
      onHighlightItemChange={itemId => setHighlightedItemId(itemId ?? candidate.line_item_id)}
      selectedScenarioTarget={selectedScenarioTarget}
      scenarioVisible={scenarioVisible}
      overlayControls={overlayControls}
      onScenarioTargetChange={setSelectedScenarioTarget}
      onScenarioVisibleChange={setScenarioVisible}
      onClose={() => {
        setExplanationOpen(false)
        setHighlightedItemId(candidate.line_item_id)
      }}
    />}
  </div>
})

function formatRunDate(value: string) { return value.replaceAll('-', '').slice(4) + ' 选股结果' }
function candidatePeriodLabel(value: ScreenerCandidate) {
  if (pullbackStates.includes(value.state)) return `回踩${value.evidence.pullback_sessions ?? '-'}日`
  return value.evidence.period ? periodLabels[value.evidence.period]
    : `${value.evidence.platform_sessions ?? 20}日`
}
function candidateStageLabel(value: ScreenerCandidate) {
  if (value.evidence.shape_maturity) return lowBaseMaturityLabel(value.evidence.shape_maturity)
  if (pullbackStates.includes(value.state)) return firstPullbackStageLabel(value.evidence.stage)
  return value.evidence.platform_style ? accumulationStyleLabel(value.evidence.platform_style)
    : value.evidence.stage ? accumulationStageLabel(value.evidence.stage) : stateLabels[value.state]
}
function strategyLabel(value: string) {
  return strategyLabels[value as ScreenerStrategyId] ?? value
}
function signed(value: number) { return `${value > 0 ? '+' : ''}${value.toFixed(2)}` }
function latestClose(value: ScreenerCandidate) {
  if (value.evidence.latest_close != null) return value.evidence.latest_close
  return (value.evidence.projected_price ?? 0) * (1 + (value.evidence.distance_percent ?? 0) / 100)
}

function menuPosition(x: number, y: number) {
  return {
    x: Math.max(8, Math.min(x, window.innerWidth - 230)),
    y: Math.max(8, Math.min(y, window.innerHeight - 260)),
  }
}
