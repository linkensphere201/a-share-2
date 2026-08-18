import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Eye, EyeOff, MousePointer2, Move, MoveHorizontal, MoveVertical, PencilLine, Percent, RefreshCw, Settings2, Trash2, X, ZoomIn } from 'lucide-react'
import { logInfo, logWarning } from './eventLogger'
import {
  createTrendLine,
  deleteTrendLine,
  loadSymbolDrawings,
  saveTrendLine,
  subscribeSymbolDrawings,
  type TrendLineAnchor,
  type TrendLineDash,
  type TrendLineDrawing,
} from './drawingStore'
import { barsInRenderPeriod, chooseAnchor, extendLineToBounds, orientTrendLineAnchors, replaceTrendLineAnchor, translateTrendLineAnchors, type LineGeometry, type TrendLineOrientation } from './trendLines'
import type { ThemeDefinition } from './themeStore'
import { loadTrendAnalysis, type TrendAnalysisRun } from './trendAnalysisClient'
import { refreshLatestDailyBar } from './latestDailyRefreshClient'
import {
  projectMarketAnnotations,
  projectMeasurement,
  projectPaneTop,
  projectTrendLineAnchors,
  projectTrendLines,
  type MarketAnnotationGeometry,
  type MeasurementGeometry,
  type ProjectedTrendLine,
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
  mergeProvisionalBar,
  millisecondsUntilMarketSession,
  millisecondsUntilNextMarketDay,
  movingAverage,
  previousCloseByDate,
  remapLogicalRange,
  snapLogicalRangeToDataEdge,
  subtractMonths,
  subtractYears,
  visibleBarStats,
  type DailyBar,
  type MacdPoint,
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
  type IRange,
  type ISeriesApi,
  type LineData,
  type Time,
} from 'lightweight-charts'

export type ChartRange = '1M' | '1Y' | '3Y' | '10Y' | 'ALL'
export type PriceMode = 'normal' | 'log'
export type ChartIndicator = 'macd' | 'none'
export type VisibleRange = { from: string; to: string }
export type { DailyBar } from './chartData'
type SelectionBox = { left: number; top: number; width: number; height: number }
type RangeSelection = {
  first: RenderBar
  last: RenderBar
  count: number
  box: SelectionBox
  menuLeft: number
  menuTop: number
}

type DrawingDrag = {
  pointerId: number
  start: TrendLineAnchor
  startX: number
  startY: number
}

type ViewportSnapshot = {
  logical: IRange<number>
  dataCount: number
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
  lineOnly?: boolean
  theme: ThemeDefinition
  range: ChartRange
  priceMode: PriceMode
  volumeVisible: boolean
  indicator: ChartIndicator
  initialVisibleRange?: VisibleRange
  onCoverageChange?: (bars: number, first?: string, last?: string) => void
  onVisibleRangeChange?: (value: VisibleRange) => void
  onVolumeVisibleChange?: (visible: boolean) => void
  onIndicatorChange?: (indicator: ChartIndicator) => void
  trendAnalysisEnabled?: boolean
  showTentativePivots?: boolean
  shortTrendLinesVisible?: boolean
  longTrendLinesVisible?: boolean
  keyLevelsVisible?: boolean
  volumeZonesVisible?: boolean
  patternsVisible?: boolean
  breakoutStateVisible?: boolean
  onBreakoutStateChange?: (value: GeneratedBreakoutState | undefined) => void
}

const rising = '#ef5350'
const falling = '#26a269'
const risingSoft = '#e99693'
const fallingSoft = '#70be9a'
const trendLineColors = [
  '#f0b85a', '#ef5350', '#ff7a45', '#e85d91',
  '#26a269', '#8ac926', '#26b5a8', '#49c6e5',
  '#57a7d9', '#4776e6', '#7b68ee', '#b984cc',
  '#d8dde6', '#9aa6b4', '#f4d35e', '#00c2a8',
] as const
const trendLineDashOptions: Array<{ value: TrendLineDash; label: string }> = [
  { value: 'solid', label: '实线' },
  { value: 'dotted', label: '点线' },
  { value: 'dashed', label: '短虚线' },
  { value: 'long-dashed', label: '长虚线' },
  { value: 'dash-dot', label: '点划线' },
]

export const chartLayoutOptions = {
  background: { type: ColorType.Solid, color: '#0d1014' },
  textColor: '#7f8997',
  panes: { separatorColor: '#303743', separatorHoverColor: '#4b84c6', enableResize: true },
  attributionLogo: false,
} as const

export const compactCrosshairMarkerOptions = {
  crosshairMarkerRadius: 2,
  crosshairMarkerBorderWidth: 1,
} as const

