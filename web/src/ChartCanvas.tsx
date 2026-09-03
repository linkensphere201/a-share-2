import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { AlertTriangle, Check, ChevronLeft, ChevronRight, MousePointer2, Move, MoveHorizontal, MoveVertical, PencilLine, Percent, RefreshCw, Settings2, Trash2, X, ZoomIn } from 'lucide-react'
import { logInfo, logWarning } from './eventLogger'
import {
  createTrendLine,
  saveTrendLine,
  type TrendLineAnchor,
  type TrendLineDrawing,
} from './drawingStore'
import { barsInRenderPeriod, chooseAnchor, orientTrendLineAnchors, replaceTrendLineAnchor, translateTrendLineAnchors, type LineGeometry, type TrendLineOrientation } from './trendLines'
import type { ThemeDefinition } from './themeStore'
import type { GeneratedAnalysisItem, TrendAnalysisRun } from './trendAnalysisClient'
import { dailyBarsUrl, useChartDailyBars } from './useChartDailyBars'
import { useChartDrawings } from './useChartDrawings'
import { useChartTrendAnalysis } from './useChartTrendAnalysis'
import {
  projectGeneratedPatterns,
  projectGeneratedPivots,
  projectGeneratedTrendLines,
  projectGeneratedZones,
  readGeneratedBreakoutState,
  type GeneratedBreakoutState,
  type GeneratedPatternGeometry,
  type GeneratedPivotGeometry,
  type GeneratedTrendLineGeometry,
  type GeneratedZoneGeometry,
} from './generatedAnalysisProjection'
import {
  projectMarketAnnotations,
  projectMeasurement,
  projectPaneTop,
  projectTrendLineAnchors,
  projectTrendLines,
} from './chartProjection'
import {
  aggregateBars,
  calculateChangePercent,
  calculateMacd,
  calculatePriceScaleMargins,
  candleColor,
  chooseLodBucket,
  clamp,
  createRangeMeasurement,
  detectPriceGaps,
  latestReadout,
  movingAverage,
  previousCloseByDate,
  remapLogicalRange,
  snapLogicalRangeToDataEdge,
  subtractMonths,
  subtractYears,
  visibleBarStats,
  type DailyBar,
  type RangeMeasurement,
  type Readout,
  type RenderBar,
} from './chartData'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  PriceScaleMode,
  createChart,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPaneApi,
  type ISeriesApi,
  type LineData,
  type Time,
} from 'lightweight-charts'
import type { ChartPaneRatios } from './workspace'
import type { ChartIndicator, ChartRange, PriceMode, VisibleRange } from './chartTypes'
import {
  ChartReadout,
  GeneratedAnalysisOverlay,
  MarketAnnotationOverlay,
  MeasurementOverlay,
  PaneHeader,
  TrendLineManager,
  TrendLineOverlay,
  applyMacdSeries,
  applyOpenInterestSeries,
  applyPaneRatios,
  applyVolumeSeries,
  captureViewport,
  chartPoint,
  emitPaneRatios,
  localPoint,
  rectangleFromPoints,
  resolveRangeSelection,
  setPaneStretchFactors,
  valueAt,
  type RangeSelection,
  type SelectionBox,
  type ViewportSnapshot,
} from './chartCanvasParts'

export type { ChartIndicator, ChartRange, PriceMode, VisibleRange } from './chartTypes'
export {
  assignGeneratedPatternLabels,
  projectGeneratedPatterns,
  projectGeneratedPivots,
  projectGeneratedTrendLines,
  projectGeneratedZones,
  readGeneratedBreakoutState,
} from './generatedAnalysisProjection'
export type { GeneratedBreakoutState } from './generatedAnalysisProjection'
export type { DailyBar } from './chartData'
type DrawingDrag = {
  pointerId: number
  start: TrendLineAnchor
  startX: number
  startY: number
}

type TrendLineMoveDrag = {
  pointerId: number
  drawing: TrendLineDrawing
  startDateIndex: number
  startPrice: number
  startX: number
  startY: number
  latest: TrendLineDrawing
  moved: boolean
}

type TrendLineAnchorDrag = {
  pointerId: number
  drawing: TrendLineDrawing
  anchorIndex: 0 | 1
  startX: number
  startY: number
  latest: TrendLineDrawing
  moved: boolean
}

type ChartCanvasProps = {
  symbol: string
  focused: boolean
  instrumentName?: string
  instrumentKind?: string
  priceBasis?: string | null
  ruleVersion?: string | null
  lineOnly?: boolean
  theme: ThemeDefinition
  range: ChartRange
  priceMode: PriceMode
  volumeVisible: boolean
  indicator: ChartIndicator
  settlementVisible: boolean
  openInterestVisible: boolean
  paneRatios?: ChartPaneRatios
  toolbarCollapsed?: boolean
  toolbarContent?: ReactNode
  initialVisibleRange?: VisibleRange
  onCoverageChange?: (bars: number, first?: string, last?: string) => void
  onVisibleRangeChange?: (value: VisibleRange) => void
  onVolumeVisibleChange?: (visible: boolean) => void
  onIndicatorChange?: (indicator: ChartIndicator) => void
  onSettlementVisibleChange?: (visible: boolean) => void
  onOpenInterestVisibleChange?: (visible: boolean) => void
  onPaneRatiosChange?: (ratios: ChartPaneRatios) => void
  onToolbarCollapsedChange?: (collapsed: boolean) => void
  trendAnalysisEnabled?: boolean
  showTentativePivots?: boolean
  shortTrendLinesVisible?: boolean
  longTrendLinesVisible?: boolean
  keyLevelsVisible?: boolean
  volumeZonesVisible?: boolean
  patternsVisible?: boolean
  breakoutStateVisible?: boolean
  trendIsolation?: boolean
  onBreakoutStateChange?: (value: GeneratedBreakoutState | undefined) => void
  onTrendAnalysisChange?: (value: TrendAnalysisRun | null) => void
  asOfDate?: string
  trendAnalysisOverride?: TrendAnalysisRun | null
  highlightedAnalysisItemId?: string
  supplementalAnalysisItems?: GeneratedAnalysisItem[]
  supplementalAnalysisOnly?: boolean
}

const rising = '#ef5350'
const falling = '#26a269'
const risingSoft = '#e99693'
const fallingSoft = '#70be9a'
const chartFontFamily = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", Arial, sans-serif'
export const chartLayoutOptions = {
  background: { type: ColorType.Solid, color: '#0d1014' },
  textColor: '#7f8997',
  fontFamily: chartFontFamily,
  fontSize: 12,
  panes: { separatorColor: '#303743', separatorHoverColor: '#4b84c6', enableResize: true },
  attributionLogo: false,
} as const

export const compactCrosshairMarkerOptions = {
  crosshairMarkerRadius: 2,
  crosshairMarkerBorderWidth: 1,
} as const

export function paneInteractionOptions(
  borderColor: string,
  accentColor: string,
  focused: boolean,
) {
  return {
    separatorColor: borderColor,
    separatorHoverColor: focused ? accentColor : borderColor,
    enableResize: focused,
  }
}

export { dailyBarsUrl }

