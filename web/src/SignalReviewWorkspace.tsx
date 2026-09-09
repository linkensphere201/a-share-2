import {
  useEffect, useMemo, useRef, useState, type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { ArrowLeft, Eye, ListFilter, MessageSquare, Pin, PinOff, Play, Radar, RefreshCw, Search } from 'lucide-react'
import { ChartCanvas } from './ChartCanvas'
import { MarketBoardBadge } from './MarketBoardBadge'
import type { ThemeDefinition } from './themeStore'
import {
  listBoardObservations, listSignalAttention, listSignalDefinitions, listSignalItems,
  listSignalRuns, listSignalScores, loadSignalRun, setSignalAttention, startSignalRun,
  type BoardDailyObservation, type SignalAttention, type SignalChangeType,
  type SignalDefinition, type SignalEvidence, type SignalItem, type SignalProfile,
  type SignalRun, type SignalScoreResult,
} from './signalReviewClient'
import { SignalChatPanel } from './SignalChatPanel'
import { loadExactTrendAnalysis, type TrendAnalysisRun } from './trendAnalysisClient'
import { TradeScenarioPanel } from './TradeScenarioPanel'

type Props = { theme: ThemeDefinition; onClose: () => void }
type ProfileFilter = 'all' | SignalProfile
type ChangeFilter = 'all' | SignalChangeType

const phaseLabels: Record<string, string> = {
  queued: '等待执行', memberships: '读取板块成分',
  'stock-features': '计算个股特征', 'board-ranking': '计算板块排名',
  'board-observations': '保存全板块一级观察',
  'board-deep-analysis': '复核重点观察板块',
  completed: '已完成', failed: '失败',
}
const profileLabels: Record<SignalProfile, string> = {
  recent: '近期高权', historical: '历史高权', market: '大盘', attention: '重点观察',
}
const changeLabels: Record<SignalChangeType, string> = { added: '新增', retained: '保留', removed: '移除' }
const EVIDENCE_HEIGHT_KEY = 'stock-harness.signal-review.evidence-height.v1'
const COLUMN_WIDTHS_KEY = 'stock-harness.signal-review.column-widths.v1'
type SignalColumn = 'runs' | 'results' | 'chat'
type SignalColumnWidths = Record<SignalColumn, number>
const DEFAULT_COLUMN_WIDTHS: SignalColumnWidths = { runs: 225, results: 360, chat: 340 }
const COLUMN_LIMITS: Record<SignalColumn, [number, number]> = {
  runs: [180, 340], results: [280, 560], chat: [280, 620],
}

export function SignalReviewWorkspace({ theme, onClose }: Props) {
  const [definitions, setDefinitions] = useState<SignalDefinition[]>([])
  const [selectedDefinition, setSelectedDefinition] = useState<SignalDefinition>()
  const [runs, setRuns] = useState<SignalRun[]>([])
  const [selectedRun, setSelectedRun] = useState<SignalRun>()
  const [startingRun, setStartingRun] = useState(false)
  const [items, setItems] = useState<SignalItem[]>([])
  const [selectedItem, setSelectedItem] = useState<SignalItem>()
  const [profile, setProfile] = useState<ProfileFilter>('all')
  const [change, setChange] = useState<ChangeFilter>('all')
  const [error, setError] = useState('')
  const [chatOpen, setChatOpen] = useState(false)
  const [highlightedEvidenceId, setHighlightedEvidenceId] = useState<string>()
  const [scenarioHighlightedItemId, setScenarioHighlightedItemId] = useState<string>()
  const [selectedScenarioTarget, setSelectedScenarioTarget] = useState<string>()
  const [scenarioVisible, setScenarioVisible] = useState(true)
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string>()
  const [dailyView, setDailyView] = useState<'results' | 'opportunities' | 'observations'>('results')
  const [observationQuery, setObservationQuery] = useState('')
  const [observations, setObservations] = useState<BoardDailyObservation[]>([])
  const [observationTotal, setObservationTotal] = useState(0)
  const [selectedObservation, setSelectedObservation] = useState<BoardDailyObservation>()
  const [attention, setAttention] = useState<SignalAttention[]>([])
  const [scores, setScores] = useState<SignalScoreResult[]>([])
  const [selectedScoreSystem, setSelectedScoreSystem] = useState('trend-breakout')
  const [historySelection, setHistorySelection] = useState<{ symbol: string; entityKey?: string }>()
  const [exactAnalysis, setExactAnalysis] = useState<TrendAnalysisRun | null>(null)
  const [evidenceHeight, setEvidenceHeight] = useState(() => {
    const stored = Number(window.localStorage.getItem(EVIDENCE_HEIGHT_KEY))
    return Number.isFinite(stored) && stored >= 135 ? Math.min(stored, 520) : 210
  })
  const [columnWidths, setColumnWidths] = useState(readColumnWidths)
  const inspectorRef = useRef<HTMLElement>(null)

  useEffect(() => {
    window.localStorage.setItem(EVIDENCE_HEIGHT_KEY, String(Math.round(evidenceHeight)))
  }, [evidenceHeight])

  useEffect(() => {
    window.localStorage.setItem(COLUMN_WIDTHS_KEY, JSON.stringify(columnWidths))
  }, [columnWidths])

  useEffect(() => {
    const controller = new AbortController()
    listSignalDefinitions(controller.signal).then(value => {
      setDefinitions(value)
      setSelectedDefinition(current => current ?? value[0])
    }).catch(reason => { if (reason.name !== 'AbortError') setError(String(reason)) })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!selectedDefinition) return
    const controller = new AbortController()
    listSignalRuns(selectedDefinition.signal_id, controller.signal).then(value => {
      setRuns(value)
      setSelectedRun(current => value.find(item => item.run_id === current?.run_id) ?? value[0])
    }).catch(reason => { if (reason.name !== 'AbortError') setError(String(reason)) })
    return () => controller.abort()
  }, [selectedDefinition])

  useEffect(() => {
    setHighlightedEvidenceId(undefined)
    setSelectedEvidenceId(undefined)
    if (!selectedRun) { setItems([]); setSelectedItem(undefined); return }
    const controller = new AbortController()
    listSignalItems(selectedRun.run_id, controller.signal).then(value => {
      setItems(value)
      setSelectedItem(current => historySelection
        ? value.find(item => item.item_key === historySelection.entityKey || item.symbol === historySelection.symbol)
        : value.find(item => item.item_id === current?.item_id))
    }).catch(reason => { if (reason.name !== 'AbortError') setError(String(reason)) })
    return () => controller.abort()
  }, [selectedRun?.run_id, selectedRun?.status, historySelection])

  useEffect(() => {
    if (!selectedRun || selectedRun.status !== 'succeeded') {
      setScores([])
      return
    }
    const controller = new AbortController()
    listSignalScores(selectedRun.run_id, controller.signal).then(setScores)
      .catch(reason => { if (reason.name !== 'AbortError') setError(String(reason)) })
    return () => controller.abort()
  }, [selectedRun?.run_id, selectedRun?.status])

  useEffect(() => {
    if (selectedRun?.status !== 'running' || !selectedDefinition) return
    const timer = window.setInterval(async () => {
      try {
        const next = await loadSignalRun(selectedRun.run_id)
        setSelectedRun(next)
        setRuns(await listSignalRuns(selectedDefinition.signal_id))
      } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    }, 1200)
    return () => window.clearInterval(timer)
  }, [selectedDefinition, selectedRun?.run_id, selectedRun?.status])

  useEffect(() => {
    if (!selectedDefinition || selectedDefinition.cadence !== 'daily') {
      setAttention([])
      return
    }
    const controller = new AbortController()
    listSignalAttention(selectedDefinition.signal_id, controller.signal)
      .then(setAttention)
      .catch(reason => { if (reason.name !== 'AbortError') setError(String(reason)) })
    return () => controller.abort()
  }, [selectedDefinition, selectedRun?.status])

  useEffect(() => {
    if (dailyView === 'results' || !selectedRun || selectedRun.status !== 'succeeded') {
      setObservations([])
      setObservationTotal(0)
      setSelectedObservation(undefined)
      return
    }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      listBoardObservations(selectedRun.run_id, observationQuery, controller.signal)
        .then(value => {
          setObservations(value.items)
          setObservationTotal(value.total)
          setSelectedObservation(current => value.items.find(item =>
            item.symbol === (historySelection?.symbol ?? current?.symbol)))
        })
        .catch(reason => { if (reason.name !== 'AbortError') setError(String(reason)) })
    }, 180)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [dailyView, observationQuery, selectedRun?.run_id, selectedRun?.status, historySelection])

  const deepAnalysisRunId = selectedObservation?.deep_analysis_run_id
    ?? selectedItem?.payload.deep_analysis_run_id
  useEffect(() => {
    setExactAnalysis(null)
    if (!deepAnalysisRunId) return
    const controller = new AbortController()
    loadExactTrendAnalysis(deepAnalysisRunId, controller.signal)
      .then(setExactAnalysis)
      .catch(reason => {
        if (reason.name !== 'AbortError') setError(`深度分析图层读取失败：${String(reason)}`)
      })
    return () => controller.abort()
  }, [deepAnalysisRunId])

  const filtered = useMemo(() => items.filter(item =>
    (profile === 'all' || item.profile === profile)
    && (change === 'all' || item.change_type === change),
  ).sort((left, right) => {
    const profileOrder = (value: SignalProfile) => value === 'market' ? 0
      : value === 'attention' || value === 'recent' ? 1 : 2
    return profileOrder(left.profile) - profileOrder(right.profile)
      || (right.payload.score_result?.total_score ?? right.score * 100)
        - (left.payload.score_result?.total_score ?? left.score * 100)
      || left.symbol.localeCompare(right.symbol)
  }), [items, profile, change])
  const boardScoreSystems = useMemo(() => [...new Set(
    scores.filter(item => item.entity_scope === 'board').map(item => item.system_id),
  )], [scores])
  useEffect(() => {
    if (boardScoreSystems.length && !boardScoreSystems.includes(selectedScoreSystem)) {
      setSelectedScoreSystem(boardScoreSystems[0])
    }
  }, [boardScoreSystems, selectedScoreSystem])
  const scoreBySymbol = useMemo(() => new Map(
    scores.filter(item => item.system_id === selectedScoreSystem)
      .map(item => [item.symbol, item]),
  ), [scores, selectedScoreSystem])
  const hardEventSummary = useMemo(() => {
    const active = scores
      .filter(score => score.entity_scope === 'board')
      .flatMap(score => score.hard_events.filter(event => event.state !== 'resolved'))
    const counts = new Map<string, number>()
    active.forEach(event => counts.set(event.event_type, (counts.get(event.event_type) ?? 0) + 1))
    return { total: active.length, counts: [...counts.entries()].sort((a, b) => b[1] - a[1]) }
  }, [scores])
  const displayedObservations = useMemo(() => [...observations]
    .filter(item => dailyView !== 'opportunities' || scoreBySymbol.get(item.symbol)?.eligible)
    .sort((left, right) => {
      const leftScore = scoreBySymbol.get(left.symbol)
      const rightScore = scoreBySymbol.get(right.symbol)
      return Number(Boolean(rightScore?.eligible)) - Number(Boolean(leftScore?.eligible))
        || (rightScore?.total_score ?? -1) - (leftScore?.total_score ?? -1)
        || left.symbol.localeCompare(right.symbol)
    }), [dailyView, observations, scoreBySymbol])
  useEffect(() => {
    if (filtered.some(item => item.item_id === selectedItem?.item_id)) return
    setSelectedItem(undefined)
  }, [filtered, selectedItem?.item_id])
  const counts = useMemo(() => Object.fromEntries(
    (['recent', 'historical', 'market', 'attention'] as SignalProfile[])
      .map(value => [value, items.filter(item => item.active && item.profile === value).length]),
  ) as Record<SignalProfile, number>, [items])
  const daily = selectedDefinition?.cadence === 'daily'
  const inspected = selectedObservation ?? selectedItem
  const inspectedScore = selectedObservation
    ? scoreBySymbol.get(selectedObservation.symbol)
    : selectedItem?.payload.score_result
  const activeEvidenceId = highlightedEvidenceId ?? selectedEvidenceId
  const highlightedAnalysisItemId = scenarioHighlightedItemId ?? selectedItem?.evidence.find(
    evidence => evidence.evidence_id === activeEvidenceId,
  )?.source_item_id ?? undefined
  const criticalAlert = buildCriticalAlert(inspected)
  const pinned = selectedObservation
    ? attention.find(item => item.symbol === selectedObservation.symbol)?.manual_pinned ?? false
    : false
  const progress = selectedRun?.work_total
    ? Math.round(selectedRun.work_done / selectedRun.work_total * 100) : 0
  const activeRun = runs.find(item => item.status === 'running')
  const activeProgress = activeRun?.work_total
    ? Math.round(activeRun.work_done / activeRun.work_total * 100) : 0
  const runBusy = startingRun || Boolean(activeRun)
  const previewReference = (itemId?: string, evidenceId?: string) => {
    setHighlightedEvidenceId(itemId === selectedItem?.item_id ? evidenceId : undefined)
  }
  const activateReference = (itemId: string, evidenceId: string) => {
    const item = items.find(value => value.item_id === itemId)
    if (!item) return
    setSelectedItem(item)
    setSelectedEvidenceId(evidenceId)
    setHighlightedEvidenceId(undefined)
  }

  const run = async () => {
    if (!selectedDefinition || runBusy) return
    setStartingRun(true)
    try {
      setError('')
      const next = await startSignalRun(selectedDefinition.signal_id)
      setRuns(current => [next, ...current])
      setSelectedRun(next)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setStartingRun(false)
    }
  }
  const openHistoricalScore = async (runId: string, symbol: string, entityKey?: string) => {
    try {
      const historicalRun = await loadSignalRun(runId)
      setHistorySelection({ symbol, entityKey })
      setSelectedRun(historicalRun)
      setRuns(current => current.some(item => item.run_id === runId) ? current : [...current, historicalRun])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const togglePinned = async () => {
    if (!selectedDefinition || !selectedObservation) return
    try {
      await setSignalAttention(selectedDefinition.signal_id, selectedObservation.symbol, !pinned)
      setAttention(await listSignalAttention(selectedDefinition.signal_id))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }

  const startEvidenceResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    const startY = event.clientY
    const startHeight = evidenceHeight
    const panelHeight = inspectorRef.current?.clientHeight || 800
    const maximum = Math.max(135, panelHeight - 300)
    const move = (pointer: PointerEvent) => {
      setEvidenceHeight(Math.max(135, Math.min(maximum, startHeight + startY - pointer.clientY)))
    }
    const stop = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
      document.body.classList.remove('signal-evidence-resizing')
    }
    document.body.classList.add('signal-evidence-resizing')
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop, { once: true })
  }

  const startColumnResize = (
    column: SignalColumn, event: ReactPointerEvent<HTMLDivElement>,
  ) => {
    event.preventDefault()
    const startX = event.clientX
    const startWidth = columnWidths[column]
    const direction = column === 'chat' ? -1 : 1
    const [minimum, maximum] = COLUMN_LIMITS[column]
    const move = (pointer: PointerEvent) => {
      const width = startWidth + (pointer.clientX - startX) * direction
      setColumnWidths(value => ({
        ...value, [column]: Math.max(minimum, Math.min(maximum, width)),
      }))
    }
    const stop = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
      document.body.classList.remove('signal-column-resizing')
    }
    document.body.classList.add('signal-column-resizing')
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop, { once: true })
  }

  const gridStyle = {
    '--signal-runs-width': `${Math.round(columnWidths.runs)}px`,
    '--signal-results-width': `${Math.round(columnWidths.results)}px`,
    '--signal-chat-width': `${Math.round(columnWidths.chat)}px`,
  } as CSSProperties

  return <main className="signal-workspace">
    <header className="signal-toolbar">
      <button className="icon-button" title="返回工作台" aria-label="返回工作台" onClick={onClose}><ArrowLeft size={16}/></button>
      <span className="signal-title"><Radar size={17}/>信号复盘 <small>手工触发 · 结果可追溯</small></span>
      <select aria-label="选择信号" disabled={runBusy} value={selectedDefinition?.signal_id ?? ''} onChange={event => {
        const next = definitions.find(item => item.signal_id === event.target.value)
        setSelectedDefinition(next)
        setSelectedRun(undefined)
        setDailyView('results')
        setProfile('all')
        setSelectedObservation(undefined)
      }}>{definitions.map(item => <option key={item.signal_id} value={item.signal_id}>{item.name}</option>)}</select>
      {selectedDefinition && <span className="signal-description">{selectedDefinition.description}</span>}
      <button className="primary-button signal-run-action" disabled={!selectedDefinition || runBusy} onClick={() => void run()}>
        {runBusy ? <RefreshCw size={14} className="spin"/> : <Play size={14}/>}<span>{startingRun
          ? '正在创建本期任务'
          : activeRun ? `${phaseLabels[activeRun.phase] ?? activeRun.phase} ${activeProgress}%` : '运行本期信号'}</span>
      </button>
    </header>
    {error && <button className="signal-error" onClick={() => setError('')}>{error}</button>}
    <section className={`${inspected ? 'signal-grid inspector-open' : 'signal-grid'}${chatOpen ? ' chat-open' : ''}`} style={gridStyle}>
      <aside className="signal-runs">
        <header><span>历史轮次</span><small>{runs.length}</small></header>
        <div className="signal-scroll">{startingRun && <div className="signal-run-pending" role="status" aria-live="polite">
          <RefreshCw size={14} className="spin"/><span>正在创建本期任务<small>准备运行记录与数据截止日</small></span>
        </div>}{runs.length === 0 && !startingRun && <div className="signal-empty compact">尚未运行</div>}{runs.map(item => <button key={item.run_id} className={selectedRun?.run_id === item.run_id ? 'active' : ''} onClick={() => setSelectedRun(item)}>
          <span>{item.effective_date} <i>R{item.revision}</i></span>
          <small>{item.status === 'running' ? `${phaseLabels[item.phase] ?? item.phase} ${progress}%` : item.status === 'failed' ? '执行失败' : `${item.item_count} 项`}</small>
          <em className={item.status}/>
        </button>)}</div>
      </aside>
      <div className="signal-column-resizer" role="separator" aria-orientation="vertical" aria-label="调整历史轮次栏宽度" title="左右拖动调整历史轮次栏宽度" onPointerDown={event => startColumnResize('runs', event)}/>
      <section className="signal-results">
        <header><span>{dailyView === 'observations' ? '全部板块观察' : dailyView === 'opportunities' ? '板块机会评分' : '复盘结果'}</span><small>{dailyView !== 'results' ? `当前 ${displayedObservations.length} / 全量 ${observationTotal}` : selectedRun ? `+${selectedRun.added_count} =${selectedRun.retained_count} -${selectedRun.removed_count}` : '请选择轮次'}</small></header>
        {selectedRun?.status === 'running' && <div className="signal-progress"><i style={{ width: `${progress}%` }}/></div>}
        {daily && <div className="signal-view-switch">
          <button className={dailyView === 'results' ? 'active' : ''} onClick={() => { setDailyView('results'); setSelectedObservation(undefined) }}><ListFilter size={12}/>今日关注</button>
          <button className={dailyView === 'opportunities' ? 'active' : ''} onClick={() => { setDailyView('opportunities'); setSelectedItem(undefined); setChatOpen(false) }}><Radar size={12}/>机会评分</button>
          <button className={dailyView === 'observations' ? 'active' : ''} onClick={() => { setDailyView('observations'); setSelectedItem(undefined); setChatOpen(false) }}><Eye size={12}/>全部观察</button>
        </div>}
        {daily && boardScoreSystems.length > 0 && <label className="signal-score-system-select">评分体系<select aria-label="评分体系" value={selectedScoreSystem} onChange={event => setSelectedScoreSystem(event.target.value)}>{boardScoreSystems.map(system => <option key={system} value={system}>{system === 'trend-breakout' ? '趋势突破' : system}</option>)}</select></label>}
        {daily && hardEventSummary.total > 0 && <div className="signal-hard-event-strip" role="status">
          <span>硬异动 {hardEventSummary.total}</span>
          {hardEventSummary.counts.slice(0, 4).map(([eventType, count]) => <small key={eventType}>{hardEventLabel(eventType)} {count}</small>)}
        </div>}
        {dailyView !== 'results' ? <>
          <label className="signal-observation-search"><Search size={12}/><input aria-label="搜索板块观察" value={observationQuery} onChange={event => setObservationQuery(event.target.value)} placeholder="板块名称或代码"/></label>
          <div className="signal-result-head observation"><span>评分</span><span>板块</span><span>状态</span><span>关注</span></div>
          <div className="signal-scroll">{displayedObservations.map(item => {
            const entry = attention.find(value => value.symbol === item.symbol)
            const score = scoreBySymbol.get(item.symbol)
            return <button key={item.symbol} className={selectedObservation?.symbol === item.symbol ? 'active' : ''} onClick={() => { setSelectedObservation(item); setSelectedItem(undefined) }}>
              <ScoreBadge score={score}/>
              <span><span className="instrument-name-line"><b>{item.name}</b></span><small>{item.symbol}</small></span>
              <SignalStateCell
                states={item.state_codes}
                fallback={item.coverage_state === 'complete' ? '未触发异动' : '数据不足'}
              />
              <span className={`attention-state ${entry?.status ?? 'inactive'}`}>{entry?.manual_pinned ? '固定' : item.attention_eligible ? '自动' : '-'}</span>
            </button>
          })}</div>
        </> : <>
          <div className="signal-filters">
            {(['all', ...(daily ? ['market', 'attention'] : ['recent', 'historical'])] as ProfileFilter[]).map(value => <button key={value} className={profile === value ? 'active' : ''} onClick={() => setProfile(value)}>{value === 'all' ? '全部' : profileLabels[value]} {value === 'all' ? items.length : counts[value]}</button>)}
          </div>
          <div className="signal-filters secondary">
            {(['all', 'added', 'retained', 'removed'] as ChangeFilter[]).map(value => <button key={value} className={change === value ? 'active' : ''} onClick={() => setChange(value)}>{value === 'all' ? '全部变化' : changeLabels[value]}</button>)}
          </div>
          <div className="signal-result-head"><span>评分</span><span>标的</span><span>{daily ? '状态' : '板块'}</span><span>变化</span></div>
          <div className="signal-scroll">{selectedRun?.status === 'failed' && <div className="signal-empty compact error">{selectedRun.error}</div>}{filtered.map(item => <button key={item.item_id} className={`${selectedItem?.item_id === item.item_id ? 'active ' : ''}${item.active ? '' : 'inactive'}`} onClick={() => { setSelectedItem(item); setSelectedObservation(undefined); setSelectedEvidenceId(undefined); setHighlightedEvidenceId(undefined) }}>
            <ScoreBadge score={item.payload.score_result} fallback={item.score * 100} rank={item.rank}/><span><span className="instrument-name-line"><b>{item.name}</b><MarketBoardBadge instrument={item}/></span><small>{item.symbol}</small></span>{daily
              ? <SignalStateCell states={item.payload.state_codes} fallback={profileLabels[item.profile]}/>
              : <span>{item.payload.board_count ?? 0}<small>{profileLabels[item.profile]}</small></span>}
            <span className={`change ${item.change_type}`}>{changeLabels[item.change_type]}</span>
          </button>)}</div>
        </>}
      </section>
      {inspected && <div className="signal-column-resizer" role="separator" aria-orientation="vertical" aria-label="调整复盘结果栏宽度" title="左右拖动调整复盘结果栏宽度" onPointerDown={event => startColumnResize('results', event)}/>}
      {inspected && <section ref={inspectorRef} className={`signal-inspector${criticalAlert ? ' has-critical' : ''}`} style={{ gridTemplateRows: criticalAlert ? `42px auto minmax(220px, 1fr) ${evidenceHeight}px` : `42px minmax(220px, 1fr) ${evidenceHeight}px` }}>
        <header><span className="instrument-name-line"><span>{inspected.name}</span>{selectedItem && <MarketBoardBadge instrument={selectedItem}/>}</span><small>{selectedItem ? `${profileLabels[selectedItem.profile]} · 得分 ${(selectedItem.score * 100).toFixed(1)} · 置信 ${(selectedItem.confidence * 100).toFixed(1)}` : `${selectedObservation?.effective_date} · 一级固定分析`}</small>{selectedObservation
          ? <button className="icon-button" title={pinned ? '取消手工固定' : '加入手工观察池'} aria-label={pinned ? '取消手工固定' : '加入手工观察池'} onClick={() => void togglePinned()}>{pinned ? <PinOff size={13}/> : <Pin size={13}/>}</button>
          : <button className="icon-button" title="Codex 信号讨论" aria-label="Codex 信号讨论" onClick={() => setChatOpen(value => !value)}><MessageSquare size={13}/></button>}</header>
        {criticalAlert && <div className={`signal-critical-alert ${criticalAlert.tone}`}>
          <span>{criticalAlert.title}<small>{criticalAlert.facts}</small></span>
          <span>{criticalAlert.reason}<small>{criticalAlert.condition}</small></span>
        </div>}
        <div className="signal-chart">{inspected
          ? <ChartCanvas key={`${selectedRun?.run_id}:${inspected.symbol}`} symbol={inspected.symbol} instrumentName={inspected.name} instrumentKind={selectedItem?.kind ?? 'sector'} focused theme={theme} range="1Y" priceMode="normal" volumeVisible indicator="none" settlementVisible={false} openInterestVisible={false} asOfDate={selectedRun?.effective_date} trendAnalysisEnabled={Boolean(exactAnalysis)} trendAnalysisOverride={exactAnalysis} highlightedAnalysisItemId={highlightedAnalysisItemId} selectedScenarioTarget={selectedScenarioTarget} riskRewardVisible={scenarioVisible}/>
          : <div className="signal-empty">选择一项结果查看 K 线</div>}</div>
        <div className="signal-evidence"><div className="signal-evidence-resizer" role="separator" aria-orientation="horizontal" aria-label="调整固定算法结论高度" title="上下拖动调整结论区域高度" onPointerDown={startEvidenceResize}/><header><span>{selectedObservation ? '一级分析' : selectedItem?.payload.rendered_summary ? '固定算法结论' : '引用证据'}</span><small>{selectedObservation ? selectedObservation.state_codes.length : selectedItem?.evidence.length ?? 0}</small></header>
          <div className="signal-evidence-content">
            {inspectedScore && <ScoreSummary score={inspectedScore} onHistorySelect={openHistoricalScore}/>}
            {exactAnalysis && <TradeScenarioPanel run={exactAnalysis} selectedTargetLabel={selectedScenarioTarget} visible={scenarioVisible} onTargetChange={setSelectedScenarioTarget} onVisibleChange={setScenarioVisible} onHighlightItemChange={setScenarioHighlightedItemId}/>}
            {selectedObservation ? <pre className="signal-fixed-summary">{observationSummary(selectedObservation)}</pre> : <div className="signal-analysis-details">{selectedItem?.payload.rendered_summary && <pre className="signal-fixed-summary">{selectedItem.payload.rendered_summary}</pre>}<div className="signal-evidence-list">{selectedItem?.evidence.map(evidence => <button key={evidence.evidence_id} className={(highlightedEvidenceId ?? selectedEvidenceId) === evidence.evidence_id ? 'active' : ''} onClick={() => setSelectedEvidenceId(evidence.evidence_id)} title="点击查看该轮固定算法引用的原始或 M4 证据">
            <code>[{evidence.alias}]</code><span>{evidenceTitle(evidence)}<small>{evidenceDetail(evidence)}</small></span>
          </button>)}</div></div>}
          </div>
        </div>
      </section>}
      {chatOpen && <div className="signal-column-resizer" role="separator" aria-orientation="vertical" aria-label="调整Codex对话栏宽度" title="左右拖动调整Codex对话栏宽度" onPointerDown={event => startColumnResize('chat', event)}/>}
      {chatOpen && selectedRun && <SignalChatPanel key={selectedRun.run_id} run={selectedRun} items={items} selectedItem={selectedItem} onReferencePreview={previewReference} onReferenceActivate={activateReference} onClose={() => setChatOpen(false)}/>}
    </section>
  </main>
}

