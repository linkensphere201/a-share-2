import { useEffect, useMemo, useRef, useState } from 'react'
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
  type ISeriesApi,
  type LineData,
  type Time,
} from 'lightweight-charts'

export type ChartRange = '1Y' | '3Y' | '10Y' | 'ALL'
export type PriceMode = 'normal' | 'log'

export type DailyBar = {
  trade_date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  source: string
}

type Readout = DailyBar & { ma5?: number; ma20?: number; ma60?: number }

type ChartCanvasProps = {
  symbol: string
  range: ChartRange
  priceMode: PriceMode
  onCoverageChange?: (bars: number, first?: string, last?: string) => void
}

const rising = '#ef5350'
const falling = '#26a269'

export function ChartCanvas({ symbol, range, priceMode, onCoverageChange }: ChartCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const ma5Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ma20Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const ma60Ref = useRef<ISeriesApi<'Line'> | null>(null)
  const barsRef = useRef<DailyBar[]>([])
  const [bars, setBars] = useState<DailyBar[]>([])
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [readout, setReadout] = useState<Readout | null>(null)

  const averages = useMemo(() => ({
    ma5: movingAverage(bars, 5),
    ma20: movingAverage(bars, 20),
    ma60: movingAverage(bars, 60),
  }), [bars])

  useEffect(() => {
    if (!hostRef.current) return
    const chart = createChart(hostRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#0d1014' },
        textColor: '#7f8997',
        panes: { separatorColor: '#303743', separatorHoverColor: '#4b84c6', enableResize: true },
      },
      grid: {
        vertLines: { color: '#1c222a' },
        horzLines: { color: '#1c222a' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#687383', labelBackgroundColor: '#303743' },
        horzLine: { color: '#687383', labelBackgroundColor: '#303743' },
      },
      rightPriceScale: { borderColor: '#303743', scaleMargins: { top: 0.05, bottom: 0.02 } },
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
    const ma5 = chart.addSeries(LineSeries, { color: '#e5b85c', lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
    const ma20 = chart.addSeries(LineSeries, { color: '#57a7d9', lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
    const ma60 = chart.addSeries(LineSeries, { color: '#b984cc', lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
      lastValueVisible: false,
    }, 1)
    chart.panes()[0]?.setStretchFactor(3)
    chart.panes()[1]?.setStretchFactor(1)

    chart.subscribeCrosshairMove(param => {
      if (!param.time) {
        setReadout(latestReadout(barsRef.current))
        return
      }
      const candle = param.seriesData.get(candles) as CandlestickData<Time> | undefined
      if (!candle || !('open' in candle)) return
      const index = barsRef.current.findIndex(item => item.trade_date === String(param.time))
      const source = index >= 0 ? barsRef.current[index].source : ''
      setReadout({
        trade_date: String(param.time),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: index >= 0 ? barsRef.current[index].volume : 0,
        source,
        ma5: valueAt(ma5, param),
        ma20: valueAt(ma20, param),
        ma60: valueAt(ma60, param),
      })
    })

    chartRef.current = chart
    candleRef.current = candles
    volumeRef.current = volume
    ma5Ref.current = ma5
    ma20Ref.current = ma20
    ma60Ref.current = ma60
    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setState('loading')
    fetch(`/api/instruments/${encodeURIComponent(symbol)}/daily-bars`, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<{ items: DailyBar[] }>
      })
      .then(body => {
        barsRef.current = body.items
        setBars(body.items)
        setReadout(latestReadout(body.items))
        setState('ready')
        onCoverageChange?.(
          body.items.length,
          body.items.at(0)?.trade_date,
          body.items.at(-1)?.trade_date,
        )
      })
      .catch(error => {
        if ((error as Error).name !== 'AbortError') setState('error')
      })
    return () => controller.abort()
  }, [symbol, onCoverageChange])

  useEffect(() => {
    const candles: CandlestickData<Time>[] = bars.map(item => ({
      time: item.trade_date,
      open: item.open,
      high: item.high,
      low: item.low,
      close: item.close,
    }))
    const volumes: HistogramData<Time>[] = bars.map(item => ({
      time: item.trade_date,
      value: item.volume,
      color: item.close >= item.open ? `${rising}99` : `${falling}99`,
    }))
    candleRef.current?.setData(candles)
    volumeRef.current?.setData(volumes)
    ma5Ref.current?.setData(averages.ma5)
    ma20Ref.current?.setData(averages.ma20)
    ma60Ref.current?.setData(averages.ma60)
  }, [bars, averages])

  useEffect(() => {
    chartRef.current?.priceScale('right', 0).applyOptions({
      mode: priceMode === 'log' ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    })
  }, [priceMode])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || bars.length === 0) return
    if (range === 'ALL') {
      chart.timeScale().fitContent()
      return
    }
    const last = bars.at(-1)!.trade_date
    const from = subtractYears(last, Number.parseInt(range, 10))
    chart.timeScale().setVisibleRange({ from, to: last })
  }, [bars, range])

  return (
    <div className="chart-stage">
      <div ref={hostRef} className="chart-host"/>
      {readout && <ChartReadout value={readout}/>}
      {state === 'loading' && <div className="chart-state">加载日线数据</div>}
      {state === 'error' && <div className="chart-state error">日线数据加载失败</div>}
      {state === 'ready' && bars.length === 0 && <div className="chart-state">暂无日线数据</div>}
    </div>
  )
}

function ChartReadout({ value }: { value: Readout }) {
  const change = value.close - value.open
  const tone = change >= 0 ? 'rise' : 'fall'
  return (
    <div className="chart-readout">
      <span>{value.trade_date}</span>
      <span>开 <b>{formatPrice(value.open)}</b></span>
      <span>高 <b>{formatPrice(value.high)}</b></span>
      <span>低 <b>{formatPrice(value.low)}</b></span>
      <span>收 <b className={tone}>{formatPrice(value.close)}</b></span>
      <span>量 <b>{formatVolume(value.volume)}</b></span>
      {value.ma5 !== undefined && <span className="ma5-value">MA5 {formatPrice(value.ma5)}</span>}
      {value.ma20 !== undefined && <span className="ma20-value">MA20 {formatPrice(value.ma20)}</span>}
      {value.ma60 !== undefined && <span className="ma60-value">MA60 {formatPrice(value.ma60)}</span>}
    </div>
  )
}

export function movingAverage(bars: DailyBar[], window: number): LineData<Time>[] {
  if (bars.length < window) return []
  const output: LineData<Time>[] = []
  let sum = 0
  for (let index = 0; index < bars.length; index += 1) {
    sum += bars[index].close
    if (index >= window) sum -= bars[index - window].close
    if (index >= window - 1) output.push({ time: bars[index].trade_date, value: sum / window })
  }
  return output
}

function latestReadout(bars: DailyBar[]): Readout | null {
  const latest = bars.at(-1)
  if (!latest) return null
  return {
    ...latest,
    ma5: latestAverage(bars, 5),
    ma20: latestAverage(bars, 20),
    ma60: latestAverage(bars, 60),
  }
}

function latestAverage(bars: DailyBar[], window: number): number | undefined {
  if (bars.length < window) return undefined
  return bars.slice(-window).reduce((sum, item) => sum + item.close, 0) / window
}

function valueAt(series: ISeriesApi<'Line'>, param: { seriesData: Map<unknown, unknown> }): number | undefined {
  const item = param.seriesData.get(series) as LineData<Time> | undefined
  return item && 'value' in item ? item.value : undefined
}

function subtractYears(date: string, years: number): string {
  const value = new Date(`${date}T00:00:00Z`)
  value.setUTCFullYear(value.getUTCFullYear() - years)
  return value.toISOString().slice(0, 10)
}

function formatPrice(value: number): string {
  return value >= 1000 ? value.toFixed(1) : value.toFixed(2)
}

function formatVolume(value: number): string {
  if (value >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)}万`
  return value.toLocaleString('zh-CN')
}