export function ChartCanvas({
  symbol,
  lineOnly = false,
  theme,
  range,
  priceMode,
  volumeVisible,
  indicator,
  initialVisibleRange,
  onCoverageChange,
  onVisibleRangeChange,
  onVolumeVisibleChange,
  onIndicatorChange,
  trendAnalysisEnabled = false,
  showTentativePivots = true,
  shortTrendLinesVisible = true,
  longTrendLinesVisible = true,
  keyLevelsVisible = true,
  volumeZonesVisible = true,
  patternsVisible = true,
  breakoutStateVisible = true,
  onBreakoutStateChange,
}: ChartCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const closeLineRef = useRef<ISeriesApi<'Line'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const volumePaneRef = useRef<IPaneApi<Time> | null>(null)
  const ma5Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ma20Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ma60Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const macdDifRef = useRef<ISeriesApi<'Line'> | null>(null)
  const macdDeaRef = useRef<ISeriesApi<'Line'> | null>(null)
  const macdHistogramRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const macdPaneRef = useRef<IPaneApi<Time> | null>(null)
  const barsRef = useRef<DailyBar[]>([])
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
  const emittedVisibleRangeRef = useRef<VisibleRange | undefined>(undefined)
  const priceModeRef = useRef(priceMode)
  const pendingViewportRef = useRef<ViewportSnapshot | undefined>(undefined)
  const skipRangeResetRef = useRef(false)
  const [bars, setBars] = useState<DailyBar[]>([])
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [readout, setReadout] = useState<Readout | null>(null)
  const [lodBucket, setLodBucket] = useState(1)
  const [selectionBox, setSelectionBox] = useState<SelectionBox>()
  const [rangeSelection, setRangeSelection] = useState<RangeSelection>()
  const [measurement, setMeasurement] = useState<RangeMeasurement>()
  const [drawingTool, setDrawingTool] = useState<'browse' | 'trend-line' | 'move'>('browse')
  const [drawings, setDrawings] = useState<TrendLineDrawing[]>(() => loadSymbolDrawings(symbol))
  const [drawingDraft, setDrawingDraft] = useState<[TrendLineAnchor, TrendLineAnchor]>()
  const [selectedDrawingId, setSelectedDrawingId] = useState<string>()
  const [drawingManagerOpen, setDrawingManagerOpen] = useState(false)
  const [movingDrawingId, setMovingDrawingId] = useState<string>()
  const [editingAnchor, setEditingAnchor] = useState<{ drawingId: string; anchorIndex: 0 | 1 }>()
  const [toolbarCollapsed, setToolbarCollapsed] = useState(false)
  const [overlayRevision, setOverlayRevision] = useState(0)
  const liveFailureCountRef = useRef(0)
  const initialTheme = useRef(theme).current
  const manualRefreshControllerRef = useRef<AbortController | undefined>(undefined)
  const manualRefreshFeedbackTimerRef = useRef(0)
  const manualRefreshWarningAtRef = useRef(0)
  const [manualRefreshing, setManualRefreshing] = useState(false)
  const [manualRefreshFeedback, setManualRefreshFeedback] = useState<'success' | 'warning'>()
  const [trendAnalysis, setTrendAnalysis] = useState<TrendAnalysisRun | null>(null)
  const [trendAnalysisPreview, setTrendAnalysisPreview] = useState(false)

  const averages = useMemo(() => ({
    ma5: movingAverage(bars, 5),
    ma20: movingAverage(bars, 20),
    ma60: movingAverage(bars, 60),
  }), [bars])
  const priceGaps = useMemo(() => detectPriceGaps(bars), [bars])
  const macd = useMemo(() => calculateMacd(bars), [bars])

  useEffect(() => { coverageCallbackRef.current = onCoverageChange }, [onCoverageChange])
  useEffect(() => { visibleRangeCallbackRef.current = onVisibleRangeChange }, [onVisibleRangeChange])

  useEffect(() => {
    if (!trendAnalysisEnabled) {
      setTrendAnalysis(null)
      setTrendAnalysisPreview(false)
      return
    }
    let controller: AbortController | undefined
    const reload = () => {
      controller?.abort()
      controller = new AbortController()
      void loadTrendAnalysis(symbol, 'daily', controller.signal).then(snapshot => {
        setTrendAnalysis(snapshot.effective)
        setTrendAnalysisPreview(
          snapshot.effective !== null && snapshot.effective.run_id === snapshot.preview?.run_id
        )
        setOverlayRevision(value => value + 1)
      }).catch(error => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        logWarning('trading-system', '趋势分析结果读取失败', {
          symbol, error: error instanceof Error ? error.message : String(error),
        })
      })
    }
    const onUpdated = (event: Event) => {
      const detail = (event as CustomEvent<{ symbol?: string }>).detail
      if (detail?.symbol?.toUpperCase() === symbol.toUpperCase()) reload()
    }
    reload()
    window.addEventListener('stock-harness:trend-analysis-updated', onUpdated)
    return () => {
      controller?.abort()
      window.removeEventListener('stock-harness:trend-analysis-updated', onUpdated)
    }
  }, [symbol, trendAnalysisEnabled])

  useEffect(() => {
    const reload = () => setDrawings(loadSymbolDrawings(symbol))
    reload()
    setSelectedDrawingId(undefined)
    setDrawingDraft(undefined)
    drawingDragRef.current = undefined
    lineMoveDragRef.current = undefined
    lineAnchorDragRef.current = undefined
    setMovingDrawingId(undefined)
    setEditingAnchor(undefined)
    setDrawingTool('browse')
    setDrawingManagerOpen(false)
    return subscribeSymbolDrawings(symbol, reload)
  }, [symbol])

  const replaceBars = useCallback((next: DailyBar[], preserveView = false) => {
    if (preserveView) {
      pendingViewportRef.current = captureViewport(chartRef.current, renderedBarListRef.current.length)
      skipRangeResetRef.current = true
    }
    barsRef.current = next
    previousCloseByDateRef.current = previousCloseByDate(next)
    setBars(next)
    setReadout(latestReadout(next))
  }, [])

  useEffect(() => {
    chartRef.current?.applyOptions({
      layout: {
        ...chartLayoutOptions,
        background: { type: ColorType.Solid, color: theme.colors.chartBackground },
        textColor: theme.colors.text,
        panes: { separatorColor: theme.colors.border, separatorHoverColor: theme.colors.accent },
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
  }, [theme])

  useEffect(() => {
    candleRef.current?.applyOptions({ visible: !lineOnly })
    closeLineRef.current?.applyOptions({ visible: lineOnly })
  }, [lineOnly])

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
          separatorColor: initialTheme.colors.border,
          separatorHoverColor: initialTheme.colors.accent,
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
      const zeroSafeRange = priceModeRef.current === 'normal'
      chart.priceScale('right', 0).applyOptions({
        autoScale: true,
        scaleMargins: calculatePriceScaleMargins(
          resolvedBars,
          resolvedWidth,
          zeroSafeRange ? low ?? stats.low : undefined,
          zeroSafeRange ? high ?? stats.high : undefined,
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
        trade_date: rendered && rendered.period_start !== rendered.trade_date
          ? `${rendered.period_start} → ${rendered.trade_date}`
          : String(param.time),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: rendered?.volume ?? 0,
        source: rendered?.source ?? '',
        changePercent: calculateChangePercent(candle.close, previousClose),
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
    const resizeObserver = new ResizeObserver(recalculateLod)
    resizeObserver.observe(hostRef.current)

    chartRef.current = chart
    candleRef.current = candles
    closeLineRef.current = closeLine
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
    const paneObserver = new ResizeObserver(() => setOverlayRevision(value => value + 1))
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
    const paneObserver = new ResizeObserver(() => setOverlayRevision(value => value + 1))
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
    const controller = new AbortController()
    selectionDragRef.current = undefined
    setSelectionBox(undefined)
    setRangeSelection(undefined)
    setMeasurement(undefined)
    setState('loading')
    fetch(`/api/instruments/${encodeURIComponent(symbol)}/daily-bars`, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<{ items: DailyBar[] }>
      })
      .then(body => {
        replaceBars(body.items)
        setState('ready')
        const finalItems = body.items.filter(item => item.bar_state !== 'intraday')
        coverageCallbackRef.current?.(
          finalItems.length,
          finalItems.at(0)?.trade_date,
          finalItems.at(-1)?.trade_date,
        )
        logInfo('chart', '日线数据加载完成', { symbol, rows: finalItems.length })
      })
      .catch(error => {
        if ((error as Error).name !== 'AbortError') {
          setState('error')
          logWarning('chart', '日线数据加载失败', { symbol, error })
        }
      })
    return () => controller.abort()
  }, [symbol, replaceBars])

  useEffect(() => () => {
    manualRefreshControllerRef.current?.abort()
    window.clearTimeout(manualRefreshFeedbackTimerRef.current)
  }, [symbol])

  useEffect(() => {
    const onRefreshed = (event: Event) => {
      const detail = (event as CustomEvent<{
        symbol?: string
        mode?: 'provisional' | 'canonical' | 'final'
        items?: DailyBar[]
      }>).detail
      if (detail?.symbol?.toUpperCase() !== symbol.toUpperCase() || !detail.items) return
      if (detail.mode === 'provisional') {
        const live = detail.items[0]
        if (live) replaceBars(mergeProvisionalBar(barsRef.current, live), true)
        return
      }
      replaceBars(detail.items, true)
      const finalItems = detail.items.filter(item => item.bar_state !== 'intraday')
      coverageCallbackRef.current?.(
        finalItems.length,
        finalItems.at(0)?.trade_date,
        finalItems.at(-1)?.trade_date,
      )
    }
    window.addEventListener('stock-harness:latest-daily-refreshed', onRefreshed)
    return () => window.removeEventListener('stock-harness:latest-daily-refreshed', onRefreshed)
  }, [symbol, replaceBars])

  const showManualRefreshFeedback = useCallback((value: 'success' | 'warning') => {
    window.clearTimeout(manualRefreshFeedbackTimerRef.current)
    setManualRefreshFeedback(value)
    manualRefreshFeedbackTimerRef.current = window.setTimeout(
      () => setManualRefreshFeedback(undefined),
      3_000,
    )
  }, [])

  const refreshIntradayNow = useCallback(() => {
    manualRefreshControllerRef.current?.abort()
    const controller = new AbortController()
    manualRefreshControllerRef.current = controller
    window.clearTimeout(manualRefreshFeedbackTimerRef.current)
    setManualRefreshFeedback(undefined)
    setManualRefreshing(true)
    refreshLatestDailyBar(symbol, new Date(), controller.signal)
      .then(result => {
        if (result.mode === 'provisional') {
          const live = result.items[0]
          if (live) replaceBars(mergeProvisionalBar(barsRef.current, live), true)
        } else {
          replaceBars(result.items, true)
          const finalItems = result.items.filter(item => item.bar_state !== 'intraday')
          coverageCallbackRef.current?.(
            finalItems.length,
            finalItems.at(0)?.trade_date,
            finalItems.at(-1)?.trade_date,
          )
        }
        showManualRefreshFeedback(result.warning ? 'warning' : 'success')
        const details = {
          symbol, mode: result.mode, state: result.status,
          rowsChanged: result.rowsChanged, error: result.error,
        }
        if (result.warning) {
          logWarning('daily-refresh', '最新日线刷新完成但存在警告，保留可用图表数据', details)
        } else {
          logInfo('daily-refresh', '最新日线刷新完成', details)
        }
      })
      .catch(error => {
        if ((error as Error).name !== 'AbortError') {
          showManualRefreshFeedback('warning')
          const now = Date.now()
          if (now - manualRefreshWarningAtRef.current >= 60_000) {
            manualRefreshWarningAtRef.current = now
            logWarning('daily-refresh', '最新日线刷新失败，保留现有图表', { symbol, error })
          }
        }
      })
      .finally(() => {
        if (manualRefreshControllerRef.current === controller) {
          manualRefreshControllerRef.current = undefined
          setManualRefreshing(false)
        }
      })
  }, [symbol, replaceBars, showManualRefreshFeedback])

  useEffect(() => {
    let stopped = false
    let timer = 0
    let controller: AbortController | undefined
    const schedule = (delay: number) => {
      timer = window.setTimeout(refresh, delay)
    }
    const refresh = () => {
      if (stopped) return
      const delayUntilSession = millisecondsUntilMarketSession(new Date())
      if (delayUntilSession > 0) {
        schedule(delayUntilSession)
        return
      }
      const params = new URLSearchParams({ symbol })
      controller = new AbortController()
      fetch(`/api/intraday-bars?${params}`, { signal: controller.signal })
        .then(response => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`)
          return response.json() as Promise<{
            items: DailyBar[]
            status: { state: string }
          }>
        })
        .then(body => {
          if (stopped) return
          const live = body.items[0]
          if (live) {
            const next = mergeProvisionalBar(barsRef.current, live)
            if (next !== barsRef.current) replaceBars(next, true)
          }
          if (liveFailureCountRef.current > 0) {
            logInfo('intraday', '盘中行情读取恢复', { symbol })
            liveFailureCountRef.current = 0
          }
          schedule(body.status.state === 'market_closed'
            ? millisecondsUntilNextMarketDay(new Date())
            : 30_000)
        })
        .catch(error => {
          if (stopped) return
          liveFailureCountRef.current += 1
          if (liveFailureCountRef.current === 1 || liveFailureCountRef.current % 10 === 0) {
            logWarning('intraday', '读取盘中临时日K失败，保留现有图表', { symbol, error })
          }
          schedule(30_000)
        })
    }
    schedule(0)
    return () => {
      stopped = true
      window.clearTimeout(timer)
      controller?.abort()
    }
  }, [symbol, replaceBars])

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
      volumeRef.current?.setData(volumes)
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
      const drawing = createTrendLine(symbol, [drag.start, anchor], priceMode)
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

  const updateDrawing = (id: string, update: (drawing: TrendLineDrawing) => TrendLineDrawing) => {
    const drawing = drawings.find(item => item.id === id)
    if (!drawing) return
    try {
      saveTrendLine({ ...update(drawing), updatedAt: new Date().toISOString() })
    } catch (error) {
      logWarning('drawing', '趋势线设置保存失败', { symbol, drawingId: id, error })
    }
  }

  const updateDrawingStyle = (id: string, style: Partial<TrendLineDrawing['style']>) => {
    updateDrawing(id, drawing => ({ ...drawing, style: { ...drawing.style, ...style } }))
  }

  const toggleDrawingVisibility = (id: string) => {
    updateDrawing(id, drawing => ({ ...drawing, visible: !drawing.visible }))
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
    deleteTrendLine(symbol, selectedDrawingId)
    logInfo('drawing', '趋势线已删除', { symbol, drawingId: selectedDrawingId })
    setSelectedDrawingId(undefined)
  }

  const showRangeMeasurement = () => {
    if (!rangeSelection) return
    setMeasurement(createRangeMeasurement(rangeSelection.first, rangeSelection.last, rangeSelection.count))
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
    drawings,
    renderedBarListRef.current,
    chartRef.current,
    candleRef.current,
  )
  const projectedDraft = drawingDraft
    ? projectTrendLineAnchors(drawingDraft, renderedBarListRef.current, chartRef.current, candleRef.current)
    : undefined
  const volumePaneTop = projectPaneTop(chartRef.current, volumePaneRef.current)
  const macdPaneTop = projectPaneTop(chartRef.current, macdPaneRef.current)
  const generatedPivots = projectGeneratedPivots(
    trendAnalysis,
    chartRef.current,
    candleRef.current ?? closeLineRef.current,
    hostRef.current,
    showTentativePivots,
  )
  const generatedTrendLines = projectGeneratedTrendLines(
    trendAnalysis,
    chartRef.current,
    candleRef.current ?? closeLineRef.current,
    hostRef.current,
    shortTrendLinesVisible,
    longTrendLinesVisible,
  )
  const generatedZones = projectGeneratedZones(
    trendAnalysis,
    chartRef.current,
    candleRef.current ?? closeLineRef.current,
    hostRef.current,
    keyLevelsVisible,
    volumeZonesVisible,
  )
  const generatedPatterns = projectGeneratedPatterns(
    trendAnalysis,
    chartRef.current,
    candleRef.current ?? closeLineRef.current,
    patternsVisible,
  )
  const generatedBreakoutState = readGeneratedBreakoutState(
    trendAnalysis, breakoutStateVisible,
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

  return (
    <div
      className={selectionDragRef.current ? 'chart-stage selecting' : 'chart-stage'}
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
      <div className={toolbarCollapsed ? 'chart-drawing-toolbar collapsed' : 'chart-drawing-toolbar'} onPointerDown={event => event.stopPropagation()}>
        <div className="chart-drawing-toolbar-actions" aria-hidden={toolbarCollapsed}>
        <button
          className={manualRefreshing ? 'refreshing' : manualRefreshFeedback ?? ''}
          title={manualRefreshing
            ? '正在刷新当前标的当日数据'
            : manualRefreshFeedback === 'success'
              ? '当日数据刷新完成'
              : manualRefreshFeedback === 'warning'
                ? '未获得新当日数据，已保留现有数据'
                : '刷新当日标的'}
          aria-label="刷新当日标的"
          disabled={manualRefreshing}
          onClick={refreshIntradayNow}
        >{manualRefreshing
          ? <RefreshCw size={13}/>
          : manualRefreshFeedback === 'success'
          ? <Check size={13}/>
          : manualRefreshFeedback === 'warning'
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
        <button
          className="chart-toolbar-toggle"
          title={toolbarCollapsed ? '展开图表工具栏' : '最小化图表工具栏'}
          aria-label={toolbarCollapsed ? '展开图表工具栏' : '最小化图表工具栏'}
          aria-expanded={!toolbarCollapsed}
          onClick={() => {
            setToolbarCollapsed(value => {
              if (!value) setDrawingManagerOpen(false)
              return !value
            })
          }}
        >{toolbarCollapsed ? <ChevronLeft size={13}/> : <ChevronRight size={13}/>}</button>
      </div>
      {drawingManagerOpen && (
        <TrendLineManager
          drawings={drawings}
          selectedId={selectedDrawingId}
          onSelect={setSelectedDrawingId}
          onDashChange={(id, dash) => updateDrawingStyle(id, { dash })}
          onColorChange={(id, color) => updateDrawingStyle(id, { color })}
          onVisibilityChange={toggleDrawingVisibility}
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
      {trendAnalysisEnabled && trendAnalysis && (
        <GeneratedAnalysisOverlay
          pivots={generatedPivots}
          lines={generatedTrendLines}
          zones={generatedZones}
          patterns={generatedPatterns}
          breakoutState={generatedBreakoutState}
          run={trendAnalysis}
          preview={trendAnalysisPreview}
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
      {readout && <ChartReadout value={readout}/>}
      {volumePaneTop !== undefined && <PaneHeader kind="volume" top={volumePaneTop} onHide={() => onVolumeVisibleChange?.(false)}/>}
      {macdPaneTop !== undefined && <PaneHeader kind="macd" top={macdPaneTop} onHide={() => onIndicatorChange?.('none')}/>}
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
      {measurement && measurementGeometry && (
        <MeasurementOverlay measurement={measurement} geometry={measurementGeometry}/>
      )}
      {marketAnnotations && <MarketAnnotationOverlay geometry={marketAnnotations}/>}
      {state === 'loading' && <div className="chart-state">加载日线数据</div>}
      {state === 'error' && <div className="chart-state error">日线数据加载失败</div>}
      {state === 'ready' && bars.length === 0 && <div className="chart-state">暂无日线数据</div>}
    </div>
  )
}

function PaneHeader({ kind, top, onHide }: { kind: 'volume' | 'macd'; top: number; onHide: () => void }) {
  const label = kind === 'volume' ? '成交量' : 'MACD'
  return (
    <div className={`chart-pane-header chart-pane-header-${kind}`} style={{ top: top + 2 }} onPointerDown={event => event.stopPropagation()}>
      {kind === 'volume'
        ? <span className="chart-pane-title">VOL</span>
        : <div className="macd-legend" aria-label="MACD 图例">
            <span>MACD</span><i className="macd-dif"/>DIF<i className="macd-dea"/>DEA<i className="macd-bars"/>柱
          </div>}
      <button title={`隐藏${label}栏`} aria-label={`隐藏${label}栏`} onClick={onHide}><EyeOff size={11}/></button>
    </div>
  )
}

function TrendLineManager({
  drawings,
  selectedId,
  onSelect,
  onDashChange,
  onColorChange,
  onVisibilityChange,
  onClose,
}: {
  drawings: TrendLineDrawing[]
  selectedId?: string
  onSelect: (id: string) => void
  onDashChange: (id: string, dash: TrendLineDash) => void
  onColorChange: (id: string, color: string) => void
  onVisibilityChange: (id: string) => void
  onClose: () => void
}) {
  const selected = drawings.find(drawing => drawing.id === selectedId)
  return (
    <div className="trend-line-manager" onPointerDown={event => event.stopPropagation()}>
      <header><strong>趋势线</strong><span>{drawings.length}</span><button title="关闭趋势线管理" aria-label="关闭趋势线管理" onClick={onClose}><X size={12}/></button></header>
      <div className="trend-line-list">
        {drawings.length === 0 && <span className="trend-line-empty">暂无趋势线</span>}
        {drawings.map((drawing, index) => (
          <div key={drawing.id} className={drawing.id === selectedId ? 'trend-line-item selected' : 'trend-line-item'}>
            <button className="trend-line-select" onClick={() => onSelect(drawing.id)}>
              <i style={{ background: drawing.style.color }}/>
              <span>趋势线 {index + 1}<small>{drawing.anchors[0].date} - {drawing.anchors[1].date}</small></span>
            </button>
            <button
              className="trend-line-visibility"
              title={drawing.visible ? '隐藏趋势线' : '显示趋势线'}
              aria-label={`${drawing.visible ? '隐藏' : '显示'}趋势线 ${index + 1}`}
              onClick={() => onVisibilityChange(drawing.id)}
            >{drawing.visible ? <Eye size={13}/> : <EyeOff size={13}/>}</button>
          </div>
        ))}
      </div>
      {selected && (
        <div className="trend-line-settings">
          <label>线型
            <select aria-label="趋势线线型" value={selected.style.dash} onChange={event => onDashChange(selected.id, event.target.value as TrendLineDash)}>
              {trendLineDashOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <div className="trend-line-colors" aria-label="趋势线颜色">
            {trendLineColors.map(color => (
              <button
                key={color}
                className={selected.style.color === color ? 'active' : ''}
                title={`使用颜色 ${color}`}
                aria-label={`趋势线颜色 ${color}`}
                style={{ '--trend-color': color } as CSSProperties}
                onClick={() => onColorChange(selected.id, color)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function TrendLineOverlay({
  lines,
  draft,
  selectedId,
  movingId,
  editingAnchor,
  onSelect,
  onMoveStart,
  onMove,
  onMoveEnd,
  onMoveCancel,
  onAnchorMoveStart,
  onAnchorMove,
  onAnchorMoveEnd,
  onAnchorMoveCancel,
}: {
  lines: ProjectedTrendLine[]
  draft?: LineGeometry
  selectedId?: string
  movingId?: string
  editingAnchor?: { drawingId: string; anchorIndex: 0 | 1 }
  onSelect: (id: string) => void
  onMoveStart: (event: ReactPointerEvent<SVGLineElement>, drawing: TrendLineDrawing) => void
  onMove: (event: ReactPointerEvent<SVGLineElement>) => void
  onMoveEnd: (event: ReactPointerEvent<SVGLineElement>) => void
  onMoveCancel: (event: ReactPointerEvent<SVGLineElement>) => void
  onAnchorMoveStart: (event: ReactPointerEvent<SVGCircleElement>, drawing: TrendLineDrawing, anchorIndex: 0 | 1) => void
  onAnchorMove: (event: ReactPointerEvent<SVGCircleElement>) => void
  onAnchorMoveEnd: (event: ReactPointerEvent<SVGCircleElement>) => void
  onAnchorMoveCancel: (event: ReactPointerEvent<SVGCircleElement>) => void
}) {
  return (
    <div className="chart-trend-lines">
      <svg width="100%" height="100%" aria-label="趋势线图层">
        {lines.map(line => (
          <g key={line.drawing.id} className={[
            'trend-line',
            line.drawing.id === selectedId ? 'selected' : '',
            line.drawing.id === movingId ? 'moving' : '',
            line.drawing.id === editingAnchor?.drawingId ? 'editing-anchor' : '',
          ].filter(Boolean).join(' ')}>
            <line
              className="trend-line-hit"
              x1={line.line.x1}
              y1={line.line.y1}
              x2={line.line.x2}
              y2={line.line.y2}
              onPointerDown={event => {
                event.preventDefault()
                event.stopPropagation()
                onSelect(line.drawing.id)
                onMoveStart(event, line.drawing)
              }}
              onPointerMove={onMove}
              onPointerUp={onMoveEnd}
              onPointerCancel={onMoveCancel}
            />
            <line
              className="trend-line-stroke"
              x1={line.line.x1}
              y1={line.line.y1}
              x2={line.line.x2}
              y2={line.line.y2}
              stroke={line.drawing.style.color}
              strokeWidth={line.drawing.style.width}
              strokeDasharray={trendLineDashPattern(line.drawing.style.dash)}
              strokeLinecap={line.drawing.style.dash === 'dotted' ? 'round' : 'butt'}
            />
            {line.drawing.id === selectedId && ([0, 1] as const).map(anchorIndex => {
              const cx = anchorIndex === 0 ? line.anchors.x1 : line.anchors.x2
              const cy = anchorIndex === 0 ? line.anchors.y1 : line.anchors.y2
              return <g key={anchorIndex}>
                <circle
                  className="trend-line-anchor-hit"
                  data-anchor-index={anchorIndex}
                  cx={cx}
                  cy={cy}
                  r="10"
                  aria-label={`拖动趋势线${anchorIndex === 0 ? '起点' : '终点'}`}
                  onPointerDown={event => onAnchorMoveStart(event, line.drawing, anchorIndex)}
                  onPointerMove={onAnchorMove}
                  onPointerUp={onAnchorMoveEnd}
                  onPointerCancel={onAnchorMoveCancel}
                />
                <circle
                  className={[
                    'trend-line-anchor-handle',
                    editingAnchor?.drawingId === line.drawing.id && editingAnchor.anchorIndex === anchorIndex ? 'active' : '',
                  ].filter(Boolean).join(' ')}
                  cx={cx}
                  cy={cy}
                  r="4"
                />
              </g>
            })}
          </g>
        ))}
        {draft && (
          <g className="trend-line draft">
            <line className="trend-line-stroke" x1={draft.x1} y1={draft.y1} x2={draft.x2} y2={draft.y2}/>
            <circle cx={draft.x1} cy={draft.y1} r="3.5"/>
            <circle cx={draft.x2} cy={draft.y2} r="3.5"/>
          </g>
        )}
      </svg>
    </div>
  )
}

type GeneratedPivotGeometry = {
  id: string
  kind: 'high' | 'low'
  x: number
  y: number
  price: number
  tentative: boolean
  pivotDate: string
  confirmedDate?: string
}

type GeneratedTrendLineGeometry = {
  id: string
  kind: 'support' | 'resistance'
  horizon: 'short' | 'long'
  line: LineGeometry
  score: number
  touchCount: number
}

type GeneratedZoneGeometry = {
  id: string
  kind: 'key-level' | 'estimated-volume-at-price'
  y: number
  height: number
  width: number
  lower: number
  upper: number
  score: number
  estimatedShare?: number
}

type GeneratedPatternGeometry = {
  id: string
  displayName: string
  state: 'forming' | 'confirmed' | 'invalidated'
  primary: boolean
  points: string
  neckline: LineGeometry
  boundaries: LineGeometry[]
  labelX: number
  labelY: number
  score: number
}

export type GeneratedBreakoutState = {
  state: 'forming' | 'ready' | 'triggered' | 'confirmed' | 'retesting' | 'continuing' | 'failed' | 'invalidated' | 'stale'
  direction: 'up' | 'down'
  boundaryPrice: number
  invalidationPrice: number
  triggerDate?: string
  confirmationDate?: string
  failureDate?: string
  preview: boolean
  eventKind?: 'upward-breakout' | 'downward-breakdown' | 'retest' | 'false-breakout-risk' | 'no-structural-change'
}

export function projectGeneratedPivots(
  run: TrendAnalysisRun | null,
  chart: IChartApi | null,
  priceSeries: { priceToCoordinate: (price: number) => number | null } | null,
  host: HTMLDivElement | null,
  showTentative: boolean,
): GeneratedPivotGeometry[] {
  if (!run || !chart || !priceSeries || !host) return []
  const projected = run.items.flatMap(item => {
    if (item.item_type !== 'anchor') return []
    const kind = item.payload.kind
    const pivotDate = item.payload.pivot_date
    const price = item.payload.price
    const tentative = item.payload.tentative === true
    if ((kind !== 'high' && kind !== 'low') || typeof pivotDate !== 'string' || typeof price !== 'number') return []
    if (tentative && !showTentative) return []
    const x = chart.timeScale().timeToCoordinate(pivotDate as Time)
    const y = priceSeries.priceToCoordinate(price)
    if (x === null || y === null || x < -20 || x > host.clientWidth + 20 || y < -20 || y > host.clientHeight + 20) return []
    return [{
      id: item.item_id,
      kind: kind as GeneratedPivotGeometry['kind'],
      x,
      y,
      price,
      tentative,
      pivotDate,
      confirmedDate: typeof item.payload.confirmed_date === 'string'
        ? item.payload.confirmed_date
        : undefined,
    }]
  })
  const seen = new Set<string>()
  return projected.filter(item => {
    const key = `${item.kind}:${item.pivotDate}:${item.price}:${item.tentative}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function projectGeneratedTrendLines(
  run: TrendAnalysisRun | null,
  chart: IChartApi | null,
  priceSeries: { priceToCoordinate: (price: number) => number | null } | null,
  host: HTMLDivElement | null,
  showShort: boolean,
  showLong: boolean,
): GeneratedTrendLineGeometry[] {
  if (!run || !chart || !priceSeries || !host) return []
  const width = chart.timeScale().width()
  const height = chart.panes()[0]?.getHeight() ?? host.clientHeight
  return run.items.flatMap(item => {
    if (item.item_type !== 'line') return []
    const kind = item.payload.kind
    const horizon = item.payload.horizon
    const firstDate = item.payload.first_pivot_date
    const secondDate = item.payload.second_pivot_date
    const firstPrice = item.payload.first_price
    const secondPrice = item.payload.second_price
    if ((kind !== 'support' && kind !== 'resistance')
      || (horizon !== 'short' && horizon !== 'long')
      || typeof firstDate !== 'string' || typeof secondDate !== 'string'
      || typeof firstPrice !== 'number' || typeof secondPrice !== 'number') return []
    if ((horizon === 'short' && !showShort) || (horizon === 'long' && !showLong)) return []
    const x1 = chart.timeScale().timeToCoordinate(firstDate as Time)
    const x2 = chart.timeScale().timeToCoordinate(secondDate as Time)
    const y1 = priceSeries.priceToCoordinate(firstPrice)
    const y2 = priceSeries.priceToCoordinate(secondPrice)
    if (x1 === null || x2 === null || y1 === null || y2 === null) return []
    return [{
      id: item.item_id,
      kind,
      horizon,
      line: extendLineToBounds({ x1, y1, x2, y2 }, width, height),
      score: typeof item.payload.score === 'number' ? item.payload.score : 0,
      touchCount: typeof item.payload.touch_count === 'number' ? item.payload.touch_count : 0,
    }]
  })
}

export function projectGeneratedZones(
  run: TrendAnalysisRun | null,
  chart: IChartApi | null,
  priceSeries: { priceToCoordinate: (price: number) => number | null } | null,
  host: HTMLDivElement | null,
  showKeyLevels: boolean,
  showVolumeZones: boolean,
): GeneratedZoneGeometry[] {
  if (!run || !chart || !priceSeries || !host) return []
  const width = chart.timeScale().width()
  const paneHeight = chart.panes()[0]?.getHeight() ?? host.clientHeight
  return run.items.flatMap(item => {
    if (item.item_type !== 'zone') return []
    const kind = item.payload.kind
    const lower = item.payload.lower
    const upper = item.payload.upper
    if ((kind !== 'key-level' && kind !== 'estimated-volume-at-price')
      || typeof lower !== 'number' || typeof upper !== 'number') return []
    if ((kind === 'key-level' && !showKeyLevels)
      || (kind === 'estimated-volume-at-price' && !showVolumeZones)) return []
    const lowerY = priceSeries.priceToCoordinate(lower)
    const upperY = priceSeries.priceToCoordinate(upper)
    if (lowerY === null || upperY === null) return []
    const top = Math.max(0, Math.min(lowerY, upperY))
    const bottom = Math.min(paneHeight, Math.max(lowerY, upperY))
    if (bottom < 0 || top > paneHeight) return []
    return [{
      id: item.item_id,
      kind,
      y: top,
      height: Math.max(kind === 'key-level' ? 2 : 1, bottom - top),
      width,
      lower,
      upper,
      score: typeof item.payload.score === 'number' ? item.payload.score : 0,
      estimatedShare: typeof item.payload.estimated_share === 'number'
        ? item.payload.estimated_share
        : undefined,
    }]
  })
}

export function projectGeneratedPatterns(
  run: TrendAnalysisRun | null,
  chart: IChartApi | null,
  priceSeries: { priceToCoordinate: (price: number) => number | null } | null,
  visible: boolean,
): GeneratedPatternGeometry[] {
  if (!visible || !run || !chart || !priceSeries) return []
  return run.items.flatMap(item => {
    if (item.item_type !== 'pattern') return []
    const displayName = item.payload.display_name
    const state = item.payload.completion_state
    const pivots = item.payload.pivots
    const necklinePrice = item.payload.neckline_price
    if (typeof displayName !== 'string'
      || (state !== 'forming' && state !== 'confirmed' && state !== 'invalidated')
      || !Array.isArray(pivots) || typeof necklinePrice !== 'number') return []
    const projected = pivots.flatMap(value => {
      if (!value || typeof value !== 'object') return []
      const pivot = value as Record<string, unknown>
      if (typeof pivot.pivot_date !== 'string' || typeof pivot.price !== 'number') return []
      const x = chart.timeScale().timeToCoordinate(pivot.pivot_date as Time)
      const y = priceSeries.priceToCoordinate(pivot.price)
      return x === null || y === null ? [] : [{ x, y }]
    })
    if (projected.length !== pivots.length || projected.length < 1) return []
    const necklineY = priceSeries.priceToCoordinate(necklinePrice)
    if (necklineY === null) return []
    const first = projected[0]
    const last = projected.at(-1)!
    const boundaryGeometry = item.payload.boundary_geometry
    const boundaries: LineGeometry[] = []
    if (boundaryGeometry && typeof boundaryGeometry === 'object') {
      const geometry = boundaryGeometry as Record<string, unknown>
      const boundaryValues = [geometry.upper, geometry.lower]
      if (Array.isArray(geometry.segments)) boundaryValues.push(...geometry.segments)
      for (const value of boundaryValues) {
        if (!value || typeof value !== 'object') continue
        const boundary = value as Record<string, unknown>
        if (typeof boundary.start_date !== 'string' || typeof boundary.end_date !== 'string'
          || typeof boundary.start_price !== 'number' || typeof boundary.end_price !== 'number') continue
        const x1 = chart.timeScale().timeToCoordinate(boundary.start_date as Time)
        const x2 = chart.timeScale().timeToCoordinate(boundary.end_date as Time)
        const y1 = priceSeries.priceToCoordinate(boundary.start_price)
        const y2 = priceSeries.priceToCoordinate(boundary.end_price)
        if (x1 === null || x2 === null || y1 === null || y2 === null) continue
        boundaries.push(extendLineToBounds(
          { x1, y1, x2, y2 },
          chart.timeScale().width(),
          chart.panes()[0]?.getHeight() ?? Math.max(y1, y2),
        ))
      }
    }
    if (projected.length < 3 && boundaries.length === 0) return []
    return [{
      id: item.item_id,
      displayName,
      state,
      primary: item.payload.primary === true,
      points: projected.map(point => `${point.x},${point.y}`).join(' '),
      neckline: { x1: first.x, y1: necklineY, x2: chart.timeScale().width(), y2: necklineY },
      boundaries,
      labelX: Math.min(first.x, last.x) + Math.abs(last.x - first.x) / 2,
      labelY: Math.min(...projected.map(point => point.y), necklineY) - 5,
      score: typeof item.payload.score === 'number' ? item.payload.score : 0,
    }]
  })
}

export function readGeneratedBreakoutState(
  run: TrendAnalysisRun | null,
  visible: boolean,
): GeneratedBreakoutState | undefined {
  if (!visible || !run) return undefined
  const primaryIds = new Set(run.items.filter(item => (
    item.item_type === 'pattern' && item.payload.primary === true
  )).map(item => item.item_id))
  const patternCandidates = run.items.filter(item => (
    item.item_type === 'evidence'
    && item.payload.kind === 'breakout-state-summary'
  ))
  const structuralCandidates = run.items.filter(item => (
    item.item_type === 'evidence'
    && item.payload.kind === 'latest-structural-event-summary'
  ))
  const item = structuralCandidates.find(value => value.payload.current_state !== 'ready')
    ?? patternCandidates.find(value => (
    typeof value.parent_item_id === 'string' && primaryIds.has(value.parent_item_id)
    ))
    ?? structuralCandidates[0]
    ?? patternCandidates[0]
  if (!item) return undefined
  const state = item.payload.current_state
  const direction = item.payload.direction
  const boundaryPrice = item.payload.boundary_price
  const invalidationPrice = item.payload.invalidation_level
  const states = new Set([
    'forming', 'ready', 'triggered', 'confirmed', 'retesting',
    'continuing', 'failed', 'invalidated', 'stale',
  ])
  if (typeof state !== 'string' || !states.has(state)
    || (direction !== 'up' && direction !== 'down')
    || typeof boundaryPrice !== 'number' || typeof invalidationPrice !== 'number') return undefined
  return {
    state: state as GeneratedBreakoutState['state'],
    direction,
    boundaryPrice,
    invalidationPrice,
    triggerDate: typeof item.payload.trigger_date === 'string' ? item.payload.trigger_date : undefined,
    confirmationDate: typeof item.payload.confirmation_date === 'string' ? item.payload.confirmation_date : undefined,
    failureDate: typeof item.payload.failure_date === 'string' ? item.payload.failure_date : undefined,
    preview: item.payload.preview === true,
    ...(item.payload.event_kind === 'upward-breakout'
      || item.payload.event_kind === 'downward-breakdown'
      || item.payload.event_kind === 'retest'
      || item.payload.event_kind === 'false-breakout-risk'
      || item.payload.event_kind === 'no-structural-change'
      ? { eventKind: item.payload.event_kind }
      : {}),
  }
}

function GeneratedAnalysisOverlay({
  pivots,
  lines,
  zones,
  patterns,
  breakoutState,
  run,
  preview,
}: {
  pivots: GeneratedPivotGeometry[]
  lines: GeneratedTrendLineGeometry[]
  zones: GeneratedZoneGeometry[]
  patterns: GeneratedPatternGeometry[]
  breakoutState?: GeneratedBreakoutState
  run: TrendAnalysisRun
  preview: boolean
}) {
  const labeled = new Set(pivots.slice(-12).map(item => item.id))
  return (
    <div className="chart-generated-analysis" aria-label="自动趋势分析图层">
      <svg width="100%" height="100%" aria-hidden="true">
        {zones.map(item => (
          <rect
            key={item.id}
            className={`generated-price-zone ${item.kind}`}
            x={0}
            y={item.y}
            width={item.width}
            height={item.height}
          >
            <title>{item.kind === 'key-level'
              ? `关键位 ${formatPrice(item.lower)}-${formatPrice(item.upper)} · 评分 ${item.score.toFixed(2)}`
              : `日线估算成交密集区 ${formatPrice(item.lower)}-${formatPrice(item.upper)} · 占比 ${((item.estimatedShare ?? 0) * 100).toFixed(1)}%`}</title>
          </rect>
        ))}
        {patterns.map(item => (
          <g key={item.id} className={`generated-pattern ${item.state} ${item.primary ? 'primary' : 'alternative'}`}>
            {item.boundaries.map((boundary, index) => (
              <line
                key={index}
                className="generated-pattern-boundary"
                x1={boundary.x1}
                y1={boundary.y1}
                x2={boundary.x2}
                y2={boundary.y2}
              />
            ))}
            <polyline points={item.points}/>
            <line
              className="generated-pattern-neckline"
              x1={item.neckline.x1}
              y1={item.neckline.y1}
              x2={item.neckline.x2}
              y2={item.neckline.y2}
            />
            <text x={item.labelX} y={item.labelY}>{item.displayName}</text>
            <title>{`${item.displayName} · ${item.state === 'forming' ? '形成中' : item.state === 'confirmed' ? '已确认' : '已失效'} · 评分 ${item.score.toFixed(2)}`}</title>
          </g>
        ))}
        {lines.map(item => (
          <line
            key={item.id}
            className={`generated-trend-line ${item.kind} ${item.horizon}`}
            x1={item.line.x1}
            y1={item.line.y1}
            x2={item.line.x2}
            y2={item.line.y2}
          >
            <title>{`${item.horizon === 'short' ? '短期' : '长期'}${item.kind === 'support' ? '支撑' : '压力'} · 评分 ${item.score.toFixed(2)} · 触碰 ${item.touchCount}`}</title>
          </line>
        ))}
        {pivots.map(pivot => {
          const markerY = pivot.kind === 'high' ? pivot.y - 7 : pivot.y + 7
          const points = pivot.kind === 'high'
            ? `${pivot.x - 4},${markerY - 4} ${pivot.x + 4},${markerY - 4} ${pivot.x},${markerY + 3}`
            : `${pivot.x - 4},${markerY + 4} ${pivot.x + 4},${markerY + 4} ${pivot.x},${markerY - 3}`
          return <g key={pivot.id} className={`generated-pivot ${pivot.kind} ${pivot.tentative ? 'tentative' : 'confirmed'}`}>
            <line x1={pivot.x} y1={pivot.y} x2={pivot.x} y2={markerY}/>
            <polygon points={points}/>
            {labeled.has(pivot.id) && (
              <text
                x={pivot.x + 6}
                y={pivot.kind === 'high' ? markerY - 5 : markerY + 9}
              >{formatPrice(pivot.price)}</text>
            )}
            <title>{`${pivot.kind === 'high' ? '高点' : '低点'} ${pivot.pivotDate} · ${pivot.tentative ? '待确认' : `确认于 ${pivot.confirmedDate}`}`}</title>
          </g>
        })}
      </svg>
      <div className={`trend-analysis-evidence ${preview ? 'preview' : 'official'} ${run.stale ? 'stale' : ''}`}>
        <span>{preview ? '盘中预览' : '正式'}</span>
        <span>日线</span>
        <span>截至 {run.as_of_date}</span>
        <span>{pivots.length} 个枢轴</span>
        <span>{lines.length} 条趋势线</span>
        <span>{zones.length} 个价格区</span>
        <span>{patterns.length} 个形态</span>
        {breakoutState && (
          <span
            className={`breakout-state ${breakoutState.state}`}
            title={`边界 ${formatPrice(breakoutState.boundaryPrice)} · 失效位 ${formatPrice(breakoutState.invalidationPrice)} · 触发 ${breakoutState.triggerDate ?? '-'} · 确认 ${breakoutState.confirmationDate ?? '-'} · 失败 ${breakoutState.failureDate ?? '-'}`}
          >{breakoutState.preview ? '盘中预览 ' : ''}{breakoutStateLabel(breakoutState)}</span>
        )}
        {run.stale && <span>已过期</span>}
      </div>
    </div>
  )
}

function breakoutStateLabel(value: GeneratedBreakoutState): string {
  if (value.eventKind === 'upward-breakout') return '向上突破'
  if (value.eventKind === 'downward-breakdown') return '向下破位'
  if (value.eventKind === 'retest') return '回踩'
  if (value.eventKind === 'false-breakout-risk') return '假突破风险'
  if (value.eventKind === 'no-structural-change') return '无结构变化'
  return ({
    forming: '形成中', ready: '准备', triggered: '已触发', confirmed: '已确认',
    retesting: '回踩', continuing: '延续', failed: '失败',
    invalidated: '失效', stale: '陈旧',
  })[value.state]
}

function trendLineDashPattern(dash: TrendLineDash): string | undefined {
  if (dash === 'solid') return undefined
  if (dash === 'dotted') return '1 5'
  if (dash === 'long-dashed') return '14 7'
  if (dash === 'dash-dot') return '12 5 2 5'
  return '6 4'
}

function MarketAnnotationOverlay({ geometry }: { geometry: MarketAnnotationGeometry }) {
  return (
    <div className="chart-market-annotations">
      <svg width={geometry.width} height={geometry.height} aria-hidden="true">
        {geometry.gaps.map(gap => {
          const top = Math.min(gap.y1, gap.y2)
          const height = Math.max(1, Math.abs(gap.y2 - gap.y1))
          const width = Math.max(1, gap.x2 - gap.x1)
          return (
            <g key={`${gap.direction}-${gap.startDate}`} className={`price-gap price-gap-${gap.direction}`}>
              <rect x={gap.x1} y={top} width={width} height={height}/>
            </g>
          )
        })}
        {geometry.extrema.map(point => {
          const drawLeft = point.x > geometry.width * 0.72
          const lineEnd = point.x + (drawLeft ? -24 : 24)
          const textX = lineEnd + (drawLeft ? -3 : 3)
          const textY = point.kind === 'high'
            ? clamp(point.y - 5, 12, geometry.height - 8)
            : clamp(point.y + 12, 12, geometry.height - 5)
          return (
            <g key={point.kind} className={`extreme-price extreme-price-${point.kind}`}>
              <circle cx={point.x} cy={point.y} r="2"/>
              <line x1={point.x} y1={point.y} x2={lineEnd} y2={point.y}/>
              <text x={textX} y={textY} textAnchor={drawLeft ? 'end' : 'start'}>{formatPrice(point.price)}</text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function MeasurementOverlay({
  measurement,
  geometry,
}: {
  measurement: RangeMeasurement
  geometry: MeasurementGeometry
}) {
  const tone = measurement.changePercent >= 0 ? 'rise' : 'fall'
  const strokeTone = measurement.changePercent >= 0 ? 'measurement-rise' : 'measurement-fall'
  const horizontalLabelX = (geometry.startX + geometry.endX) / 2
  const horizontalLabelY = clamp(geometry.startY - 7, 12, geometry.height - 6)
  return (
    <div className="chart-measurement-overlay">
      <svg width={geometry.width} height={geometry.height} aria-hidden="true">
        <polyline
          className={`measurement-triangle ${strokeTone}`}
          points={`${geometry.startX},${geometry.startY} ${geometry.endX},${geometry.startY} ${geometry.endX},${geometry.endY} ${geometry.startX},${geometry.startY}`}
        />
        <circle className={strokeTone} cx={geometry.startX} cy={geometry.startY} r="3"/>
        <circle className={strokeTone} cx={geometry.endX} cy={geometry.endY} r="3"/>
        <text className="measurement-duration" x={horizontalLabelX} y={horizontalLabelY} textAnchor="middle">
          {measurement.elapsedDays}天 · {measurement.kLineCount}根K线
        </text>
      </svg>
      <div className="chart-measurement-readout" style={{ left: geometry.labelLeft, top: geometry.labelTop }}>
        <span><small>{measurement.from}</small><b>开 {formatPrice(measurement.open)}</b></span>
        <span><small>{measurement.to}</small><b>收 {formatPrice(measurement.close)}</b></span>
        <strong className={tone}>涨跌 {formatChangePercent(measurement.changePercent)}</strong>
      </div>
    </div>
  )
}

function ChartReadout({ value }: { value: Readout }) {
  const candleTone = value.changePercent === undefined
    ? value.close >= value.open ? 'rise' : 'fall'
    : value.changePercent >= 0 ? 'rise' : 'fall'
  const changeTone = value.changePercent === undefined
    ? undefined
    : value.changePercent >= 0 ? 'rise' : 'fall'
  return (
    <div className="chart-readout">
      {value.bar_state === 'intraday' && <span className={value.stale ? 'live-badge stale' : 'live-badge'}>{value.stale ? '盘中延迟' : '盘中'}</span>}
      <span>{value.trade_date}</span>
      <span>开 <b>{formatPrice(value.open)}</b></span>
      <span>高 <b>{formatPrice(value.high)}</b></span>
      <span>低 <b>{formatPrice(value.low)}</b></span>
      <span>收 <b className={candleTone}>{formatPrice(value.close)}</b></span>
      <span>涨跌 <b className={changeTone}>{formatChangePercent(value.changePercent)}</b></span>
      <span>量 <b>{formatVolume(value.volume)}</b></span>
      {value.ma5 !== undefined && <span className="ma5-value">MA5 {formatPrice(value.ma5)}</span>}
      {value.ma20 !== undefined && <span className="ma20-value">MA20 {formatPrice(value.ma20)}</span>}
      {value.ma60 !== undefined && <span className="ma60-value">MA60 {formatPrice(value.ma60)}</span>}
    </div>
  )
}

function applyMacdSeries(
  values: MacdPoint[],
  times: Set<string>,
  dif: ISeriesApi<'Line'> | null,
  dea: ISeriesApi<'Line'> | null,
  histogram: ISeriesApi<'Histogram'> | null,
) {
  if (!dif || !dea || !histogram) return
  const visible = values.filter(item => times.has(item.time))
  dif.setData(visible.map(item => ({ time: item.time, value: item.dif })))
  dea.setData(visible.map(item => ({ time: item.time, value: item.dea })))
  histogram.setData(visible.map(item => ({
    time: item.time,
    value: item.histogram,
    color: item.histogram >= 0 ? `${rising}b3` : `${falling}b3`,
  })))
}

function applyVolumeSeries(
  bars: RenderBar[],
  previousCloseByDate: Map<string, number>,
  volume: ISeriesApi<'Histogram'>,
) {
  volume.setData(bars.map(item => ({
    time: item.trade_date,
    value: item.volume,
    color: `${candleColor(item, previousCloseByDate.get(item.period_start))}99`,
  })))
}

function setPaneStretchFactors(chart: IChartApi) {
  chart.panes()[0]?.setStretchFactor(3)
  chart.panes().slice(1).forEach(pane => pane.setStretchFactor(1))
}

function captureViewport(chart: IChartApi | null, dataCount: number): ViewportSnapshot | undefined {
  const logical = chart?.timeScale().getVisibleLogicalRange()
  return logical ? { logical: { from: logical.from, to: logical.to }, dataCount } : undefined
}

function resolveRangeSelection(
  chart: IChartApi,
  renderedBars: RenderBar[],
  box: SelectionBox,
  pointerX: number,
  pointerY: number,
  hostWidth: number,
  hostHeight: number,
): RangeSelection | undefined {
  if (renderedBars.length === 0) return undefined
  const leftLogical = chart.timeScale().coordinateToLogical(box.left)
  const rightLogical = chart.timeScale().coordinateToLogical(box.left + box.width)
  if (leftLogical === null || rightLogical === null) return undefined
  const leftIndex = clamp(Math.round(Number(leftLogical)), 0, renderedBars.length - 1)
  const rightIndex = clamp(Math.round(Number(rightLogical)), 0, renderedBars.length - 1)
  const firstIndex = Math.min(leftIndex, rightIndex)
  const lastIndex = Math.max(leftIndex, rightIndex)
  return {
    first: renderedBars[firstIndex],
    last: renderedBars[lastIndex],
    count: lastIndex - firstIndex + 1,
    box,
    menuLeft: clamp(pointerX + 6, 6, Math.max(6, hostWidth - 190)),
    menuTop: clamp(pointerY + 6, 6, Math.max(6, hostHeight - 78)),
  }
}

function localPoint(event: ReactPointerEvent<HTMLDivElement>): { x: number; y: number } {
  const bounds = event.currentTarget.getBoundingClientRect()
  return { x: event.clientX - bounds.left, y: event.clientY - bounds.top }
}

function chartPoint(event: ReactPointerEvent<Element>, host: HTMLDivElement | null): { x: number; y: number } {
  const bounds = host?.getBoundingClientRect()
  return bounds
    ? { x: event.clientX - bounds.left, y: event.clientY - bounds.top }
    : { x: event.clientX, y: event.clientY }
}

function rectangleFromPoints(startX: number, startY: number, endX: number, endY: number): SelectionBox {
  return {
    left: Math.min(startX, endX),
    top: Math.min(startY, endY),
    width: Math.abs(endX - startX),
    height: Math.abs(endY - startY),
  }
}

function valueAt(series: ISeriesApi<'Line'>, param: { seriesData: Map<unknown, unknown> }): number | undefined {
  const item = param.seriesData.get(series) as LineData<Time> | undefined
  return item && 'value' in item ? item.value : undefined
}

function formatPrice(value: number): string {
  return value >= 1000 ? value.toFixed(1) : value.toFixed(2)
}

function formatChangePercent(value?: number): string {
  if (value === undefined) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatVolume(value: number): string {
  if (value >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)}万`
  return value.toLocaleString('zh-CN')
}