function ScoreBadge({ score, fallback, rank }: {
  score?: SignalScoreResult
  fallback?: number
  rank?: number
}) {
  const value = score?.total_score ?? fallback
  if (value === undefined) return <span className="signal-score-badge unavailable">-</span>
  const grade = score?.grade ?? (value >= 85 ? 'S' : value >= 75 ? 'A' : value >= 65 ? 'B' : value >= 50 ? 'C' : 'D')
  return <span className={`signal-score-badge grade-${grade.toLowerCase()}${score && !score.eligible ? ' ineligible' : ''}`} title={score ? `${score.verdict}：${score.summary} ${score.risk_summary}` : `评分 ${value.toFixed(0)}`}>
    <i>{grade}</i><strong>{value.toFixed(0)}</strong><small>#{score?.rank ?? rank ?? '-'}</small>
  </span>
}

function ScoreSummary({ score, onHistorySelect }: {
  score: SignalScoreResult
  onHistorySelect?: (runId: string, symbol: string, entityKey?: string) => void
}) {
  return <section className={`signal-score-summary grade-${score.grade.toLowerCase()}`} aria-label="固定算法综合评分">
    <ScoreBadge score={score}/>
    <div><span>{score.verdict}</span><p>{score.summary}</p><small>{score.risk_summary}</small></div>
    <ScoreSparkline score={score} onHistorySelect={onHistorySelect}/>
    <em>{score.change_summary}</em>
    {score.hard_events.length > 0 && <div className="signal-hard-events">{score.hard_events.slice(0, 3).map(event => <span key={event.event_type} className={`${event.direction} ${event.severity}`}>{hardEventLabel(event.event_type)}</span>)}</div>}
    <details className="signal-score-diagnostics"><summary>评分明细</summary>
      <div>{Object.entries(score.components).map(([name, value]) => <span key={name}>{name}<small>{value.toFixed(1)}</small></span>)}</div>
      {score.disqualifiers.length > 0 && <p>失格：{score.disqualifiers.join(' · ')}</p>}
      {score.penalties.length > 0 && <p>扣分：{score.penalties.map(item => `${item.code} ${item.points}`).join(' · ')}</p>}
    </details>
  </section>
}

