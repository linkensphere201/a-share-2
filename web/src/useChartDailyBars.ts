import { useCallback, useEffect, useRef, useState } from 'react'
import { mergeProvisionalBar, type DailyBar } from './chartData'
import { logInfo, logWarning } from './eventLogger'
import {
  refreshLatestDailyBar,
  type LatestDailyRefreshFeedback,
} from './latestDailyRefreshClient'
import { useIntradayDailyPolling } from './useIntradayDailyPolling'

type InstrumentIdentity = {
  instrumentKind: string
  priceBasis?: string | null
  ruleVersion?: string | null
}

type ChartDailyBarsOptions = {
  symbol: string
  asOfDate?: string
  onLoadStart: () => void
  onBeforePreserve: () => void
  onBarsChanged: (bars: DailyBar[]) => void
  onCoverageChange?: (rows: number, first?: string, last?: string) => void
  onInstrumentIdentity: (identity: InstrumentIdentity) => void
}

export function dailyBarsUrl(symbol: string, asOfDate?: string): string {
  const base = `/api/instruments/${encodeURIComponent(symbol)}/daily-bars`
  return asOfDate ? `${base}?end_date=${encodeURIComponent(asOfDate)}` : base
}

export function useChartDailyBars({
  symbol,
  asOfDate,
  onLoadStart,
  onBeforePreserve,
  onBarsChanged,
  onCoverageChange,
  onInstrumentIdentity,
}: ChartDailyBarsOptions) {
  const barsRef = useRef<DailyBar[]>([])
  const callbackRef = useRef({
    onLoadStart,
    onBeforePreserve,
    onBarsChanged,
    onCoverageChange,
    onInstrumentIdentity,
  })
  const refreshControllerRef = useRef<AbortController | undefined>(undefined)
  const feedbackTimerRef = useRef(0)
  const warningAtRef = useRef(0)
  const [bars, setBars] = useState<DailyBar[]>([])
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [refreshing, setRefreshing] = useState(false)
  const [refreshFeedback, setRefreshFeedback] = useState<{
    kind: LatestDailyRefreshFeedback
    message: string
  }>()

  useEffect(() => {
    callbackRef.current = {
      onLoadStart,
      onBeforePreserve,
      onBarsChanged,
      onCoverageChange,
      onInstrumentIdentity,
    }
  }, [
    onBarsChanged,
    onBeforePreserve,
    onCoverageChange,
    onInstrumentIdentity,
    onLoadStart,
  ])

  const publishCoverage = useCallback((items: DailyBar[]) => {
    const finalItems = items.filter(item => item.bar_state !== 'intraday')
    callbackRef.current.onCoverageChange?.(
      finalItems.length,
      finalItems.at(0)?.trade_date,
      finalItems.at(-1)?.trade_date,
    )
    return finalItems.length
  }, [])

  const replaceBars = useCallback((next: DailyBar[], preserveView = false) => {
    if (preserveView) callbackRef.current.onBeforePreserve()
    barsRef.current = next
    callbackRef.current.onBarsChanged(next)
    setBars(next)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    callbackRef.current.onLoadStart()
    setState('loading')
    fetch(dailyBarsUrl(symbol, asOfDate), { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<{
          items: DailyBar[]
          instrument_kind?: string
          price_basis?: string | null
          rule_version?: string | null
        }>
      })
      .then(body => {
        if (body.instrument_kind) {
          callbackRef.current.onInstrumentIdentity({
            instrumentKind: body.instrument_kind,
            priceBasis: body.price_basis,
            ruleVersion: body.rule_version,
          })
        }
        replaceBars(body.items)
        setState('ready')
        const rows = publishCoverage(body.items)
        logInfo('chart', '日线数据加载完成', { symbol, rows })
      })
      .catch(error => {
        if ((error as Error).name !== 'AbortError') {
          setState('error')
          logWarning('chart', '日线数据加载失败', { symbol, error })
        }
      })
    return () => controller.abort()
  }, [asOfDate, publishCoverage, replaceBars, symbol])

  useEffect(() => () => {
    refreshControllerRef.current?.abort()
    window.clearTimeout(feedbackTimerRef.current)
  }, [symbol])

  useEffect(() => {
    const onRefreshed = (event: Event) => {
      if (asOfDate) return
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
      publishCoverage(detail.items)
    }
    window.addEventListener('stock-harness:latest-daily-refreshed', onRefreshed)
    return () => window.removeEventListener('stock-harness:latest-daily-refreshed', onRefreshed)
  }, [asOfDate, publishCoverage, replaceBars, symbol])

  const showRefreshFeedback = useCallback((
    kind: LatestDailyRefreshFeedback,
    message: string,
  ) => {
    window.clearTimeout(feedbackTimerRef.current)
    setRefreshFeedback({ kind, message })
    feedbackTimerRef.current = window.setTimeout(
      () => setRefreshFeedback(undefined),
      3_000,
    )
  }, [])

  const refreshNow = useCallback(() => {
    refreshControllerRef.current?.abort()
    const controller = new AbortController()
    refreshControllerRef.current = controller
    window.clearTimeout(feedbackTimerRef.current)
    setRefreshFeedback(undefined)
    setRefreshing(true)
    refreshLatestDailyBar(symbol, new Date(), controller.signal)
      .then(result => {
        if (result.mode === 'provisional') {
          const live = result.items[0]
          if (live) replaceBars(mergeProvisionalBar(barsRef.current, live), true)
        } else {
          replaceBars(result.items, true)
          publishCoverage(result.items)
        }
        showRefreshFeedback(result.feedback, result.message)
        const retained = result.mode === 'provisional' ? result.items.at(-1) : undefined
        const details = {
          symbol,
          mode: result.mode,
          state: result.status,
          rowsChanged: result.rowsChanged,
          error: result.error,
          provider: result.provider,
          requestedCount: result.requestedCount,
          receivedCount: result.receivedCount,
          missingCount: result.missingCount,
          missingSymbols: result.missingSymbols,
          retainedTradeDate: retained?.trade_date,
          retainedProviderTime: retained?.provider_time,
          retainedStale: retained?.stale,
        }
        if (result.warning) {
          const now = Date.now()
          if (now - warningAtRef.current >= 60_000) {
            warningAtRef.current = now
            logWarning(
              'daily-refresh',
              '最新日线刷新完成但存在警告，保留可用图表数据',
              details,
            )
          }
        } else {
          logInfo('daily-refresh', '最新日线刷新完成', details)
        }
      })
      .catch(error => {
        if ((error as Error).name !== 'AbortError') {
          showRefreshFeedback('fallback', '刷新失败，已保留现有图表数据')
          const now = Date.now()
          if (now - warningAtRef.current >= 60_000) {
            warningAtRef.current = now
            logWarning(
              'daily-refresh',
              '最新日线刷新失败，保留现有图表',
              { symbol, error },
            )
          }
        }
      })
      .finally(() => {
        if (refreshControllerRef.current === controller) {
          refreshControllerRef.current = undefined
          setRefreshing(false)
        }
      })
  }, [publishCoverage, replaceBars, showRefreshFeedback, symbol])

  const applyPolledBar = useCallback((live: DailyBar) => {
    const next = mergeProvisionalBar(barsRef.current, live)
    if (next !== barsRef.current) replaceBars(next, true)
  }, [replaceBars])
  useIntradayDailyPolling({
    symbol,
    disabled: Boolean(asOfDate),
    onBar: applyPolledBar,
  })

  return {
    bars,
    barsRef,
    state,
    refreshing,
    refreshFeedback,
    refreshNow,
  }
}