export function ChartCanvas({
  symbol,
  focused,
  instrumentName,
  instrumentKind,
  priceBasis,
  ruleVersion,
  lineOnly = false,
  theme,
  range,
  priceMode,
  volumeVisible,
  indicator,
  settlementVisible,
  openInterestVisible,
  paneRatios,
  toolbarCollapsed: persistedToolbarCollapsed = false,
  toolbarContent,
  initialVisibleRange,
  onCoverageChange,
  onVisibleRangeChange,
  onVolumeVisibleChange,
  onIndicatorChange,
  onSettlementVisibleChange,
  onOpenInterestVisibleChange,
  onPaneRatiosChange,
  onToolbarCollapsedChange,
  trendAnalysisEnabled = false,
  showTentativePivots = true,
  shortTrendLinesVisible = true,
  longTrendLinesVisible = true,
  keyLevelsVisible = true,
  volumeZonesVisible = true,
  patternsVisible = true,
  breakoutStateVisible = true,
  trendIsolation = false,
  onBreakoutStateChange,
  onTrendAnalysisChange,
  asOfDate,
  trendAnalysisOverride,
  highlightedAnalysisItemId,
  supplementalAnalysisItems = [],
  supplementalAnalysisOnly = false,
}: ChartCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const closeLineRef = useRef<ISeriesApi<'Line'> | null>(null)
  const settlementLineRef = useRef<ISeriesApi<'Line'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const volumePaneRef = useRef<IPaneApi<Time> | null>(null)
  const ma5Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ma20Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ma60Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const macdDifRef = useRef<ISeriesApi<'Line'> | null>(null)
  const macdDeaRef = useRef<ISeriesApi<'Line'> | null>(null)
  const macdHistogramRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const macdPaneRef = useRef<IPaneApi<Time> | null>(null)
  const openInterestRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const openInterestPaneRef = useRef<IPaneApi<Time> | null>(null)
  const previousCloseByDateRef = useRef<Map<string, number>>(new Map())
  const renderedBarsRef = useRef<Map<string, RenderBar>>(new Map())
  const renderedBarListRef = useRef<RenderBar[]>([])
  const selectionDragRef = useRef<{ pointerId: number; startX: number; startY: number } | undefined>(undefined)
  const drawingDragRef = useRef<DrawingDrag | undefined>(undefined)
  const lineMoveDragRef = useRef<TrendLineMoveDrag | undefined>(undefined)
  const lineAnchorDragRef = useRef<TrendLineAnchorDrag | undefined>(undefined)
  const bucketRef = useRef(1)
  const applyBucketRef = useRef<(bucket: number, preserve?: ViewportSnapshot) => void>(() => undefined)
  const recalculateLodRef = useRef<() => void>(() => undefined)
  const resetAutoScaleRef = useRef<(visibleBars?: number, width?: number, low?: number, high?: number) => void>(() => undefined)
  const suppressLodRef = useRef(false)
  const timeAxisPointerActiveRef = useRef(false)
  const coverageCallbackRef = useRef(onCoverageChange)
  const visibleRangeCallbackRef = useRef(onVisibleRangeChange)
  const paneRatiosCallbackRef = useRef(onPaneRatiosChange)
  const emittedVisibleRangeRef = useRef<VisibleRange | undefined>(undefined)
  const priceModeRef = useRef(priceMode)
  const pendingViewportRef = useRef<ViewportSnapshot | undefined>(undefined)
  const skipRangeResetRef = useRef(false)
  const [readout, setReadout] = useState<Readout | null>(null)
  const [lodBucket, setLodBucket] = useState(1)
  const [selectionBox, setSelectionBox] = useState<SelectionBox>()
  const [rangeSelection, setRangeSelection] = useState<RangeSelection>()
  const [measurement, setMeasurement] = useState<RangeMeasurement>()
  const [drawingTool, setDrawingTool] = useState<'browse' | 'trend-line' | 'move'>('browse')
  const [drawingDraft, setDrawingDraft] = useState<[TrendLineAnchor, TrendLineAnchor]>()
  const [selectedDrawingId, setSelectedDrawingId] = useState<string>()
  const [drawingManagerOpen, setDrawingManagerOpen] = useState(false)
  const [movingDrawingId, setMovingDrawingId] = useState<string>()
  const [editingAnchor, setEditingAnchor] = useState<{ drawingId: string; anchorIndex: 0 | 1 }>()
  const [toolbarCollapsed, setToolbarCollapsed] = useState(persistedToolbarCollapsed)
  const [overlayRevision, setOverlayRevision] = useState(0)
  const initialTheme = useRef(theme).current
  const resetDrawingInteraction = useCallback(() => {
    setSelectedDrawingId(undefined)
    setDrawingDraft(undefined)
    drawingDragRef.current = undefined
    lineMoveDragRef.current = undefined
    lineAnchorDragRef.current = undefined
    setMovingDrawingId(undefined)
    setEditingAnchor(undefined)
    setDrawingTool('browse')
    setDrawingManagerOpen(false)
  }, [])
  const {
    target: drawingTarget,
    targetKey: drawingTargetKey,
    drawings,
    setDrawings,
    migrationCandidates: drawingMigrationCandidates,
    setIdentity: setDrawingIdentity,
    resolveMigration,
    updateDrawing,
    updateDrawingStyle,
    toggleDrawingVisibility,
    removeDrawing,
  } = useChartDrawings({
    symbol,
    instrumentKind,
    priceBasis,
    ruleVersion,
    onTargetReset: resetDrawingInteraction,
  })
  const onBeforePreserveBars = useCallback(() => {
    pendingViewportRef.current = captureViewport(
      chartRef.current,
      renderedBarListRef.current.length,
    )
    skipRangeResetRef.current = true
  }, [])
  const onBarsChanged = useCallback((next: DailyBar[]) => {
    previousCloseByDateRef.current = previousCloseByDate(next)
    setReadout(latestReadout(next))
  }, [])
  const onDailyLoadStart = useCallback(() => {
    selectionDragRef.current = undefined
    setSelectionBox(undefined)
    setRangeSelection(undefined)
    setMeasurement(undefined)
  }, [])
  const onInstrumentIdentity = useCallback((identity: {
    instrumentKind: string
    priceBasis?: string | null
    ruleVersion?: string | null
  }) => {
    setDrawingIdentity({
      symbol,
      instrumentKind: identity.instrumentKind,
      priceBasis: identity.priceBasis,
      ruleVersion: identity.ruleVersion,
    })
  }, [setDrawingIdentity, symbol])
  const {
    bars,
    barsRef,
    state,
    refreshing: manualRefreshing,
    refreshFeedback: manualRefreshFeedback,
    refreshNow: refreshIntradayNow,
  } = useChartDailyBars({
    symbol,
    asOfDate,
    onLoadStart: onDailyLoadStart,
    onBeforePreserve: onBeforePreserveBars,
    onBarsChanged,
    onCoverageChange,
    onInstrumentIdentity,
  })
  const {
    analysis: trendAnalysis,
    displayed: displayedTrendAnalysis,
    preview: trendAnalysisPreview,
  } = useChartTrendAnalysis({
    symbol,
    enabled: trendAnalysisEnabled,
    override: trendAnalysisOverride,
    supplementalItems: supplementalAnalysisItems,
    supplementalOnly: supplementalAnalysisOnly,
  })

  const averages = useMemo(() => ({
    ma5: movingAverage(bars, 5),
    ma20: movingAverage(bars, 20),
    ma60: movingAverage(bars, 60),
  }), [bars])
  const priceGaps = useMemo(() => detectPriceGaps(bars), [bars])
  const macd = useMemo(() => calculateMacd(bars), [bars])

  useEffect(() => { coverageCallbackRef.current = onCoverageChange }, [onCoverageChange])
  useEffect(() => { visibleRangeCallbackRef.current = onVisibleRangeChange }, [onVisibleRangeChange])
  useEffect(() => { paneRatiosCallbackRef.current = onPaneRatiosChange }, [onPaneRatiosChange])
  useEffect(() => setToolbarCollapsed(persistedToolbarCollapsed), [persistedToolbarCollapsed])
  useEffect(() => {
    if (!trendIsolation) return
    setDrawingTool('browse')
    setDrawingDraft(undefined)
    setDrawingManagerOpen(false)
    setRangeSelection(undefined)
    setSelectionBox(undefined)
    setMeasurement(undefined)
  }, [trendIsolation])

  useEffect(() => {
    chartRef.current?.applyOptions({
      layout: {
        ...chartLayoutOptions,
        background: { type: ColorType.Solid, color: theme.colors.chartBackground },
        textColor: theme.colors.text,
        panes: paneInteractionOptions(theme.colors.border, theme.colors.accent, focused),
      },
      grid: {
        vertLines: { color: theme.colors.chartGrid },
        horzLines: { color: theme.colors.chartGrid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: theme.colors.crosshair, labelBackgroundColor: theme.colors.raised },
        horzLine: { color: theme.colors.crosshair, labelBackgroundColor: theme.colors.raised },
      },
      rightPriceScale: { borderColor: theme.colors.border },
      timeScale: { borderColor: theme.colors.border },
    })
    closeLineRef.current?.applyOptions({ color: theme.colors.accent })
  }, [theme, focused])

  useEffect(() => {
    candleRef.current?.applyOptions({ visible: !lineOnly })
    closeLineRef.current?.applyOptions({ visible: lineOnly })
  }, [lineOnly])

  useEffect(() => {
    settlementLineRef.current?.applyOptions({ visible: settlementVisible && !lineOnly })
  }, [lineOnly, settlementVisible])

  useEffect(() => {
    if (!hostRef.current) return
    const chart = createChart(hostRef.current, {
      autoSize: true,
      layout: {
        ...chartLayoutOptions,
        background: { type: ColorType.Solid, color: initialTheme.colors.chartBackground },
        textColor: initialTheme.colors.text,
        panes: {
          ...chartLayoutOptions.panes,
          ...paneInteractionOptions(
            initialTheme.colors.border,
            initialTheme.colors.accent,
            focused,
          ),
        },
      },
      grid: {
        vertLines: { color: initialTheme.colors.chartGrid },
        horzLines: { color: initialTheme.colors.chartGrid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: initialTheme.colors.crosshair, labelBackgroundColor: initialTheme.colors.raised },
        horzLine: { color: initialTheme.colors.crosshair, labelBackgroundColor: initialTheme.colors.raised },
      },
      rightPriceScale: { borderColor: initialTheme.colors.border, scaleMargins: calculatePriceScaleMargins(0, 1) },
      timeScale: {
        borderColor: initialTheme.colors.border,
        rightOffset: 3,
        minBarSpacing: 0.08,
        fixLeftEdge: false,
        fixRightEdge: false,
        rightBarStaysOnScroll: false,
        timeVisible: false,
        secondsVisible: false,
      },
      localization: { locale: 'zh-CN' },
    })

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: rising,
      downColor: falling,
      borderUpColor: rising,
      borderDownColor: falling,
      wickUpColor: rising,
      wickDownColor: falling,
      priceLineColor: '#8e99a8',
    })
    const closeLine = chart.addSeries(LineSeries, {
      color: initialTheme.colors.accent,
      lineWidth: 2,
      priceLineVisible: true,
      lastValueVisible: true,
      visible: lineOnly,
      ...compactCrosshairMarkerOptions,
    })
    const settlementLine = chart.addSeries(LineSeries, {
      title: '', color: '#d49a45', lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false,
      visible: settlementVisible && !lineOnly,
      ...compactCrosshairMarkerOptions,
    })
    const ma5 = chart.addSeries(LineSeries, { color: '#e5b85c', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, ...compactCrosshairMarkerOptions })
    const ma20 = chart.addSeries(LineSeries, { color: '#57a7d9', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, ...compactCrosshairMarkerOptions })
    const ma60 = chart.addSeries(LineSeries, { color: '#b984cc', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, ...compactCrosshairMarkerOptions })
    const resetAutoScale = (visibleBars?: number, width?: number, low?: number, high?: number) => {
      const visible = chart.timeScale().getVisibleRange()
      const stats = visibleBars === undefined || low === undefined || high === undefined
        ? visible
          ? visibleBarStats(barsRef.current, String(visible.from), String(visible.to))
          : visibleBarStats(barsRef.current)
        : { count: visibleBars, low, high }
      const resolvedBars = visibleBars ?? stats.count
      const resolvedWidth = width ?? chart.timeScale().width() ?? hostRef.current?.clientWidth ?? 1
      const logicalRange = chart.timeScale().getVisibleLogicalRange()
      const visibleLogicalBars = logicalRange
        ? Math.max(1, logicalRange.to - logicalRange.from)
        : resolvedBars
      const zeroSafeRange = priceModeRef.current === 'normal'
      chart.priceScale('right', 0).applyOptions({
        autoScale: true,
        scaleMargins: calculatePriceScaleMargins(
          resolvedBars,
          resolvedWidth,
          zeroSafeRange ? low ?? stats.low : undefined,
          zeroSafeRange ? high ?? stats.high : undefined,
          visibleLogicalBars,
        ),
      })
      chart.panes().slice(1).forEach((_, index) => {
        chart.priceScale('right', index + 1).applyOptions({ autoScale: true })
      })
    }
    resetAutoScaleRef.current = resetAutoScale

    chart.subscribeCrosshairMove(param => {
      if (!param.time) {
        setReadout(latestReadout(barsRef.current))
        return
      }
      const candle = param.seriesData.get(candles) as CandlestickData<Time> | undefined
      if (!candle || !('open' in candle)) return
      const rendered = renderedBarsRef.current.get(String(param.time))
      const previousClose = rendered
        ? previousCloseByDateRef.current.get(rendered.period_start)
        : undefined
      setReadout({
        ...rendered,
        trade_date: rendered && rendered.period_start !== rendered.trade_date
          ? `${rendered.period_start} → ${rendered.trade_date}`
          : String(param.time),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: rendered?.volume ?? 0,
        source: rendered?.source ?? '',
        changePercent: calculateChangePercent(
          candle.close,
          rendered?.previous_settlement ?? previousClose,
        ),
        ma5: valueAt(ma5, param),
        ma20: valueAt(ma20, param),
        ma60: valueAt(ma60, param),
      })
    })

    let lodFrame = 0
    let visibleRangeTimer = 0
    let edgeSnapTimer = 0
    const recalculateLod = () => {
      if (suppressLodRef.current) return
      window.cancelAnimationFrame(lodFrame)
      lodFrame = window.requestAnimationFrame(() => {
        setOverlayRevision(value => value + 1)
        const visible = chart.timeScale().getVisibleRange()
        if (!visible || !hostRef.current) return
        const stats = visibleBarStats(barsRef.current, String(visible.from), String(visible.to))
        resetAutoScale(stats.count, chart.timeScale().width(), stats.low, stats.high)
        const nextBucket = chooseLodBucket(stats.count, hostRef.current.clientWidth)
        if (nextBucket !== bucketRef.current) {
          applyBucketRef.current(nextBucket, captureViewport(chart, renderedBarListRef.current.length))
        }
      })
      window.clearTimeout(visibleRangeTimer)
      visibleRangeTimer = window.setTimeout(() => {
        const visible = chart.timeScale().getVisibleRange()
        if (visible && !suppressLodRef.current) {
          const value = { from: String(visible.from), to: String(visible.to) }
          emittedVisibleRangeRef.current = value
          visibleRangeCallbackRef.current?.(value)
        }
      }, 180)
      window.clearTimeout(edgeSnapTimer)
      edgeSnapTimer = window.setTimeout(() => {
        if (suppressLodRef.current || timeAxisPointerActiveRef.current) return
        const logical = chart.timeScale().getVisibleLogicalRange()
        const snapped = logical && snapLogicalRangeToDataEdge(
          logical,
          renderedBarListRef.current.length,
          chart.timeScale().options().barSpacing,
        )
        if (snapped) chart.timeScale().setVisibleLogicalRange(snapped)
      }, 240)
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(recalculateLod)
    recalculateLodRef.current = recalculateLod
    let observedHostSize = {
      width: Math.round(hostRef.current.clientWidth),
      height: Math.round(hostRef.current.clientHeight),
    }
    const resizeObserver = new ResizeObserver(entries => {
      const bounds = entries[0]?.contentRect
      if (!bounds) return
      const next = { width: Math.round(bounds.width), height: Math.round(bounds.height) }
      if (next.width === observedHostSize.width && next.height === observedHostSize.height) return
      observedHostSize = next
      recalculateLod()
    })
    resizeObserver.observe(hostRef.current)

    chartRef.current = chart
    candleRef.current = candles
    closeLineRef.current = closeLine
    settlementLineRef.current = settlementLine
    ma5Ref.current = ma5
    ma20Ref.current = ma20
    ma60Ref.current = ma60
    return () => {
      window.cancelAnimationFrame(lodFrame)
      window.clearTimeout(visibleRangeTimer)
      window.clearTimeout(edgeSnapTimer)
      resizeObserver.disconnect()
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(recalculateLod)
      chart.remove()
      chartRef.current = null
      closeLineRef.current = null
      settlementLineRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !volumeVisible || lineOnly) {
      if (chart) setPaneStretchFactors(chart)
      return
    }
    const pane = chart.addPane(true)
    pane.moveTo(1)
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
      lastValueVisible: false,
    }, pane.paneIndex())
    volumePaneRef.current = pane
    volumeRef.current = volume
    const paneObserver = new ResizeObserver(() => {
      setOverlayRevision(value => value + 1)
      emitPaneRatios(chart, pane, macdPaneRef.current, openInterestPaneRef.current, paneRatiosCallbackRef.current)
    })
    const paneElement = pane.getHTMLElement()
    if (paneElement) paneObserver.observe(paneElement)
    applyVolumeSeries(renderedBarListRef.current, previousCloseByDateRef.current, volume)
    setPaneStretchFactors(chart)
    setOverlayRevision(value => value + 1)
    return () => {
      volumeRef.current = null
      volumePaneRef.current = null
      paneObserver.disconnect()
      if (chartRef.current !== chart) return
      chart.removeSeries(volume)
      const paneIndex = chart.panes().indexOf(pane)
      if (paneIndex >= 0) chart.removePane(paneIndex)
      setPaneStretchFactors(chart)
      setOverlayRevision(value => value + 1)
    }
  }, [lineOnly, volumeVisible])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || indicator !== 'macd' || lineOnly) {
      if (chart) setPaneStretchFactors(chart)
      return
    }
    const pane = chart.addPane(true)
    const paneIndex = pane.paneIndex()
    const histogram = chart.addSeries(HistogramSeries, {
      title: '',
      base: 0,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      priceLineVisible: false,
      lastValueVisible: false,
      ...compactCrosshairMarkerOptions,
    }, paneIndex)
    const dif = chart.addSeries(LineSeries, {
      title: '',
      color: '#e5b85c',
      lineWidth: 1,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      priceLineVisible: false,
      lastValueVisible: false,
      ...compactCrosshairMarkerOptions,
    }, paneIndex)
    const dea = chart.addSeries(LineSeries, {
      title: '',
      color: '#57a7d9',
      lineWidth: 1,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      priceLineVisible: false,
      lastValueVisible: false,
      ...compactCrosshairMarkerOptions,
    }, paneIndex)
    macdPaneRef.current = pane
    macdHistogramRef.current = histogram
    macdDifRef.current = dif
    macdDeaRef.current = dea
    const paneObserver = new ResizeObserver(() => {
      setOverlayRevision(value => value + 1)
      emitPaneRatios(chart, volumePaneRef.current, pane, openInterestPaneRef.current, paneRatiosCallbackRef.current)
    })
    const paneElement = pane.getHTMLElement()
    if (paneElement) paneObserver.observe(paneElement)
    setPaneStretchFactors(chart)
    chart.priceScale('right', paneIndex).applyOptions({ autoScale: true, scaleMargins: { top: 0.12, bottom: 0.12 } })
    const times = new Set(renderedBarListRef.current.map(item => item.trade_date))
    applyMacdSeries(macd, times, dif, dea, histogram)
    setOverlayRevision(value => value + 1)
    return () => {
      macdHistogramRef.current = null
      macdPaneRef.current = null
      paneObserver.disconnect()
      macdDifRef.current = null
      macdDeaRef.current = null
      if (chartRef.current !== chart) return
      chart.removeSeries(histogram)
      chart.removeSeries(dif)
      chart.removeSeries(dea)
      const currentPaneIndex = chart.panes().indexOf(pane)
      if (currentPaneIndex >= 0) chart.removePane(currentPaneIndex)
      setPaneStretchFactors(chart)
      setOverlayRevision(value => value + 1)
    }
  }, [indicator, lineOnly])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !openInterestVisible || lineOnly) {
      if (chart) setPaneStretchFactors(chart)
      return
    }
    const pane = chart.addPane(true)
    const series = chart.addSeries(HistogramSeries, {
      title: '', priceFormat: { type: 'volume' },
      priceLineVisible: false, lastValueVisible: false,
    }, pane.paneIndex())
    openInterestPaneRef.current = pane
    openInterestRef.current = series
    const paneObserver = new ResizeObserver(() => {
      setOverlayRevision(value => value + 1)
      emitPaneRatios(chart, volumePaneRef.current, macdPaneRef.current, pane, paneRatiosCallbackRef.current)
    })
    const paneElement = pane.getHTMLElement()
    if (paneElement) paneObserver.observe(paneElement)
    applyOpenInterestSeries(renderedBarListRef.current, series)
    setPaneStretchFactors(chart)
    setOverlayRevision(value => value + 1)
    return () => {
      openInterestRef.current = null
      openInterestPaneRef.current = null
      paneObserver.disconnect()
      if (chartRef.current !== chart) return
      chart.removeSeries(series)
      const paneIndex = chart.panes().indexOf(pane)
      if (paneIndex >= 0) chart.removePane(paneIndex)
      setPaneStretchFactors(chart)
      setOverlayRevision(value => value + 1)
    }
  }, [lineOnly, openInterestVisible])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !paneRatios) return
    applyPaneRatios(chart, paneRatios, volumePaneRef.current, macdPaneRef.current, openInterestPaneRef.current)
  }, [indicator, openInterestVisible, paneRatios, volumeVisible])

  useEffect(() => {
    applyBucketRef.current = (bucket, preserve) => {
      const renderedBars = aggregateBars(bars, bucket)
      const colors = new Map(renderedBars.map(item => [
        item.trade_date,
        candleColor(item, previousCloseByDateRef.current.get(item.period_start)),
      ]))
      const candles: CandlestickData<Time>[] = renderedBars.map(item => {
        const color = colors.get(item.trade_date)!
        return {
          time: item.trade_date,
          open: item.open,
          high: item.high,
          low: item.low,
          close: item.close,
          color,
          borderColor: color,
          wickColor: color,
        }
      })
      const volumes: HistogramData<Time>[] = renderedBars.map(item => ({
        time: item.trade_date,
        value: item.volume,
        color: `${colors.get(item.trade_date)!}99`,
      }))
      const times = new Set(renderedBars.map(item => item.trade_date))
      suppressLodRef.current = true
      candleRef.current?.setData(candles)
      closeLineRef.current?.setData(renderedBars.map(item => ({
        time: item.trade_date,
        value: item.close,
      } satisfies LineData<Time>)))
      settlementLineRef.current?.setData(renderedBars.flatMap(item => item.settlement == null
        ? []
        : [{ time: item.trade_date, value: item.settlement } satisfies LineData<Time>]))
      volumeRef.current?.setData(volumes)
      openInterestRef.current?.setData(renderedBars.flatMap(item => item.open_interest == null
        ? []
        : [{ time: item.trade_date, value: item.open_interest, color: '#4f91b8aa' } satisfies HistogramData<Time>]))
      ma5Ref.current?.setData(averages.ma5.filter(item => times.has(String(item.time))))
      ma20Ref.current?.setData(averages.ma20.filter(item => times.has(String(item.time))))
      ma60Ref.current?.setData(averages.ma60.filter(item => times.has(String(item.time))))
      applyMacdSeries(
        macd,
        times,
        macdDifRef.current,
        macdDeaRef.current,
        macdHistogramRef.current,
      )
      renderedBarsRef.current = new Map(renderedBars.map(item => [item.trade_date, item]))
      renderedBarListRef.current = renderedBars
      bucketRef.current = bucket
      setLodBucket(bucket)
      setOverlayRevision(value => value + 1)
      window.requestAnimationFrame(() => {
        if (preserve) {
          chartRef.current?.timeScale().setVisibleLogicalRange(remapLogicalRange(
            preserve.logical,
            preserve.dataCount,
            renderedBars.length,
          ))
        }
        resetAutoScaleRef.current()
        window.requestAnimationFrame(() => {
          suppressLodRef.current = false
        })
      })
    }
    applyBucketRef.current(1, pendingViewportRef.current)
    pendingViewportRef.current = undefined
  }, [bars, averages, macd])

  useEffect(() => {
    priceModeRef.current = priceMode
    chartRef.current?.priceScale('right', 0).applyOptions({
      autoScale: true,
      mode: priceMode === 'log' ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    })
    resetAutoScaleRef.current()
    window.requestAnimationFrame(() => setOverlayRevision(value => value + 1))
  }, [priceMode])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || bars.length === 0) return
    if (skipRangeResetRef.current) {
      skipRangeResetRef.current = false
      return
    }
    if (initialVisibleRange) {
      const emitted = emittedVisibleRangeRef.current
      if (emitted?.from === initialVisibleRange.from && emitted.to === initialVisibleRange.to) return
      chart.timeScale().setVisibleRange(initialVisibleRange)
      resetAutoScaleRef.current()
      window.requestAnimationFrame(() => recalculateLodRef.current())
      return
    }
    if (range === 'ALL') {
      chart.timeScale().fitContent()
      resetAutoScaleRef.current()
      window.requestAnimationFrame(() => recalculateLodRef.current())
      return
    }
    const last = bars.at(-1)!.trade_date
    const from = range === '1M' ? subtractMonths(last, 1) : subtractYears(last, Number.parseInt(range, 10))
    chart.timeScale().setVisibleRange({ from, to: last })
    resetAutoScaleRef.current()
    window.requestAnimationFrame(() => recalculateLodRef.current())
  }, [bars, range, initialVisibleRange?.from, initialVisibleRange?.to])

  const handleSelectionStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 2 || !chartRef.current) return
    event.preventDefault()
    const point = localPoint(event)
    const paneHeight = chartRef.current.panes()[0]?.getHeight() ?? event.currentTarget.clientHeight
    if (point.y > paneHeight) return
    event.currentTarget.setPointerCapture(event.pointerId)
    selectionDragRef.current = { pointerId: event.pointerId, startX: point.x, startY: point.y }
    setMeasurement(undefined)
    setRangeSelection(undefined)
    setSelectionBox({ left: point.x, top: point.y, width: 0, height: 0 })
  }

  const handleSelectionMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = selectionDragRef.current
    if (!drag || drag.pointerId !== event.pointerId || !chartRef.current) return
    event.preventDefault()
    const point = localPoint(event)
    const paneHeight = chartRef.current.panes()[0]?.getHeight() ?? event.currentTarget.clientHeight
    setSelectionBox(rectangleFromPoints(
      drag.startX,
      drag.startY,
      clamp(point.x, 0, event.currentTarget.clientWidth),
      clamp(point.y, 0, paneHeight),
    ))
  }

  const handleSelectionEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = selectionDragRef.current
    if (!drag || drag.pointerId !== event.pointerId || !chartRef.current) return
    event.preventDefault()
    selectionDragRef.current = undefined
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    const point = localPoint(event)
    const paneHeight = chartRef.current.panes()[0]?.getHeight() ?? event.currentTarget.clientHeight
    const box = rectangleFromPoints(
      drag.startX,
      drag.startY,
      clamp(point.x, 0, event.currentTarget.clientWidth),
      clamp(point.y, 0, paneHeight),
    )
    if (box.width < 8 || box.height < 8) {
      setSelectionBox(undefined)
      return
    }
    const selected = resolveRangeSelection(
      chartRef.current,
      renderedBarListRef.current,
      box,
      point.x,
      point.y,
      event.currentTarget.clientWidth,
      event.currentTarget.clientHeight,
    )
    if (!selected) {
      setSelectionBox(undefined)
      return
    }
    setSelectionBox(box)
    setRangeSelection(selected)
  }

  const handleSelectionCancel = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (selectionDragRef.current?.pointerId !== event.pointerId) return
    selectionDragRef.current = undefined
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    setSelectionBox(undefined)
    setRangeSelection(undefined)
  }

  const resolveDrawingAnchor = (x: number, y: number): TrendLineAnchor | undefined => {
    const chart = chartRef.current
    const candles = candleRef.current
    if (!chart || !candles || renderedBarListRef.current.length === 0) return undefined
    let nearest: RenderBar | undefined
    let nearestX = 0
    let nearestDistance = Number.POSITIVE_INFINITY
    for (const period of renderedBarListRef.current) {
      const periodX = chart.timeScale().timeToCoordinate(period.trade_date)
      if (periodX === null) continue
      const distance = Math.abs(periodX - x)
      if (distance < nearestDistance) {
        nearest = period
        nearestX = periodX
        nearestDistance = distance
      }
    }
    const price = candles.coordinateToPrice(y)
    if (!nearest || price === null || !Number.isFinite(price)) return undefined
    const fallback: TrendLineAnchor = { date: nearest.trade_date, price, snap: 'free' }
    const candidates = barsInRenderPeriod(nearest, barsRef.current).flatMap(bar => {
      const highY = candles.priceToCoordinate(bar.high)
      const lowY = candles.priceToCoordinate(bar.low)
      return [
        ...(highY === null ? [] : [{ date: bar.trade_date, price: bar.high, snap: 'high' as const, x: nearestX, y: highY }]),
        ...(lowY === null ? [] : [{ date: bar.trade_date, price: bar.low, snap: 'low' as const, x: nearestX, y: lowY }]),
      ]
    })
    return chooseAnchor(x, y, fallback, candidates)
  }

  const resolveTradingDateIndexAtX = (x: number): number | undefined => {
    const chart = chartRef.current
    if (!chart || barsRef.current.length === 0 || renderedBarListRef.current.length === 0) return undefined
    const logical = chart.timeScale().coordinateToLogical(x)
    if (logical === null) return undefined
    const renderedIndex = clamp(Math.round(Number(logical)), 0, renderedBarListRef.current.length - 1)
    const nearestDate = renderedBarListRef.current[renderedIndex].trade_date
    const exact = barsRef.current.findIndex(bar => bar.trade_date === nearestDate)
    if (exact >= 0) return exact
    return barsRef.current.reduce((nearest, bar, index) => (
      Math.abs(Date.parse(bar.trade_date) - Date.parse(nearestDate!))
        < Math.abs(Date.parse(barsRef.current[nearest].trade_date) - Date.parse(nearestDate!)) ? index : nearest
    ), 0)
  }

  const handleDrawingStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || drawingTool !== 'trend-line' || !chartRef.current) return
    const point = localPoint(event)
    const paneHeight = chartRef.current.panes()[0]?.getHeight() ?? event.currentTarget.clientHeight
    if (point.y > paneHeight) return
    const anchor = resolveDrawingAnchor(point.x, point.y)
    if (!anchor) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    drawingDragRef.current = { pointerId: event.pointerId, start: anchor, startX: point.x, startY: point.y }
    setSelectedDrawingId(undefined)
    setDrawingDraft([anchor, anchor])
  }

  const handleDrawingMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = drawingDragRef.current
    if (!drag || drag.pointerId !== event.pointerId || !chartRef.current) return
    const point = localPoint(event)
    const paneHeight = chartRef.current.panes()[0]?.getHeight() ?? event.currentTarget.clientHeight
    const anchor = resolveDrawingAnchor(point.x, clamp(point.y, 0, paneHeight))
    if (anchor) setDrawingDraft([drag.start, anchor])
  }

  const handleDrawingEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = drawingDragRef.current
    if (!drag || drag.pointerId !== event.pointerId || !chartRef.current) return
    const rawPoint = localPoint(event)
    const paneHeight = chartRef.current.panes()[0]?.getHeight() ?? event.currentTarget.clientHeight
    const point = {
      x: clamp(rawPoint.x, 0, event.currentTarget.clientWidth),
      y: clamp(rawPoint.y, 0, paneHeight),
    }
    const anchor = resolveDrawingAnchor(point.x, point.y)
    drawingDragRef.current = undefined
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    setDrawingDraft(undefined)
    setDrawingTool('browse')
    if (!anchor || Math.hypot(point.x - drag.startX, point.y - drag.startY) < 6) return
    try {
      const drawing = createTrendLine(drawingTarget, [drag.start, anchor], priceMode)
      saveTrendLine(drawing)
      setSelectedDrawingId(drawing.id)
      logInfo('drawing', '趋势线已保存', { symbol, drawingId: drawing.id })
    } catch (error) {
      logWarning('drawing', '趋势线保存失败', { symbol, error })
    }
  }

  const cancelDrawing = () => {
    drawingDragRef.current = undefined
    setDrawingDraft(undefined)
    setDrawingTool('browse')
  }

  const previewTrendLineMove = (event: ReactPointerEvent<SVGLineElement>): TrendLineMoveDrag | undefined => {
    const drag = lineMoveDragRef.current
    const chart = chartRef.current
    const candles = candleRef.current
    if (!drag || drag.pointerId !== event.pointerId || !chart || !candles) return undefined
    const point = chartPoint(event, hostRef.current)
    const paneHeight = chart.panes()[0]?.getHeight() ?? hostRef.current?.clientHeight ?? 0
    const dateIndex = resolveTradingDateIndexAtX(point.x)
    const currentPrice = candles.coordinateToPrice(clamp(point.y, 0, paneHeight))
    if (dateIndex === undefined || currentPrice === null || !Number.isFinite(currentPrice)) return drag
    const anchors = translateTrendLineAnchors(
      drag.drawing.anchors,
      barsRef.current.map(bar => bar.trade_date),
      dateIndex - drag.startDateIndex,
      drag.startPrice,
      currentPrice,
      priceMode,
    )
    drag.latest = { ...drag.drawing, anchors }
    drag.moved ||= Math.hypot(point.x - drag.startX, point.y - drag.startY) >= 3
    setDrawings(current => current.map(item => item.id === drag.drawing.id ? drag.latest : item))
    return drag
  }

  const handleTrendLineMoveStart = (event: ReactPointerEvent<SVGLineElement>, drawing: TrendLineDrawing) => {
    const chart = chartRef.current
    const candles = candleRef.current
    if (
      event.button !== 0 || drawingTool !== 'move'
      || drawing.id !== selectedDrawingId || !chart || !candles
    ) return
    const point = chartPoint(event, hostRef.current)
    const paneHeight = chart.panes()[0]?.getHeight() ?? hostRef.current?.clientHeight ?? 0
    if (point.y > paneHeight) return
    const startDateIndex = resolveTradingDateIndexAtX(point.x)
    const startPrice = candles.coordinateToPrice(point.y)
    if (startDateIndex === undefined || startPrice === null || !Number.isFinite(startPrice)) return
    event.preventDefault()
    event.stopPropagation()
    event.currentTarget.setPointerCapture(event.pointerId)
    lineMoveDragRef.current = {
      pointerId: event.pointerId,
      drawing,
      startDateIndex,
      startPrice,
      startX: point.x,
      startY: point.y,
      latest: drawing,
      moved: false,
    }
    setSelectedDrawingId(drawing.id)
    setMovingDrawingId(drawing.id)
  }

  const handleTrendLineMove = (event: ReactPointerEvent<SVGLineElement>) => {
    if (lineMoveDragRef.current?.pointerId !== event.pointerId) return
    event.preventDefault()
    event.stopPropagation()
    previewTrendLineMove(event)
  }

  const finishTrendLineMove = (event: ReactPointerEvent<SVGLineElement>, cancelled = false) => {
    const drag = lineMoveDragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    event.preventDefault()
    event.stopPropagation()
    if (!cancelled) previewTrendLineMove(event)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    lineMoveDragRef.current = undefined
    setMovingDrawingId(undefined)
    if (cancelled || !drag.moved) {
      setDrawings(current => current.map(item => item.id === drag.drawing.id ? drag.drawing : item))
      return
    }
    try {
      saveTrendLine({ ...drag.latest, updatedAt: new Date().toISOString() })
      logInfo('drawing', '趋势线位置已更新', { symbol, drawingId: drag.drawing.id })
    } catch (error) {
      setDrawings(current => current.map(item => item.id === drag.drawing.id ? drag.drawing : item))
      logWarning('drawing', '趋势线移动保存失败', { symbol, drawingId: drag.drawing.id, error })
    }
  }

  const previewTrendLineAnchorMove = (event: ReactPointerEvent<SVGCircleElement>): TrendLineAnchorDrag | undefined => {
    const drag = lineAnchorDragRef.current
    const chart = chartRef.current
    const host = hostRef.current
    if (!drag || drag.pointerId !== event.pointerId || !chart || !host) return undefined
    const rawPoint = chartPoint(event, host)
    const paneHeight = chart.panes()[0]?.getHeight() ?? host.clientHeight
    const point = {
      x: clamp(rawPoint.x, 0, host.clientWidth),
      y: clamp(rawPoint.y, 0, paneHeight),
    }
    const anchor = resolveDrawingAnchor(point.x, point.y)
    if (!anchor) return drag
    drag.latest = {
      ...drag.drawing,
      anchors: replaceTrendLineAnchor(drag.drawing.anchors, drag.anchorIndex, anchor),
    }
    drag.moved ||= Math.hypot(point.x - drag.startX, point.y - drag.startY) >= 3
    setDrawings(current => current.map(item => item.id === drag.drawing.id ? drag.latest : item))
    return drag
  }

  const handleTrendLineAnchorMoveStart = (
    event: ReactPointerEvent<SVGCircleElement>,
    drawing: TrendLineDrawing,
    anchorIndex: 0 | 1,
  ) => {
    if (event.button !== 0 || drawingTool !== 'browse' || !chartRef.current || !candleRef.current) return
    const point = chartPoint(event, hostRef.current)
    event.preventDefault()
    event.stopPropagation()
    event.currentTarget.setPointerCapture(event.pointerId)
    lineAnchorDragRef.current = {
      pointerId: event.pointerId,
      drawing,
      anchorIndex,
      startX: point.x,
      startY: point.y,
      latest: drawing,
      moved: false,
    }
    setSelectedDrawingId(drawing.id)
    setEditingAnchor({ drawingId: drawing.id, anchorIndex })
  }

  const handleTrendLineAnchorMove = (event: ReactPointerEvent<SVGCircleElement>) => {
    if (lineAnchorDragRef.current?.pointerId !== event.pointerId) return
    event.preventDefault()
    event.stopPropagation()
    previewTrendLineAnchorMove(event)
  }

  const finishTrendLineAnchorMove = (event: ReactPointerEvent<SVGCircleElement>, cancelled = false) => {
    const drag = lineAnchorDragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    event.preventDefault()
    event.stopPropagation()
    if (!cancelled) previewTrendLineAnchorMove(event)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    lineAnchorDragRef.current = undefined
    setEditingAnchor(undefined)
    if (cancelled || !drag.moved) {
      setDrawings(current => current.map(item => item.id === drag.drawing.id ? drag.drawing : item))
      return
    }
    try {
      saveTrendLine({ ...drag.latest, updatedAt: new Date().toISOString() })
      logInfo('drawing', '趋势线端点已更新', {
        symbol,
        drawingId: drag.drawing.id,
        anchorIndex: drag.anchorIndex,
      })
    } catch (error) {
      setDrawings(current => current.map(item => item.id === drag.drawing.id ? drag.drawing : item))
      logWarning('drawing', '趋势线端点保存失败', {
        symbol,
        drawingId: drag.drawing.id,
        anchorIndex: drag.anchorIndex,
        error,
      })
    }
  }

  const orientSelectedDrawing = (orientation: TrendLineOrientation) => {
    if (!selectedDrawingId) return
    updateDrawing(selectedDrawingId, drawing => ({
      ...drawing,
      anchors: orientTrendLineAnchors(drawing.anchors, orientation),
    }))
  }

  const removeSelectedDrawing = () => {
    if (!selectedDrawingId) return
    removeDrawing(selectedDrawingId)
    setSelectedDrawingId(undefined)
  }

  const showRangeMeasurement = () => {
    if (!rangeSelection) return
    const rollEventCount = renderedBarListRef.current.filter(item => (
      item.trade_date >= rangeSelection.first.trade_date
      && item.trade_date <= rangeSelection.last.trade_date
      && item.roll_event
    )).length
    setMeasurement(createRangeMeasurement(
      rangeSelection.first, rangeSelection.last, rangeSelection.count, rollEventCount,
    ))
    setRangeSelection(undefined)
    setSelectionBox(undefined)
    setOverlayRevision(value => value + 1)
  }

  const zoomToSelection = () => {
    if (!rangeSelection || rangeSelection.count < 2 || !chartRef.current) return
    chartRef.current.timeScale().setVisibleRange({
      from: rangeSelection.first.period_start,
      to: rangeSelection.last.trade_date,
    })
    resetAutoScaleRef.current()
    setRangeSelection(undefined)
    setSelectionBox(undefined)
    window.requestAnimationFrame(() => recalculateLodRef.current())
  }

  const measurementGeometry = projectMeasurement(
    measurement,
    chartRef.current,
    candleRef.current,
    hostRef.current,
  )
  const marketAnnotations = projectMarketAnnotations(
    renderedBarListRef.current,
    priceGaps,
    chartRef.current,
    candleRef.current,
    hostRef.current,
    lodBucket,
  )
  const projectedDrawings = projectTrendLines(
    trendIsolation ? [] : drawings,
    renderedBarListRef.current,
    chartRef.current,
    candleRef.current,
  )
  const projectedDraft = drawingDraft && !trendIsolation
    ? projectTrendLineAnchors(drawingDraft, renderedBarListRef.current, chartRef.current, candleRef.current)
    : undefined
  const volumePaneTop = projectPaneTop(chartRef.current, volumePaneRef.current)
  const macdPaneTop = projectPaneTop(chartRef.current, macdPaneRef.current)
  const openInterestPaneTop = projectPaneTop(chartRef.current, openInterestPaneRef.current)
  const generatedPivots = projectGeneratedPivots(
    displayedTrendAnalysis,
    chartRef.current,
    candleRef.current ?? closeLineRef.current,
    hostRef.current,
    showTentativePivots,
  )
  const generatedTrendLines = projectGeneratedTrendLines(
    displayedTrendAnalysis,
    chartRef.current,
    candleRef.current ?? closeLineRef.current,
    hostRef.current,
    shortTrendLinesVisible,
    longTrendLinesVisible,
    highlightedAnalysisItemId,
  )
  const generatedZones = projectGeneratedZones(
    displayedTrendAnalysis,
    chartRef.current,
    candleRef.current ?? closeLineRef.current,
    hostRef.current,
    keyLevelsVisible,
    volumeZonesVisible,
  )
  const generatedPatterns = projectGeneratedPatterns(
    displayedTrendAnalysis,
    chartRef.current,
    candleRef.current ?? closeLineRef.current,
    patternsVisible,
  )
  const generatedBreakoutState = readGeneratedBreakoutState(
    displayedTrendAnalysis, breakoutStateVisible,
  )
  useEffect(() => {
    onBreakoutStateChange?.(generatedBreakoutState)
  }, [
    generatedBreakoutState?.state,
    generatedBreakoutState?.eventKind,
    generatedBreakoutState?.preview,
    generatedBreakoutState?.boundaryPrice,
    onBreakoutStateChange,
  ])
  useEffect(() => {
    onTrendAnalysisChange?.(trendAnalysis)
  }, [trendAnalysis?.run_id, trendAnalysis?.stale, onTrendAnalysisChange])

  return (
    <div
      className={`${selectionDragRef.current ? 'chart-stage selecting' : 'chart-stage'}${trendIsolation ? ' trend-isolated' : ''}`}
      onPointerDown={handleSelectionStart}
      onPointerDownCapture={event => {
        if (event.button === 0) timeAxisPointerActiveRef.current = true
      }}
      onPointerMove={handleSelectionMove}
      onPointerUp={handleSelectionEnd}
      onPointerUpCapture={event => {
        if (event.button !== 0) return
        timeAxisPointerActiveRef.current = false
        recalculateLodRef.current()
      }}
      onPointerCancel={handleSelectionCancel}
      onPointerCancelCapture={() => {
        timeAxisPointerActiveRef.current = false
        recalculateLodRef.current()
      }}
      onContextMenu={event => event.preventDefault()}
    >
      <div ref={hostRef} className="chart-host"/>
      <div
        className={toolbarCollapsed ? 'chart-drawing-toolbar chart-unified-toolbar collapsed' : 'chart-drawing-toolbar chart-unified-toolbar'}
        data-testid="chart-unified-toolbar"
        onPointerDown={event => event.stopPropagation()}
      >
        <div className="chart-drawing-toolbar-actions" aria-hidden={toolbarCollapsed}>
        <button
          className={manualRefreshing ? 'refreshing' : manualRefreshFeedback?.kind ?? ''}
          title={manualRefreshing
            ? '正在刷新当前标的当日数据'
            : manualRefreshFeedback?.message ?? '刷新当日标的'}
          aria-label="刷新当日标的"
          disabled={manualRefreshing || Boolean(asOfDate)}
          onClick={refreshIntradayNow}
        >{manualRefreshing
          ? <RefreshCw size={13}/>
          : manualRefreshFeedback?.kind === 'success' || manualRefreshFeedback?.kind === 'canonical'
          ? <Check size={13}/>
          : manualRefreshFeedback
            ? <AlertTriangle size={13}/>
            : <RefreshCw size={13}/>}</button>
        <button
          className={drawingTool === 'browse' ? 'active' : ''}
          title="浏览并选择趋势线"
          aria-label="浏览并选择趋势线"
          aria-pressed={drawingTool === 'browse'}
          onClick={() => cancelDrawing()}
        ><MousePointer2 size={13}/></button>
        <button
          className={drawingTool === 'trend-line' ? 'active' : ''}
          title="绘制趋势线"
          aria-label="绘制趋势线"
          aria-pressed={drawingTool === 'trend-line'}
          disabled={!drawingTargetKey}
          onClick={() => {
            setDrawingTool('trend-line')
            setSelectedDrawingId(undefined)
          }}
        ><PencilLine size={13}/></button>
        <button
          className={drawingTool === 'move' ? 'active' : ''}
          title="平移选中的趋势线并保持角度"
          aria-label="平移选中的趋势线并保持角度"
          aria-pressed={drawingTool === 'move'}
          disabled={!selectedDrawingId}
          onClick={() => setDrawingTool(value => value === 'move' ? 'browse' : 'move')}
        ><Move size={13}/></button>
        <button
          title="将选中趋势线设为水平"
          aria-label="将选中趋势线设为水平"
          disabled={!selectedDrawingId}
          onClick={() => orientSelectedDrawing('horizontal')}
        ><MoveHorizontal size={13}/></button>
        <button
          title="将选中趋势线设为垂直"
          aria-label="将选中趋势线设为垂直"
          disabled={!selectedDrawingId}
          onClick={() => orientSelectedDrawing('vertical')}
        ><MoveVertical size={13}/></button>
        <button
          className={drawingManagerOpen ? 'active' : ''}
          title="趋势线管理"
          aria-label="趋势线管理"
          aria-pressed={drawingManagerOpen}
          onClick={() => setDrawingManagerOpen(value => !value)}
        ><Settings2 size={13}/></button>
        <button title="删除选中的趋势线" aria-label="删除选中的趋势线" disabled={!selectedDrawingId} onClick={removeSelectedDrawing}><Trash2 size={13}/></button>
        {drawingTool === 'trend-line' && <button title="取消画线" aria-label="取消画线" onClick={cancelDrawing}><X size={13}/></button>}
        </div>
        {toolbarContent && <>
          <span className="chart-toolbar-divider" aria-hidden="true"/>
          <div className="chart-toolbar-content" aria-hidden={toolbarCollapsed}>{toolbarContent}</div>
        </>}
        <button
          className="chart-toolbar-toggle"
          title={toolbarCollapsed ? '展开图表工具栏' : '最小化图表工具栏'}
          aria-label={toolbarCollapsed ? '展开图表工具栏' : '最小化图表工具栏'}
          aria-expanded={!toolbarCollapsed}
          onClick={() => {
            setToolbarCollapsed(value => {
              if (!value) setDrawingManagerOpen(false)
              const next = !value
              onToolbarCollapsedChange?.(next)
              return next
            })
          }}
        >{toolbarCollapsed ? <ChevronLeft size={13}/> : <ChevronRight size={13}/>}</button>
      </div>
      {drawingManagerOpen && (
        <TrendLineManager
          drawings={drawings}
          migrationCandidates={drawingMigrationCandidates}
          selectedId={selectedDrawingId}
          onSelect={setSelectedDrawingId}
          onDashChange={(id, dash) => updateDrawingStyle(id, { dash })}
          onColorChange={(id, color) => updateDrawingStyle(id, { color })}
          onVisibilityChange={toggleDrawingVisibility}
          onResolveMigration={resolveMigration}
          onClose={() => setDrawingManagerOpen(false)}
        />
      )}
      <TrendLineOverlay
        lines={projectedDrawings}
        draft={projectedDraft}
        selectedId={selectedDrawingId}
        movingId={movingDrawingId}
        editingAnchor={editingAnchor}
        onSelect={setSelectedDrawingId}
        onMoveStart={handleTrendLineMoveStart}
        onMove={handleTrendLineMove}
        onMoveEnd={event => finishTrendLineMove(event)}
        onMoveCancel={event => finishTrendLineMove(event, true)}
        onAnchorMoveStart={handleTrendLineAnchorMoveStart}
        onAnchorMove={handleTrendLineAnchorMove}
        onAnchorMoveEnd={event => finishTrendLineAnchorMove(event)}
        onAnchorMoveCancel={event => finishTrendLineAnchorMove(event, true)}
      />
      {trendAnalysisEnabled && displayedTrendAnalysis && (
        <GeneratedAnalysisOverlay
          pivots={generatedPivots}
          lines={generatedTrendLines}
          zones={generatedZones}
          patterns={generatedPatterns}
          breakoutState={generatedBreakoutState}
          run={displayedTrendAnalysis}
          preview={trendAnalysisPreview}
          highlightedItemId={highlightedAnalysisItemId}
        />
      )}
      {drawingTool === 'trend-line' && (
        <div
          className="chart-drawing-input"
          onPointerDown={handleDrawingStart}
          onPointerMove={handleDrawingMove}
          onPointerUp={handleDrawingEnd}
          onPointerCancel={cancelDrawing}
          onContextMenu={event => event.preventDefault()}
        />
      )}
      {readout && <ChartReadout
        value={readout}
        instrumentName={instrumentName}
        futures={instrumentKind === 'futures-contract' || instrumentKind === 'futures-continuous'}
      />}
      {volumePaneTop !== undefined && <PaneHeader kind="volume" top={volumePaneTop} onHide={() => onVolumeVisibleChange?.(false)}/>}
      {macdPaneTop !== undefined && <PaneHeader kind="macd" top={macdPaneTop} onHide={() => onIndicatorChange?.('none')}/>}
      {openInterestPaneTop !== undefined && <PaneHeader kind="open-interest" top={openInterestPaneTop} onHide={() => onOpenInterestVisibleChange?.(false)}/>}
      {selectionBox && <div className="chart-range-selection" style={selectionBox}/>}
      {rangeSelection && (
        <div
          className="chart-range-menu"
          style={{ left: rangeSelection.menuLeft, top: rangeSelection.menuTop }}
          onPointerDown={event => event.stopPropagation()}
        >
          <button onClick={showRangeMeasurement}><Percent size={14}/>展示区域涨跌幅</button>
          <button disabled={rangeSelection.count < 2} onClick={zoomToSelection}><ZoomIn size={14}/>缩放区域</button>
          <button className="chart-range-menu-close" title="关闭" aria-label="关闭区域菜单" onClick={() => {
            setRangeSelection(undefined)
            setSelectionBox(undefined)
          }}><X size={13}/></button>
        </div>
      )}
      {!trendIsolation && measurement && measurementGeometry && (
        <MeasurementOverlay measurement={measurement} geometry={measurementGeometry}/>
      )}
      {!trendIsolation && marketAnnotations && <MarketAnnotationOverlay geometry={marketAnnotations}/>}
      {state === 'loading' && <div className="chart-state">加载日线数据</div>}
      {state === 'error' && <div className="chart-state error">日线数据加载失败</div>}
      {state === 'ready' && bars.length === 0 && <div className="chart-state">暂无日线数据</div>}
    </div>
  )
}