function ScoreSparkline({ score, onHistorySelect }: {
  score: SignalScoreResult
  onHistorySelect?: (runId: string, symbol: string, entityKey?: string) => void
}) {
  const pointsWithHistory = [...score.history].reverse().filter(item => typeof item.total_score === 'number')
  const values = pointsWithHistory.map(item => item.total_score as number)
  values.push(score.total_score)
  if (values.length < 2) return <span className="signal-score-new">新基线</span>
  const width = 68
  const height = 24
  const coordinates = values.map((value, index) => {
    const x = values.length === 1 ? width : index / (values.length - 1) * width
    const y = height - 2 - Math.max(0, Math.min(100, value)) / 100 * (height - 4)
    return [x.toFixed(1), y.toFixed(1)] as const
  })
  return <svg className="signal-score-sparkline" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-label="最近兼容评分走势"><polyline points={coordinates.map(point => point.join(',')).join(' ')}/>{pointsWithHistory.map((item, index) => {
    const [cx, cy] = coordinates[index]
    return <circle key={`${item.run_id}:${index}`} cx={cx} cy={cy} r="2.5" tabIndex={item.run_id ? 0 : -1} role={item.run_id ? 'button' : undefined} aria-label={item.run_id ? `打开 ${item.effective_date} 冻结评分` : undefined} onClick={() => item.run_id && onHistorySelect?.(item.run_id, score.symbol, item.entity_key)} onKeyDown={event => { if ((event.key === 'Enter' || event.key === ' ') && item.run_id) onHistorySelect?.(item.run_id, score.symbol, item.entity_key) }}/>
  })}</svg>
}

