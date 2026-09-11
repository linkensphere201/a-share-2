import {
  useEffect, useMemo, useRef, useState, type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { ArrowLeft, Boxes, Eye, Layers3, ListFilter, MessageSquare, Pin, PinOff, Play, Radar, RefreshCw, RotateCcw, Search } from 'lucide-react'
import { ChartCanvas } from './ChartCanvas'
import { MarketBoardBadge } from './MarketBoardBadge'
import type { ThemeDefinition } from './themeStore'
import {
  listBoardObservations, listSignalAttention, listSignalDefinitions, listSignalItems,
  listSignalRuns, listSignalScores, loadObservationPool, loadSignalRun, setSignalAttention, startSignalRun,
  type BoardDailyObservation, type SignalAttention, type SignalChangeType,
  type ObservationPoolItem, type ObservationPoolSnapshot, type ObservationPoolSource,
  type SignalDefinition, type SignalEvidence, type SignalItem, type SignalProfile,
  type SignalRun, type SignalScoreResult,
} from './signalReviewClient'
import { SignalChatPanel } from './SignalChatPanel'
import {
  loadExactTrendAnalysis, loadTrendAnalysis, recalculateTrendAnalysis,
  type TrendAnalysisRun,
} from './trendAnalysisClient'
import { TradeScenarioPanel } from './TradeScenarioPanel'
import { TradingSystemControls } from './TradingSystemControls'
import { TrendExplanationPanel } from './TrendExplanationPanel'
import {
  createTradingSystemWindowState, normalizeTrendTradingSystemSettings,
  tradingSystemRegistry, type TradingSystemWindowState,
} from './tradingSystems'
import type { GeneratedBreakoutState } from './generatedAnalysisProjection'
import { logInfo, logWarning } from './eventLogger'
import { DailyConclusionSummary } from './DailyConclusionSummary'
import {
  dailyConclusionReferenceFor, dailyConclusionSource,
  mergeDailyConclusionAnalysis,
} from './dailyConclusionProjection'

type Props = { theme: ThemeDefinition; onClose: () => void }
type ProfileFilter = 'all' | SignalProfile
type ChangeFilter = 'all' | SignalChangeType
type DailyView = 'results' | 'opportunities' | 'leading' | 'hotspots' | 'observations' | 'board-pool' | 'stock-pool'
type HotspotFilter = 'all' | 'rising' | 'confirmed' | 'fading'
type LeadingFilter = 'all' | 'strengthening' | 'confirmed'
type PoolLifecycleFilter = 'all' | ObservationPoolItem['lifecycle_state']
type PoolPresentationView = 'focus' | 'opportunity' | 'risk' | 'all'
type SignalRunContextMenu = { x: number; y: number; run: SignalRun }

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
  const [pendingEffectiveDate, setPendingEffectiveDate] = useState<string>()
  const [runContextMenu, setRunContextMenu] = useState<SignalRunContextMenu>()
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
  const [dailyView, setDailyView] = useState<DailyView>('results')
  const [observationQuery, setObservationQuery] = useState('')
  const [observations, setObservations] = useState<BoardDailyObservation[]>([])
  const [observationTotal, setObservationTotal] = useState(0)
  const [selectedObservation, setSelectedObservation] = useState<BoardDailyObservation>()
  const [observationPool, setObservationPool] = useState<ObservationPoolSnapshot>()
  const [selectedPoolItem, setSelectedPoolItem] = useState<ObservationPoolItem>()
  const [poolQuery, setPoolQuery] = useState('')
  const [poolLifecycle, setPoolLifecycle] = useState<PoolLifecycleFilter>('all')
  const [poolPresentation, setPoolPresentation] = useState<PoolPresentationView>('focus')
  const [attention, setAttention] = useState<SignalAttention[]>([])
  const [scores, setScores] = useState<SignalScoreResult[]>([])
  const [selectedScoreSystem, setSelectedScoreSystem] = useState('trend-breakout')
  const [hotspotFilter, setHotspotFilter] = useState<HotspotFilter>('rising')
  const [leadingFilter, setLeadingFilter] = useState<LeadingFilter>('strengthening')
  const [historySelection, setHistorySelection] = useState<{ symbol: string; entityKey?: string }>()
  const [exactAnalysis, setExactAnalysis] = useState<TrendAnalysisRun | null>(null)
  const [reviewTrendState, setReviewTrendState] = useState<TradingSystemWindowState>(createReviewTrendState)
  const [trendRecalculationState, setTrendRecalculationState] = useState<'idle' | 'running' | 'failed'>('idle')
  const [trendExplanationOpen, setTrendExplanationOpen] = useState(false)
  const [chartBreakoutState, setChartBreakoutState] = useState<GeneratedBreakoutState>()
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
    if (!runContextMenu) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setRunContextMenu(undefined)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [runContextMenu])

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
    const startedAt = performance.now()
    listSignalScores(selectedRun.run_id, controller.signal).then(value => {
      setScores(value)
      reportSignalTiming('评分数据加载', startedAt, {
        run_id: selectedRun.run_id, count: value.length,
      })
    })
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
    if (!['opportunities', 'leading', 'hotspots', 'observations'].includes(dailyView) || !selectedRun || selectedRun.status !== 'succeeded') {
      setObservations([])
      setObservationTotal(0)
      setSelectedObservation(undefined)
      return
    }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      const startedAt = performance.now()
      listBoardObservations(selectedRun.run_id, observationQuery, controller.signal)
        .then(value => {
          setObservations(value.items)
          setObservationTotal(value.total)
          setSelectedObservation(current => value.items.find(item =>
            item.symbol === (historySelection?.symbol ?? current?.symbol)))
          reportSignalTiming('板块观察加载', startedAt, {
            run_id: selectedRun.run_id, returned: value.items.length, total: value.total,
            query: observationQuery || undefined,
          })
        })
        .catch(reason => { if (reason.name !== 'AbortError') setError(String(reason)) })
    }, 180)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [dailyView, observationQuery, selectedRun?.run_id, selectedRun?.status, historySelection])

  useEffect(() => {
    if (!selectedRun || !['opportunities', 'leading', 'hotspots', 'observations'].includes(dailyView)) return
    const startedAt = performance.now()
    let secondFrame = 0
    const firstFrame = window.requestAnimationFrame(() => {
      secondFrame = window.requestAnimationFrame(() => reportSignalTiming('复盘页签绘制', startedAt, {
        run_id: selectedRun.run_id, view: dailyView,
        observations: observations.length, scores: scores.length,
      }, 250))
    })
    return () => {
      window.cancelAnimationFrame(firstFrame)
      if (secondFrame) window.cancelAnimationFrame(secondFrame)
    }
  }, [dailyView, observations.length, scores.length, selectedRun?.run_id])

  useEffect(() => {
    if (!['board-pool', 'stock-pool'].includes(dailyView)
      || !selectedRun || selectedRun.status !== 'succeeded') {
      setObservationPool(undefined)
      setSelectedPoolItem(undefined)
      return
    }
    const controller = new AbortController()
    const poolKind = dailyView === 'board-pool' ? 'board' : 'stock'
    loadObservationPool(selectedRun.run_id, poolKind, controller.signal)
      .then(value => {
        setObservationPool(value)
        if (poolKind === 'stock') {
          setPoolPresentation(value.items.some(item => item.payload.presentation_bucket)
            ? 'focus' : 'all')
        }
        setSelectedPoolItem(current => value.items.find(item => item.symbol === current?.symbol))
      })
      .catch(reason => {
        if (reason.name !== 'AbortError') {
          setObservationPool(undefined)
          setSelectedPoolItem(undefined)
          setError(String(reason))
        }
      })
    return () => controller.abort()
  }, [dailyView, selectedRun?.run_id, selectedRun?.status])

  const deepAnalysisRunId = selectedPoolItem?.payload.m4_analysis?.run_id
    ?? selectedObservation?.deep_analysis_run_id
    ?? selectedItem?.payload.deep_analysis_run_id
  const conclusionSource = useMemo(() => dailyConclusionSource(
    selectedRun?.run_id, selectedObservation,
    selectedItem?.profile === 'attention' ? selectedItem : undefined,
  ), [selectedItem, selectedObservation, selectedRun?.run_id])
  const conclusionAnalysis = useMemo(
    () => mergeDailyConclusionAnalysis(exactAnalysis, conclusionSource),
    [conclusionSource, exactAnalysis],
  )
  const displayedAnalysis = conclusionAnalysis.run
  useEffect(() => {
    setExactAnalysis(null)
    setTrendExplanationOpen(false)
    setChartBreakoutState(undefined)
    setScenarioHighlightedItemId(undefined)
    setSelectedScenarioTarget(undefined)
    setTrendRecalculationState('idle')
    setReviewTrendState(current => ({ ...current, analysisStatus: 'not-run' }))
    if (!deepAnalysisRunId) return
    const controller = new AbortController()
    const startedAt = performance.now()
    loadExactTrendAnalysis(deepAnalysisRunId, controller.signal)
      .then(value => {
        setExactAnalysis(value)
        setReviewTrendState(current => ({ ...current, analysisStatus: 'current' }))
        reportSignalTiming('趋势分析图层加载', startedAt, {
          run_id: selectedRun?.run_id, analysis_run_id: deepAnalysisRunId,
          symbol: selectedPoolItem?.symbol ?? selectedObservation?.symbol ?? selectedItem?.symbol,
          item_count: value.items.length,
        })
      })
      .catch(reason => {
        if (reason.name !== 'AbortError') setError(`深度分析图层读取失败：${String(reason)}`)
      })
    return () => controller.abort()
  }, [deepAnalysisRunId, selectedItem?.symbol, selectedObservation?.symbol, selectedPoolItem?.symbol, selectedRun?.run_id])

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
  const scoresBySystem = useMemo(() => {
    const grouped = new Map<string, SignalScoreResult[]>()
    scores.forEach(item => {
      const values = grouped.get(item.system_id)
      if (values) values.push(item)
      else grouped.set(item.system_id, [item])
    })
    return grouped
  }, [scores])
  const boardScoreSystems = useMemo(() => [...scoresBySystem.entries()]
    .filter(([, values]) => values.some(item => item.entity_scope === 'board'))
    .map(([system]) => system), [scoresBySystem])
  useEffect(() => {
    if (boardScoreSystems.length && !boardScoreSystems.includes(selectedScoreSystem)) {
      setSelectedScoreSystem(boardScoreSystems[0])
    }
  }, [boardScoreSystems, selectedScoreSystem])
  const scoreBySymbol = useMemo(() => new Map(
    (scoresBySystem.get(selectedScoreSystem) ?? [])
      .map(item => [item.symbol, item]),
  ), [scoresBySystem, selectedScoreSystem])
  const hardEventSummary = useMemo(() => {
    const active = (scoresBySystem.get('trend-breakout') ?? [])
      .flatMap(score => score.hard_events.filter(event => event.state !== 'resolved'))
    const counts = new Map<string, number>()
    active.forEach(event => counts.set(event.event_type, (counts.get(event.event_type) ?? 0) + 1))
    return { total: active.length, counts: [...counts.entries()].sort((a, b) => b[1] - a[1]) }
  }, [scoresBySystem])
  const displayedObservations = useMemo(() => [...observations]
    .filter(item => dailyView !== 'opportunities' || scoreBySymbol.get(item.symbol)?.eligible)
    .filter(item => dailyView !== 'hotspots' || hotspotMatches(
      scoreBySymbol.get(item.symbol), hotspotFilter,
    ))
    .filter(item => dailyView !== 'leading' || leadingMatches(
      scoreBySymbol.get(item.symbol), leadingFilter,
    ))
    .sort((left, right) => {
      const leftScore = scoreBySymbol.get(left.symbol)
      const rightScore = scoreBySymbol.get(right.symbol)
      return Number(Boolean(rightScore?.eligible)) - Number(Boolean(leftScore?.eligible))
        || (rightScore?.total_score ?? -1) - (leftScore?.total_score ?? -1)
        || left.symbol.localeCompare(right.symbol)
    }), [dailyView, hotspotFilter, leadingFilter, observations, scoreBySymbol])
  const hotspotSummary = useMemo(() => {
    const values = (scoresBySystem.get('board-hotspot-emergence') ?? [])
      .filter(item => item.radar_visible)
    return {
      all: values.length,
      rising: values.filter(item => item.score_direction === 'strengthening').length,
      confirmed: values.filter(item => ['hotspot-confirmed', 'accelerating'].includes(item.hotspot_stage ?? '')).length,
      fading: values.filter(item => ['diverging', 'exhausted'].includes(item.hotspot_stage ?? '')).length,
    }
  }, [scoresBySystem])
  const hotspotMarket = useMemo(() => (
    scoresBySystem.get('board-hotspot-emergence') ?? []
  ).find(item => item.market_liquidity_capacity), [scoresBySystem])
  const leadingSummary = useMemo(() => {
    const values = (scoresBySystem.get('board-hotspot-leading') ?? [])
      .filter(item => item.leading_visible)
    return {
      all: values.length,
      strengthening: values.filter(item => item.leading_state === 'strengthening').length,
      confirmed: values.filter(item => item.leading_state === 'launch-confirmed').length,
    }
  }, [scoresBySystem])
  const leadingMarket = useMemo(() => (
    scoresBySystem.get('board-hotspot-leading') ?? []
  ).find(item => item.market_liquidity_capacity), [scoresBySystem])
  const viewEmptyExplanation = useMemo(() => buildViewEmptyExplanation(
    dailyView, scoresBySystem, observationQuery, displayedObservations.length,
  ), [dailyView, scoresBySystem, observationQuery, displayedObservations.length])
  const displayedPoolItems = useMemo(() => {
    const query = poolQuery.trim().toLocaleLowerCase()
    return [...(observationPool?.items ?? [])].filter(item =>
      (poolLifecycle === 'all' || item.lifecycle_state === poolLifecycle)
      && (dailyView !== 'stock-pool' || poolPresentation === 'all'
        || item.payload.presentation_bucket === poolPresentation)
      && (!query || `${item.name} ${item.symbol} ${item.sources.map(source => source.reason).join(' ')}`
        .toLocaleLowerCase().includes(query)),
    ).sort((left, right) => (
      poolPresentation === 'all' ? left.rank - right.rank
        : (left.payload.presentation_rank ?? left.rank)
          - (right.payload.presentation_rank ?? right.rank)
    ) || left.symbol.localeCompare(right.symbol))
  }, [dailyView, observationPool, poolLifecycle, poolPresentation, poolQuery])
  useEffect(() => {
    if (displayedPoolItems.some(item => item.symbol === selectedPoolItem?.symbol)) return
    setSelectedPoolItem(undefined)
  }, [displayedPoolItems, selectedPoolItem?.symbol])
  useEffect(() => {
    if (filtered.some(item => item.item_id === selectedItem?.item_id)) return
    setSelectedItem(undefined)
  }, [filtered, selectedItem?.item_id])
  const counts = useMemo(() => Object.fromEntries(
    (['recent', 'historical', 'market', 'attention'] as SignalProfile[])
      .map(value => [value, items.filter(item => item.active && item.profile === value).length]),
  ) as Record<SignalProfile, number>, [items])
  const daily = selectedDefinition?.cadence === 'daily'
  const workspaceChatAvailable = runs.some(item => item.status === 'succeeded')
  const inspected = selectedPoolItem ?? selectedObservation ?? selectedItem
  const inspectedScore = selectedPoolItem?.payload.opportunity_score
    ?? (selectedObservation
    ? scoreBySymbol.get(selectedObservation.symbol)
    : selectedItem?.payload.score_result)
  const activeEvidenceId = highlightedEvidenceId ?? selectedEvidenceId
  const highlightedAnalysisItemId = scenarioHighlightedItemId ?? selectedItem?.evidence.find(
    evidence => evidence.evidence_id === activeEvidenceId,
  )?.source_item_id ?? undefined
  const criticalAlert = buildCriticalAlert(selectedObservation ?? selectedItem)
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
    if (itemId?.startsWith('pool:')) {
      setHighlightedEvidenceId(evidenceId)
      return
    }
    setHighlightedEvidenceId(itemId === selectedItem?.item_id ? evidenceId : undefined)
  }
  const activateReference = (itemId: string, evidenceId: string) => {
    if (itemId.startsWith('pool:')) {
      const source = selectedPoolItem?.sources.find(value =>
        `${value.source_type}:${value.source_entity_key}` === evidenceId)
      if (source) void activatePoolSource(source)
      return
    }
    const item = items.find(value => value.item_id === itemId)
    if (!item) return
    setSelectedItem(item)
    setSelectedEvidenceId(evidenceId)
    setHighlightedEvidenceId(undefined)
  }

  const run = async (effectiveDate?: string) => {
    if (!selectedDefinition || runBusy) return
    setRunContextMenu(undefined)
    setPendingEffectiveDate(effectiveDate)
    setStartingRun(true)
    try {
      setError('')
      const next = await startSignalRun(selectedDefinition.signal_id, effectiveDate)
      setRuns(current => [next, ...current])
      setSelectedRun(next)
      logInfo('signal-review', effectiveDate ? '历史复盘重跑任务已创建' : '本期复盘任务已创建', {
        signal_id: selectedDefinition.signal_id,
        effective_date: next.effective_date,
        revision: next.revision,
        run_id: next.run_id,
      })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setStartingRun(false)
      setPendingEffectiveDate(undefined)
    }
  }
  const latestSucceededDate = runs.reduce((latest, item) => (
    item.status === 'succeeded' && item.effective_date > latest ? item.effective_date : latest
  ), '')
  const recalculationAvailable = Boolean(
    inspected && selectedRun?.effective_date === latestSucceededDate,
  )
  const recalculateInspectedTrend = async (nextState: TradingSystemWindowState) => {
    if (!inspected || !recalculationAvailable) return
    const startedAt = performance.now()
    setTrendRecalculationState('running')
    setError('')
    try {
      const settings = {
        ...normalizeTrendTradingSystemSettings(nextState.settings),
        provisionalPreview: false,
      }
      await recalculateTrendAnalysis(inspected.symbol, settings, nextState.settingsRevision)
      const snapshot = await loadTrendAnalysis(inspected.symbol)
      const result = snapshot.official ?? snapshot.effective
      if (!result || result.as_of_date !== selectedRun?.effective_date) {
        throw new Error('重新测算结果与当前复盘日期不一致，已拒绝覆盖本轮图层')
      }
      setExactAnalysis(result)
      setReviewTrendState({
        ...nextState, enabled: true, analysisStatus: 'current', settings,
      })
      setTrendRecalculationState('idle')
      reportSignalTiming('趋势分析重新测算', startedAt, {
        run_id: selectedRun?.run_id, symbol: inspected.symbol,
        analysis_run_id: result.run_id, item_count: result.items.length,
      }, 1500)
    } catch (reason) {
      setTrendRecalculationState('failed')
      setError(reason instanceof Error ? reason.message : String(reason))
      logWarning('signal-review', '趋势分析重新测算失败', {
        run_id: selectedRun?.run_id, symbol: inspected.symbol,
        error: reason instanceof Error ? reason.message : String(reason),
      })
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

  async function activatePoolSource(source: ObservationPoolSource) {
    setSelectedEvidenceId(`${source.source_type}:${source.source_entity_key}`)
    const analysisRunId = source.source_type === 'm4-analysis'
      ? source.source_reference
      : typeof source.payload.analysis_run_id === 'string' ? source.payload.analysis_run_id : undefined
    const analysisItemId = typeof source.payload.analysis_item_id === 'string'
      ? source.payload.analysis_item_id
      : typeof source.payload.scenario_item_id === 'string' ? source.payload.scenario_item_id : undefined
    if (analysisItemId) setScenarioHighlightedItemId(analysisItemId)
    if (!analysisRunId || analysisRunId === deepAnalysisRunId) return
    try {
      setExactAnalysis(await loadExactTrendAnalysis(analysisRunId))
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
        setSelectedPoolItem(undefined)
      }}>{definitions.map(item => <option key={item.signal_id} value={item.signal_id}>{item.name}</option>)}</select>
      {selectedDefinition && <span className="signal-description">{selectedDefinition.description}</span>}
      <button className={`icon-button${chatOpen ? ' active' : ''}`} title={workspaceChatAvailable ? 'Codex 多轮复盘讨论' : '完成至少一轮复盘后可讨论'} aria-label="Codex 信号讨论" disabled={!selectedDefinition || !workspaceChatAvailable} onClick={() => setChatOpen(value => !value)}><MessageSquare size={14}/></button>
      <button className="primary-button signal-run-action" disabled={!selectedDefinition || runBusy} onClick={() => void run()}>
        {runBusy ? <RefreshCw size={14} className="spin"/> : <Play size={14}/>}<span>{startingRun
          ? pendingEffectiveDate ? `正在重跑 ${pendingEffectiveDate}` : '正在创建本期任务'
          : activeRun ? `${phaseLabels[activeRun.phase] ?? activeRun.phase} ${activeProgress}%` : '运行本期信号'}</span>
      </button>
    </header>
    {error && <button className="signal-error" onClick={() => setError('')}>{error}</button>}
    <section className={`${inspected ? 'signal-grid inspector-open' : 'signal-grid'}${chatOpen ? ' chat-open' : ''}`} style={gridStyle}>
      <aside className="signal-runs">
        <header><span>历史轮次</span><small>{runs.length}</small></header>
        <div className="signal-scroll">{startingRun && <div className="signal-run-pending" role="status" aria-live="polite">
          <RefreshCw size={14} className="spin"/><span>{pendingEffectiveDate ? `正在重跑 ${pendingEffectiveDate}` : '正在创建本期任务'}<small>{pendingEffectiveDate ? '旧结果保留，正在创建同日新修订版' : '准备运行记录与数据截止日'}</small></span>
        </div>}{runs.length === 0 && !startingRun && <div className="signal-empty compact">尚未运行</div>}{runs.map(item => <button key={item.run_id} className={selectedRun?.run_id === item.run_id ? 'active' : ''} onClick={() => setSelectedRun(item)} onContextMenu={event => {
          event.preventDefault()
          setSelectedRun(item)
          setRunContextMenu({ ...signalRunMenuPosition(event.clientX, event.clientY), run: item })
        }}>
          <span>{item.effective_date} <i>R{item.revision}</i></span>
          <small>{item.status === 'running' ? `${phaseLabels[item.phase] ?? item.phase} ${progress}%` : item.status === 'failed' ? '执行失败' : `${item.item_count} 项`}</small>
          <em className={item.status}/>
        </button>)}</div>
      </aside>
      <div className="signal-column-resizer" role="separator" aria-orientation="vertical" aria-label="调整历史轮次栏宽度" title="左右拖动调整历史轮次栏宽度" onPointerDown={event => startColumnResize('runs', event)}/>
      <section className="signal-results">
        <header><span>{dailyView === 'observations' ? '全部板块观察'
          : dailyView === 'leading' ? '热点启动前导雷达'
          : dailyView === 'hotspots' ? '近期热点雷达'
          : dailyView === 'opportunities' ? '板块机会评分'
            : dailyView === 'board-pool' ? '板块观察池'
              : dailyView === 'stock-pool' ? '个股观察池' : '复盘结果'}</span><small>{['board-pool', 'stock-pool'].includes(dailyView)
          ? `当前 ${displayedPoolItems.length} / 全量 ${observationPool?.items.length ?? 0}`
          : dailyView !== 'results' ? `当前 ${displayedObservations.length} / 全量 ${observationTotal}`
            : selectedRun ? `+${selectedRun.added_count} =${selectedRun.retained_count} -${selectedRun.removed_count}` : '请选择轮次'}</small></header>
        {selectedRun?.status === 'running' && <div className="signal-progress"><i style={{ width: `${progress}%` }}/></div>}
        {daily && <div className="signal-view-switch">
          <button className={dailyView === 'results' ? 'active' : ''} onClick={() => { setDailyView('results'); setSelectedObservation(undefined); setSelectedPoolItem(undefined) }}><ListFilter size={12}/>今日关注</button>
          <button className={dailyView === 'opportunities' ? 'active' : ''} onClick={() => { setDailyView('opportunities'); setSelectedScoreSystem('trend-breakout'); setSelectedItem(undefined); setSelectedPoolItem(undefined) }}><Radar size={12}/>机会评分</button>
          <button className={dailyView === 'leading' ? 'active' : ''} onClick={() => { setDailyView('leading'); setSelectedScoreSystem('board-hotspot-leading'); setSelectedItem(undefined); setSelectedPoolItem(undefined) }}><Radar size={12}/>前导雷达</button>
          <button className={dailyView === 'hotspots' ? 'active' : ''} onClick={() => { setDailyView('hotspots'); setSelectedScoreSystem('board-hotspot-emergence'); setSelectedItem(undefined); setSelectedPoolItem(undefined) }}><Radar size={12}/>近期热点</button>
          <button className={dailyView === 'observations' ? 'active' : ''} onClick={() => { setDailyView('observations'); setSelectedItem(undefined); setSelectedPoolItem(undefined) }}><Eye size={12}/>全部观察</button>
          <button className={dailyView === 'board-pool' ? 'active' : ''} onClick={() => { setDailyView('board-pool'); setSelectedItem(undefined); setSelectedObservation(undefined) }}><Layers3 size={12}/>板块池</button>
          <button className={dailyView === 'stock-pool' ? 'active' : ''} onClick={() => { setDailyView('stock-pool'); setSelectedItem(undefined); setSelectedObservation(undefined) }}><Boxes size={12}/>个股池</button>
        </div>}
        {daily && boardScoreSystems.length > 0 && <div className="signal-score-system-select">当前体系<span>{scoreSystemLabel(selectedScoreSystem)}</span></div>}
        {dailyView === 'hotspots' && <div className="signal-hotspot-filters" aria-label="热点阶段筛选">
          {hotspotMarket && <span className="signal-hotspot-market">{marketCapacityLabel(hotspotMarket.market_liquidity_capacity)} · {marketDirectionLabel(hotspotMarket.market_liquidity_direction)} · {hotspotMarket.radar_slot_limit ?? 1}席</span>}
          {(['all', 'rising', 'confirmed', 'fading'] as HotspotFilter[]).map(value => <button key={value} className={hotspotFilter === value ? 'active' : ''} onClick={() => setHotspotFilter(value)}>{hotspotFilterLabel(value)}<small>{hotspotSummary[value]}</small></button>)}
        </div>}
        {dailyView === 'leading' && <div className="signal-hotspot-filters leading" aria-label="前导阶段筛选">
          {leadingMarket && <span className="signal-hotspot-market">{marketCapacityLabel(leadingMarket.market_liquidity_capacity)} · {marketDirectionLabel(leadingMarket.market_liquidity_direction)} · {leadingMarket.leading_slot_limit ?? 1}席</span>}
          {(['all', 'strengthening', 'confirmed'] as LeadingFilter[]).map(value => <button key={value} className={leadingFilter === value ? 'active' : ''} onClick={() => setLeadingFilter(value)}>{leadingFilterLabel(value)}<small>{leadingSummary[value]}</small></button>)}
        </div>}
        {daily && hardEventSummary.total > 0 && <div className="signal-hard-event-strip" role="status" title="独立硬异动是趋势体系捕获的量价或结构异常，不等于热点雷达席位">
          <span>独立硬异动 {hardEventSummary.total}</span><small>不等于热点席位</small>
          {hardEventSummary.counts.slice(0, 4).map(([eventType, count]) => <small key={eventType}>{hardEventLabel(eventType)} {count}</small>)}
        </div>}
        {['board-pool', 'stock-pool'].includes(dailyView) ? <>
          <label className="signal-observation-search"><Search size={12}/><input aria-label="搜索观察池" value={poolQuery} onChange={event => setPoolQuery(event.target.value)} placeholder="名称、代码或入池原因"/></label>
          {dailyView === 'stock-pool' && <div className="signal-filters pool-presentation">
            {(['focus', 'opportunity', 'risk', 'all'] as PoolPresentationView[]).map(value => <button key={value} className={poolPresentation === value ? 'active' : ''} onClick={() => setPoolPresentation(value)}>{poolPresentationLabel(value)} {poolPresentationCount(observationPool, value)}</button>)}
          </div>}
          <div className="signal-filters secondary pool-lifecycle">
            {(['all', 'new', 'active', 'strengthened', 'weakened', 'manual-pinned', 'cooldown'] as PoolLifecycleFilter[]).map(value => <button key={value} className={poolLifecycle === value ? 'active' : ''} onClick={() => setPoolLifecycle(value)}>{poolLifecycleLabel(value)}</button>)}
          </div>
          <div className="signal-result-head pool"><span>评分</span><span>标的</span><span>状态</span><span>来源</span></div>
          <div className="signal-scroll signal-pool-list">{displayedPoolItems.map(item => <button key={item.symbol} className={selectedPoolItem?.symbol === item.symbol ? 'active' : ''} onClick={() => {
            setSelectedPoolItem(item)
            setSelectedItem(undefined)
            setSelectedObservation(undefined)
            setSelectedEvidenceId(undefined)
            setHighlightedEvidenceId(undefined)
          }}>
            <PoolScoreBadge item={item}/>
            <span><span className="instrument-name-line"><b>{item.name}</b><MarketBoardBadge instrument={item}/></span><small>{item.symbol}</small></span>
            <span className={`pool-lifecycle-state ${item.lifecycle_state}`}>{poolLifecycleLabel(item.lifecycle_state)}<small>{poolClassification(item)}</small></span>
            <span className="pool-source-count">{item.sources.length}<small>{item.payload.recognized ? '含辨识度' : '条证据'}</small></span>
          </button>)}</div>
        </> : dailyView !== 'results' ? <>
          <label className="signal-observation-search"><Search size={12}/><input aria-label="搜索板块观察" value={observationQuery} onChange={event => setObservationQuery(event.target.value)} placeholder="板块名称或代码"/></label>
          <div className="signal-result-head observation"><span>评分</span><span>板块</span><span>状态</span><span>关注</span></div>
          <div className="signal-scroll">{viewEmptyExplanation && <SignalViewEmpty explanation={viewEmptyExplanation}/>} {displayedObservations.map(item => {
            const entry = attention.find(value => value.symbol === item.symbol)
            const score = scoreBySymbol.get(item.symbol)
            return <button key={item.symbol} className={selectedObservation?.symbol === item.symbol ? 'active' : ''} onClick={() => { setSelectedObservation(item); setSelectedItem(undefined); setSelectedPoolItem(undefined) }}>
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
          <div className="signal-scroll">{selectedRun?.status === 'failed' && <div className="signal-empty compact error">{selectedRun.error}</div>}{filtered.map(item => <button key={item.item_id} className={`${selectedItem?.item_id === item.item_id ? 'active ' : ''}${item.active ? '' : 'inactive'}`} onClick={() => { setSelectedItem(item); setSelectedObservation(undefined); setSelectedPoolItem(undefined); setSelectedEvidenceId(undefined); setHighlightedEvidenceId(undefined) }}>
            <ScoreBadge score={item.payload.score_result} fallback={item.score * 100} rank={item.rank}/><span><span className="instrument-name-line"><b>{item.name}</b><MarketBoardBadge instrument={item}/></span><small>{item.symbol}</small></span>{daily
              ? <SignalStateCell states={item.payload.state_codes} fallback={profileLabels[item.profile]}/>
              : <span>{item.payload.board_count ?? 0}<small>{profileLabels[item.profile]}</small></span>}
            <span className={`change ${item.change_type}`}>{changeLabels[item.change_type]}</span>
          </button>)}</div>
        </>}
      </section>
      {inspected && <div className="signal-column-resizer" role="separator" aria-orientation="vertical" aria-label="调整复盘结果栏宽度" title="左右拖动调整复盘结果栏宽度" onPointerDown={event => startColumnResize('results', event)}/>}
      {inspected && <section ref={inspectorRef} className={`signal-inspector${criticalAlert ? ' has-critical' : ''}`} style={{ gridTemplateRows: criticalAlert ? `42px auto minmax(220px, 1fr) ${evidenceHeight}px` : `42px minmax(220px, 1fr) ${evidenceHeight}px` }}>
        <header><span className="instrument-name-line"><span>{inspected.name}</span>{(selectedItem || selectedPoolItem) && <MarketBoardBadge instrument={selectedPoolItem ?? selectedItem!}/>}</span><small>{selectedPoolItem
          ? `${poolLifecycleLabel(selectedPoolItem.lifecycle_state)} · 排名 #${selectedPoolItem.rank} · ${selectedPoolItem.sources.length} 条来源`
          : selectedItem ? `${profileLabels[selectedItem.profile]} · 得分 ${(selectedItem.score * 100).toFixed(1)} · 置信 ${(selectedItem.confidence * 100).toFixed(1)}` : `${selectedObservation?.effective_date} · 一级固定分析`}</small>{selectedObservation
          ? <button className="icon-button" title={pinned ? '取消手工固定' : '加入手工观察池'} aria-label={pinned ? '取消手工固定' : '加入手工观察池'} onClick={() => void togglePinned()}>{pinned ? <PinOff size={13}/> : <Pin size={13}/>}</button>
          : null}</header>
        {criticalAlert && <div className={`signal-critical-alert ${criticalAlert.tone}`}>
          <span>{criticalAlert.title}<small>{criticalAlert.facts}</small></span>
          <span>{criticalAlert.reason}<small>{criticalAlert.condition}</small></span>
        </div>}
        <div className="signal-chart">{inspected
          ? <ChartCanvas key={`${selectedRun?.run_id}:${inspected.symbol}`} symbol={inspected.symbol} instrumentName={inspected.name} instrumentKind={selectedPoolItem?.kind ?? selectedItem?.kind ?? 'sector'} focused theme={theme} range="1Y" priceMode="normal" volumeVisible indicator="none" settlementVisible={false} openInterestVisible={false} asOfDate={selectedRun?.effective_date}
              toolbarContent={<TradingSystemControls embedded instrumentKind={selectedPoolItem?.kind ?? selectedItem?.kind ?? 'sector'} state={reviewTrendState} breakoutState={chartBreakoutState} analysisRun={displayedAnalysis} recalculationState={trendRecalculationState} recalculationAvailable={recalculationAvailable} recalculationDisabledReason="历史轮次保持冻结，只能重新测算最新复盘日期" onChange={setReviewTrendState} onRecalculate={recalculateInspectedTrend} explanationOpen={trendExplanationOpen} onExplanationOpenChange={setTrendExplanationOpen}/>}
              trendAnalysisEnabled={reviewTrendState.enabled && Boolean(displayedAnalysis)} trendAnalysisOverride={displayedAnalysis} highlightedAnalysisItemId={highlightedAnalysisItemId} selectedScenarioTarget={selectedScenarioTarget} riskRewardVisible={scenarioVisible}
              showTentativePivots={Boolean(reviewTrendState.settings.showTentativePivots)} shortTrendLinesVisible={reviewTrendState.layers['short-trend-lines'] !== false} mediumTrendLinesVisible={reviewTrendState.layers['medium-trend-lines'] !== false} longTrendLinesVisible={reviewTrendState.layers['long-trend-lines'] !== false} keyLevelsVisible={reviewTrendState.layers['key-levels'] !== false} volumeZonesVisible={reviewTrendState.layers['volume-zones'] !== false} patternsVisible={reviewTrendState.layers.patterns !== false} breakoutStateVisible={reviewTrendState.layers['breakout-state'] !== false} trendIsolation={reviewTrendState.isolate} onBreakoutStateChange={setChartBreakoutState}/>
          : <div className="signal-empty">选择一项结果查看 K 线</div>}
          {trendExplanationOpen && displayedAnalysis && <TrendExplanationPanel
            run={displayedAnalysis}
            selectedScenarioTarget={selectedScenarioTarget}
            scenarioVisible={scenarioVisible}
            onScenarioTargetChange={setSelectedScenarioTarget}
            onScenarioVisibleChange={setScenarioVisible}
            onHighlightItemChange={setScenarioHighlightedItemId}
            onClose={() => {
              setTrendExplanationOpen(false)
              setScenarioHighlightedItemId(undefined)
            }}
          />}
        </div>
        <div className="signal-evidence"><div className="signal-evidence-resizer" role="separator" aria-orientation="horizontal" aria-label="调整固定算法结论高度" title="上下拖动调整结论区域高度" onPointerDown={startEvidenceResize}/><header><span>{selectedPoolItem ? '观察池依据' : selectedObservation ? '一级分析' : selectedItem?.payload.rendered_summary ? '固定算法结论' : '引用证据'}</span><small>{selectedPoolItem ? selectedPoolItem.sources.length : selectedObservation ? selectedObservation.state_codes.length : selectedItem?.evidence.length ?? 0}</small></header>
          <div className="signal-evidence-content">
            {inspectedScore && <ScoreSummary score={inspectedScore} onHistorySelect={openHistoricalScore} onEvidenceHighlight={(eventType) => setScenarioHighlightedItemId(dailyConclusionReferenceFor('hard-event', conclusionAnalysis.references, eventType))}/>}
            {displayedAnalysis && <TradeScenarioPanel run={displayedAnalysis} selectedTargetLabel={selectedScenarioTarget} visible={scenarioVisible} onTargetChange={setSelectedScenarioTarget} onVisibleChange={setScenarioVisible} onHighlightItemChange={setScenarioHighlightedItemId}/>}
            {selectedPoolItem ? <PoolEvidence item={selectedPoolItem} selectedSourceId={highlightedEvidenceId ?? selectedEvidenceId} onSourceActivate={activatePoolSource}/>
              : selectedObservation ? <DailyConclusionSummary text={observationSummary(selectedObservation)} references={conclusionAnalysis.references} onHighlight={setScenarioHighlightedItemId}/> : <div className="signal-analysis-details">{selectedItem?.payload.rendered_summary && <DailyConclusionSummary text={selectedItem.payload.rendered_summary} references={conclusionAnalysis.references} onHighlight={setScenarioHighlightedItemId}/>}<div className="signal-evidence-list">{selectedItem?.evidence.map(evidence => <button key={evidence.evidence_id} className={(highlightedEvidenceId ?? selectedEvidenceId) === evidence.evidence_id ? 'active' : ''} onClick={() => setSelectedEvidenceId(evidence.evidence_id)} title="点击查看该轮固定算法引用的原始或 M4 证据">
            <code>[{evidence.alias}]</code><span>{evidenceTitle(evidence)}<small>{evidenceDetail(evidence)}</small></span>
          </button>)}</div></div>}
          </div>
        </div>
      </section>}
      {chatOpen && <div className="signal-column-resizer" role="separator" aria-orientation="vertical" aria-label="调整Codex对话栏宽度" title="左右拖动调整Codex对话栏宽度" onPointerDown={event => startColumnResize('chat', event)}/>}
      {chatOpen && selectedDefinition && <SignalChatPanel key={selectedDefinition.signal_id} signalId={selectedDefinition.signal_id} runs={runs} run={selectedRun} items={items} selectedItem={selectedItem} selectedPoolItem={selectedPoolItem} onReferencePreview={previewReference} onReferenceActivate={activateReference} onClose={() => setChatOpen(false)}/>}
    </section>
    {runContextMenu && <div className="signal-context-layer" onPointerDown={event => {
      if (event.target === event.currentTarget) setRunContextMenu(undefined)
    }} onContextMenu={event => event.preventDefault()}>
      <div className="signal-context-menu" role="menu" style={{ left: runContextMenu.x, top: runContextMenu.y }}>
        <header>{runContextMenu.run.effective_date} · R{runContextMenu.run.revision}</header>
        <button role="menuitem" disabled={runBusy || runContextMenu.run.status === 'running'} onClick={() => void run(runContextMenu.run.effective_date)}>
          <RotateCcw size={14}/><span>重跑该日</span>
        </button>
      </div>
    </div>}
  </main>
}

function signalRunMenuPosition(x: number, y: number) {
  return {
    x: Math.max(8, Math.min(x, window.innerWidth - 210)),
    y: Math.max(8, Math.min(y, window.innerHeight - 100)),
  }
}

type SignalViewEmptyExplanation = {
  title: string
  detail: string
  reasons: string[]
}

function SignalViewEmpty({ explanation }: { explanation: SignalViewEmptyExplanation }) {
  return <div className="signal-view-empty" role="status">
    <span>{explanation.title}</span>
    <p>{explanation.detail}</p>
    {explanation.reasons.map(reason => <small key={reason}>{reason}</small>)}
  </div>
}

function buildViewEmptyExplanation(
  view: DailyView,
  scoresBySystem: Map<string, SignalScoreResult[]>,
  query: string,
  displayedCount: number,
): SignalViewEmptyExplanation | undefined {
  if (!['opportunities', 'leading', 'hotspots', 'observations'].includes(view) || displayedCount > 0) return undefined
  if (scoresBySystem.size === 0 && view !== 'observations') return undefined
  if (query.trim()) return {
    title: '当前搜索没有匹配结果',
    detail: `关键词“${query.trim()}”未命中当前视图，可清空搜索后查看本轮完整结果。`,
    reasons: [],
  }
  if (view === 'opportunities') {
    const values = scoresBySystem.get('trend-breakout') ?? []
    const eligible = values.filter(item => item.eligible).length
    return {
      title: eligible ? `该轮存在 ${eligible} 个机会，但列表未能匹配` : '该轮没有板块通过机会门槛',
      detail: eligible
        ? `趋势突破体系实际筛出 ${eligible}/${values.length} 个结果；若仍为空，属于界面数据匹配异常。`
        : `已评分 ${values.length} 个板块，必须同时满足完整数据、有效多头价格顺序和压力盈亏比不低于 3。`,
      reasons: topFilterReasons(values),
    }
  }
  if (view === 'hotspots') {
    const values = scoresBySystem.get('board-hotspot-emergence') ?? []
    const candidates = values.filter(item => item.hotspot_stage && item.hotspot_stage !== 'failed').length
    const visible = values.filter(item => item.radar_visible).length
    return {
      title: '该轮没有热点进入可见席位',
      detail: `固定算法发现 ${candidates} 个初始热点候选，${visible} 个通过持续窗口与市场容量约束。初始候选不会直接作为热点展示。`,
      reasons: topFilterReasons(values, true),
    }
  }
  if (view === 'leading') {
    const values = scoresBySystem.get('board-hotspot-leading') ?? []
    const candidates = values.filter(item => item.leading_state && item.leading_state !== 'invalidated').length
    const visible = values.filter(item => item.leading_visible).length
    return {
      title: '该轮没有前导信号进入可见席位',
      detail: `固定算法保留 ${candidates} 个观察候选，${visible} 个满足结构临界、证据增强和席位约束。`,
      reasons: topFilterReasons(values),
    }
  }
  return {
    title: '当前没有板块观察结果',
    detail: '本轮可能尚未完成，或板块日线覆盖不足。',
    reasons: [],
  }
}

function topFilterReasons(values: SignalScoreResult[], includeWindow = false): string[] {
  const counts = new Map<string, number>()
  values.forEach(item => {
    const reasons = [
      ...item.disqualifiers,
      ...(includeWindow ? item.hotspot_window_failure_reasons ?? [] : []),
    ]
    new Set(reasons).forEach(reason => counts.set(reason, (counts.get(reason) ?? 0) + 1))
  })
  return [...counts.entries()].sort((left, right) => right[1] - left[1]).slice(0, 4)
    .map(([reason, count]) => `${filterReasonLabel(reason)}：${count} 个`)
}

function filterReasonLabel(value: string): string {
  return ({
    'invalid-long-price-ordering': '入场、止损与目标位顺序无效',
    'no-credible-target-at-3r': '没有达到 1:3 的可信目标位',
    'scenario-no-entry': '当前结构尚无入场场景',
    'incomplete-current-date-data': '当日数据不完整',
    'insufficient-history': '历史长度不足',
    'stale-through-effective-date': '数据未覆盖复盘日期',
    'weak-structure-proximity': '距离结构触发位不够近',
    'first-seen-after-confirmation': '首次发现时行情已经确认启动',
    'overextended-before-admission': '进入观察前已经过度延伸',
    'insufficient-window-history': '持续观察窗口不足',
    'weak-price': '短期价格强度不足',
    'weak-relative-strength': '相对强度不足',
    'weak-shape': '形态支持不足',
    'weak-leader': '板块龙头强度不足',
    'weak-breadth': '板块内部扩散不足',
    'weak-activity': '量能活跃度不足',
  } as Record<string, string>)[value] ?? value
}

function createReviewTrendState(): TradingSystemWindowState {
  const state = createTradingSystemWindowState(tradingSystemRegistry.get('trend')!)
  return {
    ...state,
    enabled: true,
    settings: { ...state.settings, provisionalPreview: false },
  }
}

const reportedSlowSignalOperations = new Set<string>()

function reportSignalTiming(
  operation: string,
  startedAt: number,
  context: Record<string, unknown>,
  warningThresholdMs = 1200,
) {
  const durationMs = Math.round(performance.now() - startedAt)
  const detail = { ...context, duration_ms: durationMs }
  logInfo('signal-review-performance', operation, detail)
  const warningKey = `${operation}:${String(context.run_id ?? '')}:${String(context.view ?? '')}`
  if (durationMs < warningThresholdMs || reportedSlowSignalOperations.has(warningKey)) return
  if (reportedSlowSignalOperations.size >= 50) reportedSlowSignalOperations.clear()
  reportedSlowSignalOperations.add(warningKey)
  logWarning('signal-review-performance', `${operation}耗时偏高`, detail)
}

function poolLifecycleLabel(value: PoolLifecycleFilter) {
  const labels: Record<PoolLifecycleFilter, string> = {
    all: '全部状态', new: '新入池', active: '持续观察', strengthened: '增强',
    weakened: '减弱', invalidated: '失效', cooldown: '冷却', 'manual-pinned': '手工固定',
  }
  return labels[value]
}

function poolPresentationLabel(value: PoolPresentationView) {
  return {
    focus: '重点观察', opportunity: '严格机会', risk: '风险异动', all: '完整归档',
  }[value]
}

function poolPresentationCount(
  snapshot: ObservationPoolSnapshot | undefined, value: PoolPresentationView,
) {
  if (!snapshot) return 0
  if (value === 'all') return snapshot.items.length
  return snapshot.items.filter(item => item.payload.presentation_bucket === value).length
}

function poolClassification(item: ObservationPoolItem) {
  if (item.payload.leading_eligible && !item.payload.hotspot_eligible && !item.payload.trend_eligible) {
    return `前导 · ${leadingStateLabel(item.payload.leading_state ?? undefined)}`
  }
  if (item.payload.hotspot_eligible && !item.payload.trend_eligible) {
    return `热点 · ${hotspotStageLabel(item.payload.hotspot_stage ?? undefined)}`
  }
  const classification = item.payload.opportunity_classification?.classification
  const labels: Record<string, string> = {
    'recognition-opportunity': '辨识度机会',
    'independent-opportunity': '独立行情机会',
    'recognition-watch': '辨识度观察',
    'independent-watch': '独立行情观察',
    'board-opportunity': '板块机会',
    'board-watch': '板块观察',
  }
  return classification ? (labels[classification] ?? classification) : item.kind === 'sector' ? '板块观察' : '个股观察'
}

function PoolScoreBadge({ item }: { item: ObservationPoolItem }) {
  const score = item.payload.opportunity_score
  const fallback = item.kind === 'sector'
    ? item.payload.trend_score ?? item.payload.leading_score ?? item.payload.hotspot_score ?? undefined
    : item.payload.independent_score
  return <ScoreBadge score={score} fallback={fallback} rank={item.rank}/>
}

function PoolEvidence({ item, selectedSourceId, onSourceActivate }: {
  item: ObservationPoolItem
  selectedSourceId?: string
  onSourceActivate: (source: ObservationPoolSource) => void
}) {
  const classification = item.payload.opportunity_classification
  const m4 = item.payload.m4_analysis
  return <div className="signal-pool-evidence">
    <div className="signal-pool-summary">
      <span>{poolClassification(item)}<small>{poolLifecycleLabel(item.lifecycle_state)}</small></span>
      <span>来源 {item.sources.length}<small>{(item.payload.source_types ?? []).join(' · ') || '固定算法'}</small></span>
      {classification && <span>{classification.opportunity_eligible ? '具备机会资格' : '继续观察'}<small>可信目标 {classification.credible_target_count ?? 0}</small></span>}
      {m4 && <span>M4 {m4.status ?? m4.state ?? '已测算'}<small>{m4.warning_count ?? 0} 条警告</small></span>}
    </div>
    <div className="signal-pool-sources">{item.sources.map((source, index) => {
      const sourceId = `${source.source_type}:${source.source_entity_key}`
      return <button key={`${sourceId}:${index}`} className={selectedSourceId === sourceId ? 'active' : ''} onClick={() => void onSourceActivate(source)} title="打开该项来源证据">
        <code>[O{index + 1}]</code>
        <span>{source.reason}<small>{source.source_type} · {source.source_entity_key}</small></span>
      </button>
    })}</div>
  </div>
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

function ScoreSummary({ score, onHistorySelect, onEvidenceHighlight }: {
  score: SignalScoreResult
  onHistorySelect?: (runId: string, symbol: string, entityKey?: string) => void
  onEvidenceHighlight?: (eventType?: string) => void
}) {
  return <section className={`signal-score-summary grade-${score.grade.toLowerCase()}`} aria-label="固定算法综合评分">
    <ScoreBadge score={score}/>
    <div><span>{score.verdict}</span><p>{score.summary}</p><small>{score.risk_summary}</small></div>
    <ScoreSparkline score={score} onHistorySelect={onHistorySelect}/>
    <em>{score.change_summary}</em>
    {score.system_id === 'board-hotspot-emergence' && <div className="signal-hotspot-state">
      <span className={score.score_direction ?? 'stable'}>{hotspotStageLabel(score.hotspot_stage)}<small>{score.score_direction === 'strengthening' ? '持续增强' : score.score_direction === 'declining' ? '正在衰退' : score.score_direction === 'new' ? '首次识别' : '强度稳定'}</small></span>
      {score.hotspot_wave_id && <span>{hotspotWaveStageLabel(score.hotspot_wave_stage)}<small>第 {score.hotspot_wave_sequence ?? 1} 轮 · 已运行 {score.hotspot_wave_session_count ?? 1} 日</small></span>}
      <span>{hotspotWindowStateLabel(score.hotspot_window_state)}<small>{score.hotspot_window_qualified_sessions ?? 0}/{score.hotspot_window_observed_sessions ?? 0} 日有效 · 形态支持 {score.hotspot_window_shape_support_sessions ?? 0} 日</small></span>
      <span>连续 {score.candidate_streak ?? 0} 日<small>峰值 {(score.peak_score ?? score.total_score).toFixed(0)} · 回撤 {(score.drawdown_from_peak ?? 0).toFixed(0)}</small></span>
      <span>{score.limit_up_count ?? 0} 家涨停<small>最高 {score.max_limit_up_streak ?? 0} 连板 · 破板 {score.broken_up_count ?? 0}</small></span>
      <span>{score.theme_name ?? boardCapacityLabel(score.board_capacity_tier)}<small>{score.theme_parent_name ? `${score.theme_parent_name} · ` : ''}{boardCapacityLabel(score.board_capacity_tier)} · 匹配 {score.capacity_fit_score?.toFixed(0) ?? '-'}</small></span>
    </div>}
    {score.system_id === 'board-hotspot-leading' && <div className="signal-hotspot-state leading">
      <span className={score.score_direction ?? 'stable'}>{leadingStateLabel(score.leading_state)}<small>{score.score_direction === 'strengthening' ? '证据继续增强' : score.score_direction === 'declining' ? '证据正在减弱' : '保持当前状态'}</small></span>
      <span>{score.leading_acceleration_count ?? 0} 项加速<small>连续观察 {score.leading_streak ?? 0} 日</small></span>
      <span>{hotspotShapeLabel(score.setup_path)}<small>{score.leading_objective_confirmed ? '已达到热点确认条件' : '尚未达到热点确认条件'}</small></span>
      <span>{score.limit_up_count ?? 0} 家涨停<small>最高 {score.max_limit_up_streak ?? 0} 连板 · 扩散 {((score.positive_return_5_ratio ?? 0) * 100).toFixed(0)}%</small></span>
      <span>{score.theme_name ?? boardCapacityLabel(score.board_capacity_tier)}<small>{boardCapacityLabel(score.board_capacity_tier)} · 容量匹配 {score.capacity_fit_score?.toFixed(0) ?? '-'}</small></span>
    </div>}
    {score.hard_events.length > 0 && <div className="signal-hard-events">{score.hard_events.slice(0, 3).map(event => <button
      key={event.event_type}
      className={`${event.direction} ${event.severity}`}
      onPointerEnter={() => onEvidenceHighlight?.(event.event_type)}
      onPointerLeave={() => onEvidenceHighlight?.(undefined)}
      onFocus={() => onEvidenceHighlight?.(event.event_type)}
      onBlur={() => onEvidenceHighlight?.(undefined)}
    >{hardEventLabel(event.event_type)}</button>)}</div>}
    <details className="signal-score-diagnostics"><summary>评分明细</summary>
      <div>{Object.entries(score.components).map(([name, value]) => <span key={name}>{name}<small>{value.toFixed(1)}</small></span>)}</div>
      {score.disqualifiers.length > 0 && <p>失格：{score.disqualifiers.join(' · ')}</p>}
      {score.penalties.length > 0 && <p>扣分：{score.penalties.map(item => `${item.code} ${item.points}`).join(' · ')}</p>}
    </details>
  </section>
}

function scoreSystemLabel(system: string) {
  return {
    'trend-breakout': '趋势突破',
    'board-hotspot-emergence': '近期热点',
    'board-hotspot-leading': '热点前导',
  }[system] ?? system
}

function leadingStateLabel(state?: string) {
  return {
    watch: '潜伏观察', strengthening: '临界增强',
    'launch-confirmed': '启动确认', invalidated: '失效',
  }[state ?? ''] ?? '数据不足'
}

function leadingFilterLabel(value: LeadingFilter) {
  return { all: '全部前导', strengthening: '临界增强', confirmed: '启动确认' }[value]
}

function hotspotShapeLabel(path?: string) {
  return {
    'platform-breakout': '平台临界', 'downtrend-reversal': '下降转势',
    'trend-continuation': '趋势延续', none: '结构未形成',
  }[path ?? ''] ?? path ?? '结构未形成'
}

function hotspotStageLabel(stage?: string) {
  return {
    'leader-ignited': '龙头点火',
    'trend-emerging': '趋势形成',
    'breadth-expanding': '扩散增强',
    'hotspot-confirmed': '热点确认',
    accelerating: '加速', diverging: '分歧衰减', exhausted: '退潮', failed: '未形成',
  }[stage ?? ''] ?? '数据不足'
}

function hotspotWaveStageLabel(stage?: string) {
  return {
    ignition: '波段点火', emerging: '波段成形', confirmed: '波段确认',
    advancing: '主升推进', diverging: '波段分歧', reaccelerating: '二次增强',
    exhausted: '波段退潮', ended: '波段结束',
  }[stage ?? ''] ?? '波段未建立'
}

function hotspotWindowStateLabel(state?: string) {
  return {
    insufficient: '窗口积累中', pulse: '单日脉冲', building: '窗口形成',
    persistent: '窗口持续', reaccelerating: '窗口再增强', fading: '窗口衰减',
    overextended: '形态过热', fragmented: '证据离散',
  }[state ?? ''] ?? '窗口未计算'
}

function hotspotFilterLabel(value: HotspotFilter) {
  return { all: '全部有效', rising: '持续增强', confirmed: '已确认', fading: '分歧/退潮' }[value]
}

function marketCapacityLabel(value?: string) {
  return ({ low: '低容量', medium: '中容量', high: '高容量' } as Record<string, string>)[value ?? ''] ?? '容量未知'
}

function boardCapacityLabel(value?: string) {
  return ({ micro: '微容量板块', small: '小容量板块', medium: '中容量板块', large: '大容量板块', mega: '超大容量板块' } as Record<string, string>)[value ?? ''] ?? '板块容量未知'
}

function marketDirectionLabel(value?: string) {
  return ({ contracting: '缩量', neutral: '平量', expanding: '放量' } as Record<string, string>)[value ?? ''] ?? '方向未知'
}

function hotspotMatches(score: SignalScoreResult | undefined, filter: HotspotFilter) {
  if (!score || score.system_id !== 'board-hotspot-emergence') return false
  const fadingWave = score.hotspot_wave_representative
    && ['diverging', 'exhausted', 'ended'].includes(score.hotspot_wave_stage ?? '')
  if (score.hotspot_stage === 'failed' && !fadingWave) return false
  if (!score.radar_visible && !fadingWave) return false
  if (filter === 'all') return true
  if (filter === 'rising') return score.score_direction === 'strengthening'
    && ((score.candidate_streak ?? 0) >= 1 || (score.max_limit_up_streak ?? 0) >= 2)
  if (filter === 'confirmed') return ['hotspot-confirmed', 'accelerating'].includes(score.hotspot_stage ?? '')
  return fadingWave || ['diverging', 'exhausted'].includes(score.hotspot_stage ?? '')
}

function leadingMatches(score: SignalScoreResult | undefined, filter: LeadingFilter) {
  if (!score || score.system_id !== 'board-hotspot-leading' || !score.leading_visible) return false
  if (filter === 'all') return true
  return filter === 'strengthening'
    ? score.leading_state === 'strengthening'
    : score.leading_state === 'launch-confirmed'
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
