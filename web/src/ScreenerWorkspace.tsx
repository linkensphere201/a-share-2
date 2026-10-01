import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ChevronLeft, ChevronRight, Filter, ListPlus, Play, ListStart, RefreshCw, Trash2, X } from 'lucide-react'
import { ChartCanvas } from './ChartCanvas'
import { MarketBoardBadge } from './MarketBoardBadge'
import { fetchInstrumentBoardMemberships, type InstrumentBoardMembership } from './boardTags'
import { TradingSystemControls } from './TradingSystemControls'
import { TrendExplanationPanel } from './TrendExplanationPanel'
import { accumulationStyleLabel, lowBaseMaturityLabel } from './trendExplanation'
import { candidatePeriodLabel, candidateStageLabel, pullbackStates, periodLabels, stateLabels } from './screenerPresentation'
import { ScreenerEvidence } from './ScreenerEvidence'
import { useResultQuery } from './useResultQuery'
import { selectedRunKey, useScreenerHistory } from './useScreenerHistory'
import { useAnalysisOverlayVisibility, useAnalysisLayers } from './AnalysisOverlayToggle'
import { logError, logInfo } from './eventLogger'
import {
  deleteScreenerRun, listScreenerCandidates, listScreenerStrategies,
  startScreenerRun, startScreenerBatch, loadScreenerCandidate,
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

const strategyLabels: Record<ScreenerStrategyId, string> = {
  'platform-box-breakout': '平台箱体突破',
  'low-accumulation-platform': '低位吸筹平台',
  'long-consolidation-platform': '长期横盘平台',
  'major-descending-breakout': '大斜边突破',
  'volume-accumulation-20d': '20日堆量蓄势',
  'strong-first-pullback': '强势股首次回踩',
  'low-base-platform-pullback': '低位平台回踩',
  'bull-flag-consolidation': '牛旗盘整',
  'deep-drawdown-consolidation': '深跌缩量整理',
}
const trendStates: ScreenerState[] = ['critical-breakout', 'breakout-retest', 'broken-out']
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
  const [error, setError] = useState('')
  const { runs, selectedRun, setSelectedRun, loading: historyLoading, hasMore,
    loadMore, add: addRuns, remove: removeHistoryRun } = useScreenerHistory(value => setError(String(value)))
  const [candidates, setCandidates] = useState<ScreenerCandidate[]>([])
  const [selectedSummary, setSelected] = useState<ScreenerCandidate>()
  const [detail, setDetail] = useState<{ key: string; value: ScreenerCandidate }>()
  const detailKey = selectedRun && selectedSummary ? `${selectedRun.run_id}:${selectedSummary.rank}` : undefined
  const selected = detailKey && detail?.key === detailKey ? detail.value : selectedSummary
  const [analysis, setAnalysis] = useState<TrendAnalysisRun | null>(null)
  const [strategyId, setStrategyId] = useState<ScreenerStrategyId>('major-descending-breakout')
  const [availableStrategies, setAvailableStrategies] = useState(strategyLabels)
  useEffect(() => {
    const controller = new AbortController()
    listScreenerStrategies(controller.signal).then(values => {
      if (!controller.signal.aborted && values?.length) {
        setAvailableStrategies(Object.fromEntries(values.map(value => [value.strategy_id, value.name])))
      }
    }).catch(() => { /* Retain the legacy catalog when connected to an older desktop. */ })
    return () => controller.abort()
  }, [])
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
  const [notice, setNotice] = useState('')
  const [contextMenu, setContextMenu] = useState<ScreenerContextMenu>()
  const [startingStrategy, setStartingStrategy] = useState<ScreenerStrategyId | null>(null)
  const [startingAll, setStartingAll] = useState(false)
  const startingRef = useRef(false)
  const strategyBusy = runs.some(run => run.status === 'running' && run.strategy_id === strategyId)
  const batchBusy = runs.some(run => run.status === 'running' && run.parameters.batch_id)

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
    ([...trendStates, ...pullbackStates, 'accumulating', 'shape-match'] as ScreenerState[]).map(state => [
      state,
      candidates.filter(item => item.state === state).length,
    ]),
  ) as Record<ScreenerState, number>, [candidates])

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

  useResultQuery(selectedSummary?.evidence_complete === false ? detailKey : undefined,
    signal => loadScreenerCandidate(selectedRun!.run_id, selectedSummary!.rank, signal),
    value => setDetail({ key: detailKey!, value }), value => setError(String(value)))
  useEffect(() => { setAnalysis(null) }, [selected?.analysis_run_id])
  useResultQuery(selected?.analysis_run_id,
    signal => loadExactTrendAnalysis(selected!.analysis_run_id, signal),
    setAnalysis, value => setError(String(value)))

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
    if (startingRef.current || strategyBusy) return
    startingRef.current = true
    setStartingStrategy(strategyId)
    setError('')
    try {
      const run = await startScreenerRun({ strategy_id: strategyId, periods, states, max_results: maxResults })
      addRuns([run])
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

  const startAll = async () => {
    if (startingRef.current || batchBusy) return
    startingRef.current = true
    setStartingAll(true)
    setError('')
    setNotice('')
    try {
      const batch = await startScreenerBatch(maxResults)
      addRuns(batch.items)
      const skipped = batch.skipped.map(item => `${strategyLabel(item.strategy_id)}（${
        item.reason === 'already-running' ? '已在运行' : item.reason === 'cutoff-unavailable' ? '日期不可用' : '创建失败'}）`)
      setNotice(`已提交 ${batch.items.length} 个策略 · ${batch.as_of_date}${skipped.length ? `；跳过：${skipped.join('、')}` : ''}`)
      logInfo('screener', '批量选股已提交', { batchId: batch.batch_id, count: batch.items.length })
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    } finally {
      startingRef.current = false
      setStartingAll(false)
    }
  }

  const removeRun = async (run: ScreenerRun) => {
    if (run.status === 'running') return
    if (!window.confirm(`删除“${formatRunDate(run.as_of_date)}”？删除后无法恢复。`)) return
    setError('')
    try {
      await deleteScreenerRun(run.run_id)
      removeHistoryRun(run.run_id)
      if (window.localStorage.getItem(selectedRunKey) === run.run_id) window.localStorage.removeItem(selectedRunKey)
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
      <span className="screener-title"><Filter size={17}/>选股器 <small>{availableStrategies[strategyId] ?? strategyId}</small></span>
      <label className="screener-limit">策略<select value={strategyId} onChange={event => {
        setStrategyId(event.target.value as ScreenerStrategyId)
        setResultStateFilter('all')
      }}>
        {Object.entries(availableStrategies).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select></label>
      {strategyId === 'major-descending-breakout' && <fieldset><legend>周期</legend>{(['6m', '1y'] as ScreenerPeriod[]).map(item =>
        <label key={item}><input type="checkbox" checked={periods.includes(item)} onChange={() => toggle(item, periods, setPeriods)}/>{periodLabels[item]}</label>)}</fieldset>}
      {strategyId === 'major-descending-breakout' && <fieldset><legend>状态</legend>{trendStates.map(item =>
        <label key={item}><input type="checkbox" checked={states.includes(item)} onChange={() => toggle(item, states, setStates)}/>{stateLabels[item]}</label>)}</fieldset>}
      <label className="screener-limit">上限<select value={maxResults} onChange={event => setMaxResults(Number(event.target.value))}>
        {[50, 100, 200, 500].map(value => <option key={value}>{value}</option>)}
      </select></label>
      <div className="screener-actions"><button className="primary-button" aria-busy={startingStrategy !== null} disabled={startingAll || startingStrategy !== null || (strategyId === 'major-descending-breakout' && (!periods.length || !states.length)) || strategyBusy} onClick={start}>
        {startingStrategy !== null || strategyBusy ? <RefreshCw size={14} className="spin"/> : <Play size={14}/>}开始选股
      </button>
      <button className="primary-button" title="按各策略默认条件运行全部策略，沿用当前结果上限；跳过已在运行的策略" aria-busy={startingAll}
        disabled={startingAll || startingStrategy !== null || batchBusy} onClick={startAll}>
        {startingAll ? <RefreshCw size={14} className="spin"/> : <ListStart size={14}/>}全部选股
      </button></div>
    </header>
    {error && <div className="screener-error">{error}</div>}
    {notice && <button className="screener-notice" onClick={() => setNotice('')}>{notice}</button>}
    <section className="screener-grid">
      <aside className="screener-runs">
        <header>每轮选股结果 <span>已加载 {runs.length} 轮</span><button className="icon-button" title="刷新选股历史" aria-label="刷新选股历史" disabled={historyLoading} onClick={() => void loadMore(true)}><RefreshCw size={14}/></button></header>
        <div className="screener-scroll">
          {(startingStrategy !== null || startingAll) && <div className="screener-starting" role="status">
            <RefreshCw size={14} className="spin"/><span>正在创建选股任务<small>{startingAll ? '全部策略' : strategyLabel(startingStrategy!)}</small></span>
          </div>}
          {runs.length === 0 && startingStrategy === null && !startingAll && <div className="screener-empty compact">暂无历史结果</div>}{runs.map(run => <button key={run.run_id} className={selectedRun?.run_id === run.run_id ? 'active' : ''} onClick={() => setSelectedRun(run)} onContextMenu={event => {
          event.preventDefault()
          setContextMenu({ kind: 'run', ...menuPosition(event.clientX, event.clientY), run })
        }}>
          <span>{formatRunDate(run.as_of_date)}</span><small>{strategyLabel(run.strategy_id)} · {run.status === 'running' ? run.execution_state === 'queued' ? `排队中 · 第${run.queue_position ?? 1}位` : `${run.scanned_count}/${run.universe_count}` : run.status === 'failed' ? '失败' : `${run.candidate_count} 个标的`}</small>
          <i className={run.status}/>
        </button>)}</div>
        {hasMore && <button disabled={historyLoading} onClick={() => void loadMore()}>{historyLoading ? '加载中…' : '加载更早结果'}</button>}
      </aside>
      <section className="screener-results">
        <header><span>选股结果</span><small>{selectedRun?.as_of_date ?? '尚未运行'} · {selectedRun?.status === 'running' ? selectedRun.execution_state === 'queued' ? '排队中' : `扫描 ${progress}%` : `${candidates.length} 个`}</small></header>
        {selectedRun?.status === 'running' && <div className="screener-progress"><i style={{ width: `${progress}%` }}/></div>}
        <div className="screener-quick-filters" role="group" aria-label="结果快速过滤">
          <button className={resultStateFilter === 'all' ? 'active' : ''} aria-label="快速过滤：全部" onClick={() => setResultStateFilter('all')}>全部 <small>{candidates.length}</small></button>
          {(selectedRun?.strategy_id === 'platform-box-breakout' ? ['broken-out', 'breakout-retest'] as ScreenerState[] : ['low-accumulation-platform', 'long-consolidation-platform', 'deep-drawdown-consolidation'].includes(selectedRun?.strategy_id ?? '') ? ['shape-match'] as ScreenerState[] : selectedRun?.strategy_id === 'bull-flag-consolidation' ? ['pullback-observation'] as ScreenerState[] : selectedRun?.strategy_id === 'volume-accumulation-20d' ? ['accumulating'] as ScreenerState[] : ['strong-first-pullback', 'low-base-platform-pullback'].includes(selectedRun?.strategy_id ?? '') ? pullbackStates : trendStates).map(state => <button
            key={state}
            className={resultStateFilter === state ? 'active' : ''}
            aria-label={`快速过滤：${selectedRun?.strategy_id === 'long-consolidation-platform' ? '平台收紧' : selectedRun?.strategy_id === 'bull-flag-consolidation' ? '旗面盘整中' : stateLabels[state]}`}
            onClick={() => setResultStateFilter(state)}
          >{selectedRun?.strategy_id === 'long-consolidation-platform' ? '平台收紧' : selectedRun?.strategy_id === 'bull-flag-consolidation' ? '旗面盘整中' : stateLabels[state]} <small>{resultStateCounts[state]}</small></button>)}
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
        <div className="screener-result-head"><span>#</span><span>标的</span><span>周期/状态</span><span>{selectedRun?.strategy_id === 'deep-drawdown-consolidation' ? '相似度' : '得分'}</span></div>
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
        <ScreenerEvidence selected={selected}/>
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
function strategyLabel(value: string) {
  return strategyLabels[value as ScreenerStrategyId] ?? value
}
function menuPosition(x: number, y: number) {
  return {
    x: Math.max(8, Math.min(x, window.innerWidth - 230)),
    y: Math.max(8, Math.min(y, window.innerHeight - 260)),
  }
}