function hardEventLabel(value: string): string {
  return ({
    'major-trend-breakout': '大级别突破',
    'trend-breakout': '趋势突破',
    'bullish-boundary-triggered': '多头边界触发',
    'oversold-rebound-triggered': '超跌反弹触发',
    'downside-exhaustion': '下跌衰竭',
    'sudden-volume-expansion': '突然放量',
    'trend-boundary-proximity': '趋势边界临界',
    'boundary-volume-contraction': '边界缩量',
    'relative-strength-regime': '相对强弱异动',
    'prior-state-strengthened': '状态增强',
    'prior-state-weakened': '状态减弱',
    'prior-state-changed': '状态切换',
    'structure-invalidated': '结构失效',
  } as Record<string, string>)[value] ?? value
}

function readColumnWidths(): SignalColumnWidths {
  try {
    const stored = JSON.parse(window.localStorage.getItem(COLUMN_WIDTHS_KEY) ?? '{}') as Partial<SignalColumnWidths>
    return Object.fromEntries((Object.keys(DEFAULT_COLUMN_WIDTHS) as SignalColumn[]).map(column => {
      const [minimum, maximum] = COLUMN_LIMITS[column]
      const value = Number(stored[column])
      return [column, Number.isFinite(value)
        ? Math.max(minimum, Math.min(maximum, value))
        : DEFAULT_COLUMN_WIDTHS[column]]
    })) as SignalColumnWidths
  } catch {
    return { ...DEFAULT_COLUMN_WIDTHS }
  }
}

