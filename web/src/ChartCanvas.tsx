import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import { Eye, EyeOff, MousePointer2, PencilLine, Percent, Settings2, Trash2, X, ZoomIn } from 'lucide-react'
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
import { barsInRenderPeriod, chooseAnchor, type LineGeometry } from './trendLines'
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


type ChartCanvasProps = {
  symbol: string
  range: ChartRange
  priceMode: PriceMode
  volumeVisible: boolean
  indicator: ChartIndicator
  initialVisibleRange?: VisibleRange
  onCoverageChange?: (bars: number, first?: string, last?: string) => void
  onVisibleRangeChange?: (value: VisibleRange) => void
  onVolumeVisibleChange?: (visible: boolean) => void
  onIndicatorChange?: (indicator: ChartIndicator) => void
}

const rising = '#ef5350'
const falling = '#26a269'
const risingSoft = '#e99693'
const fallingSoft = '#70be9a'
const trendLineColors = ['#f0b85a', '#ef5350', '#26a269', '#57a7d9', '#b984cc', '#d8dde6'] as const
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
  range,
  priceMode,
  volumeVisible,
  indicator,
  initialVisibleRange,
  onCoverageChange,
  onVisibleRangeChange,
  onVolumeVisibleChange,
  onIndicatorChange,
}: ChartCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
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
  const bucketRef = useRef(1)
  const applyBucketRef = useRef<(bucket: number, preserve?: IRange<Time>) => void>(() => undefined)
  const recalculateLodRef = useRef<() => void>(() => undefined)
  const resetAutoScaleRef = useRef<(visibleBars?: number, width?: number, low?: number, high?: number) => void>(() => undefined)
  const suppressLodRef = useRef(false)
  const coverageCallbackRef = useRef(onCoverageChange)
  const visibleRangeCallbackRef = useRef(onVisibleRangeChange)
  const priceModeRef = useRef(priceMode)
  const pendingVisibleRangeRef = useRef<IRange<Time> | undefined>(undefined)
  const skipRangeResetRef = useRef(false)
  const [bars, setBars] = useState<DailyBar[]>([])
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [readout, setReadout] = useState<Readout | null>(null)
  const [lodBucket, setLodBucket] = useState(1)
  const [selectionBox, setSelectionBox] = useState<SelectionBox>()
  const [rangeSelection, setRangeSelection] = useState<RangeSelection>()
  const [measurement, setMeasurement] = useState<RangeMeasurement>()
  const [drawingTool, setDrawingTool] = useState<'browse' | 'trend-line'>('browse')
  const [drawings, setDrawings] = useState<TrendLineDrawing[]>(() => loadSymbolDrawings(symbol))
  const [drawingDraft, setDrawingDraft] = useState<[TrendLineAnchor, TrendLineAnchor]>()
  const [selectedDrawingId, setSelectedDrawingId] = useState<string>()
  const [drawingManagerOpen, setDrawingManagerOpen] = useState(false)
  const [overlayRevision, setOverlayRevision] = useState(0)
  const liveFailureCountRef = useRef(0)

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
    const reload = () => setDrawings(loadSymbolDrawings(symbol))
    reload()
    setSelectedDrawingId(undefined)
    setDrawingDraft(undefined)
    drawingDragRef.current = undefined
    setDrawingTool('browse')
    setDrawingManagerOpen(false)
    return subscribeSymbolDrawings(symbol, reload)
  }, [symbol])

  const replaceBars = useCallback((next: DailyBar[], preserveView = false) => {
    if (preserveView) {
      pendingVisibleRangeRef.current = chartRef.current?.timeScale().getVisibleRange() ?? undefined
      skipRangeResetRef.current = true
    }
    barsRef.current = next
    previousCloseByDateRef.current = previousCloseByDate(next)
    setBars(next)
    setReadout(latestReadout(next))
  }, [])

  useEffect(() => {
    if (!hostRef.current) return
    const chart = createChart(hostRef.current, {
      autoSize: true,
      layout: chartLayoutOptions,
      grid: {
        vertLines: { color: '#1c222a' },
        horzLines: { color: '#1c222a' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#687383', labelBackgroundColor: '#303743' },
        horzLine: { color: '#687383', labelBackgroundColor: '#303743' },
      },
      rightPriceScale: { borderColor: '#303743', scaleMargins: calculatePriceScaleMargins(0, 1) },
      timeScale: {
        borderColor: '#303743',
        rightOffset: 3,
        minBarSpacing: 0.08,
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
        if (nextBucket !== bucketRef.current) applyBucketRef.current(nextBucket, visible)
      })
      window.clearTimeout(visibleRangeTimer)
      visibleRangeTimer = window.setTimeout(() => {
        const visible = chart.timeScale().getVisibleRange()
        if (visible && !suppressLodRef.current) {
          visibleRangeCallbackRef.current?.({ from: String(visible.from), to: String(visible.to) })
        }
      }, 180)
    }
    chart.timeScale().subscribeVisibleTimeRangeChange(recalculateLod)
    recalculateLodRef.current = recalculateLod
    const resizeObserver = new ResizeObserver(recalculateLod)
    resizeObserver.observe(hostRef.current)

    chartRef.current = chart
    candleRef.current = candles
    ma5Ref.current = ma5
    ma20Ref.current = ma20
    ma60Ref.current = ma60
    return () => {
      window.cancelAnimationFrame(lodFrame)
      window.clearTimeout(visibleRangeTimer)
      resizeObserver.disconnect()
      chart.timeScale().unsubscribeVisibleTimeRangeChange(recalculateLod)
      chart.remove()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !volumeVisible) {
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
  }, [volumeVisible])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || indicator !== 'macd') {
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
  }, [indicator])

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
        if (preserve) chartRef.current?.timeScale().setVisibleRange(preserve)
        resetAutoScaleRef.current()
        window.requestAnimationFrame(() => {
          suppressLodRef.current = false
        })
      })
    }
    applyBucketRef.current(1, pendingVisibleRangeRef.current)
    pendingVisibleRangeRef.current = undefined
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

  return (
    <div
      className={selectionDragRef.current ? 'chart-stage selecting' : 'chart-stage'}
      onPointerDown={handleSelectionStart}
      onPointerMove={handleSelectionMove}
      onPointerUp={handleSelectionEnd}
      onPointerCancel={handleSelectionCancel}
      onContextMenu={event => event.preventDefault()}
    >
      <div ref={hostRef} className="chart-host"/>
      <div className="chart-drawing-toolbar" onPointerDown={event => event.stopPropagation()}>
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
          className={drawingManagerOpen ? 'active' : ''}
          title="趋势线管理"
          aria-label="趋势线管理"
          aria-pressed={drawingManagerOpen}
          onClick={() => setDrawingManagerOpen(value => !value)}
        ><Settings2 size={13}/></button>
        <button title="删除选中的趋势线" aria-label="删除选中的趋势线" disabled={!selectedDrawingId} onClick={removeSelectedDrawing}><Trash2 size={13}/></button>
        {drawingTool === 'trend-line' && <button title="取消画线" aria-label="取消画线" onClick={cancelDrawing}><X size={13}/></button>}
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
        onSelect={setSelectedDrawingId}
      />
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
      {readout && <ChartReadout value={readout} lodBucket={lodBucket}/>}
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
  onSelect,
}: {
  lines: ProjectedTrendLine[]
  draft?: LineGeometry
  selectedId?: string
  onSelect: (id: string) => void
}) {
  return (
    <div className="chart-trend-lines">
      <svg width="100%" height="100%" aria-label="趋势线图层">
        {lines.map(line => (
          <g key={line.drawing.id} className={line.drawing.id === selectedId ? 'trend-line selected' : 'trend-line'}>
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
              }}
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
            {line.drawing.id === selectedId && <>
              <circle cx={line.anchors.x1} cy={line.anchors.y1} r="3.5"/>
              <circle cx={line.anchors.x2} cy={line.anchors.y2} r="3.5"/>
            </>}
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

function ChartReadout({ value, lodBucket }: { value: Readout; lodBucket: number }) {
  const candleTone = value.changePercent === undefined
    ? value.close >= value.open ? 'rise' : 'fall'
    : value.changePercent >= 0 ? 'rise' : 'fall'
  const changeTone = value.changePercent === undefined
    ? undefined
    : value.changePercent >= 0 ? 'rise' : 'fall'
  return (
    <div className="chart-readout">
      <span className="lod-badge">{lodBucket}D</span>
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