function stateLabel(states?: string[]) {
  const labels = signalStateLabels()
  return states?.map(item => labels[item]).find(Boolean) ?? (states?.[0] || '数据不足')
}

function SignalStateCell({ states, fallback }: { states?: string[]; fallback: string }) {
  const details = stateDetailLabels(states)
  return <span className="signal-result-state">
    {stateLabel(states)}
    <small className="signal-result-details" title={details.join(' · ')}>
      {details.length > 0
        ? details.map(detail => <span key={detail}>{detail}</span>)
        : <span>{fallback}</span>}
    </small>
  </span>
}

export function stateDetailLabels(states?: string[]) {
  const labels = signalStateLabels()
  const primary = stateLabel(states)
  return Array.from(new Set((states ?? []).map(item => labels[item]).filter(
    (item): item is string => Boolean(item) && item !== primary && item !== '中性',
  )))
}

function signalStateLabels(): Record<string, string> {
  return {
    'bullish-boundary-triggered': '多头边界已触发',
    'bullish-transition-candidate': '多头临界',
    'oversold-exhaustion-candidate': '下跌衰竭临界',
    'oversold-rebound-triggered': '超跌反弹已触发',
    'descending-envelope-3m-approaching': '3月斜边临界',
    'descending-envelope-3m-broken': '3月斜边突破',
    'descending-envelope-6m-broken': '6月斜边突破',
    'descending-envelope-1y-broken': '1年斜边突破',
    'sudden-volume-expansion': '突然放量',
    'boundary-volume-contraction': '边界缩量',
    'relative-strength-regime': '相对强弱异动',
    neutral: '中性',
  }
}

function observationSummary(item: BoardDailyObservation) {
  return item.rendered_summary
    || (item.coverage_state !== 'complete'
      ? '结论：板块日线覆盖不足，未生成有效一级结论。'
      : `状态：${stateLabel(item.state_codes)}`)
}

function evidenceTitle(evidence: SignalEvidence) {
  if (evidence.evidence_type === 'market-style-divergence') return '上证/活跃市值风格差'
  if (evidence.evidence_type.startsWith('m4-')) {
    return ({ line: '趋势线', zone: '关键位/成交区', pattern: '形态', transition: '突破/破位状态' } as Record<string, string>)[
      evidence.evidence_type.slice(3)
    ] ?? 'M4 结构证据'
  }
  return evidence.payload.board_name ?? evidence.evidence_type
}

function evidenceDetail(evidence: SignalEvidence) {
  if (evidence.source_run_id && evidence.source_item_id) return '点击后高亮当轮精确 M4 图形证据'
  if (evidence.evidence_type === 'board-daily-observation') return '当日一级固定算法观察'
  if (evidence.evidence_type === 'market-style-divergence') return '固定算法 5/20 日相对收益比较'
  const classification = evidence.payload.board_classification === 'industry' ? '行业板块' : '概念板块'
  return `${classification} · 板块第 ${evidence.payload.rank ?? '-'} · 得分 ${((evidence.payload.score ?? 0) * 100).toFixed(1)}`
}

function buildCriticalAlert(item?: BoardDailyObservation | SignalItem) {
  if (!item) return undefined
  const states = 'state_codes' in item ? item.state_codes : item.payload.state_codes ?? []
  const critical = [
    'bullish-boundary-triggered', 'oversold-rebound-triggered',
    'bullish-transition-candidate', 'oversold-exhaustion-candidate',
  ].find(value => states.includes(value))
  if (!critical) return undefined
  const metrics = ('metrics' in item ? item.metrics : item.payload.metrics) ?? {}
  const envelopes = metrics.descending_envelopes as Record<string, { boundary?: number; distance_atr?: number }> | undefined
  const nearest = Object.entries(envelopes ?? {})
    .filter((entry): entry is [string, { boundary?: number; distance_atr: number }] => entry[1]?.distance_atr != null)
    .sort((left, right) => Math.abs(left[1].distance_atr) - Math.abs(right[1].distance_atr))[0]
  const effectiveDate = 'effective_date' in item ? item.effective_date : String(item.payload.effective_date ?? '')
  const reasons = 'attention_reasons' in item ? item.attention_reasons : item.payload.attention_reasons ?? []
  const bullish = critical.startsWith('bullish')
  const triggered = critical.endsWith('triggered')
  const boundary = nearest
    ? `${nearest[0]} 边界 ${nearest[1].boundary?.toFixed(2) ?? '-'} · 距离 ${nearest[1].distance_atr.toFixed(2)} ATR`
    : '结构边界由精确 M4 证据补充'
  return {
    title: stateLabel([critical]),
    tone: triggered ? 'triggered' : 'candidate',
    facts: `${effectiveDate} · ${boundary}`,
    reason: reasons.slice(0, 2).map(attentionReasonLabel).join(' · ') || '固定结构门槛已命中',
    condition: bullish
      ? '确认：放量收于边界上方；失效：重新跌回边界下方 0.25 ATR'
      : '确认：收盘突破短期反转边界；失效：放量创出新低',
  }
}

function attentionReasonLabel(value: string) {
  if (value.includes('descending-envelope-broken')) return '下降边界收盘突破'
  if (value.includes('descending-envelope-approaching') || value === 'bullish-boundary-proximity') return '接近下降边界'
  if (value === 'downside-exhaustion') return '下跌扩展且动能减速'
  if (value === 'oversold-rebound-triggered') return '超跌反转边界已触发'
  if (value === 'sudden-volume-expansion') return '成交量异常放大'
  if (value.startsWith('prior-state-')) return '较上一交易日状态变化'
  return value
}
