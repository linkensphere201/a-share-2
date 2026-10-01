import { useEffect, useMemo, useRef, useState } from 'react'
import { logWarning } from './eventLogger'
import {
  loadTrendAnalysis,
  type GeneratedAnalysisItem,
  type TrendAnalysisRun,
} from './trendAnalysisClient'

type ChartTrendAnalysisOptions = {
  symbol: string
  enabled: boolean
  override?: TrendAnalysisRun | null
  supplementalItems?: GeneratedAnalysisItem[]
  supplementalOnly?: boolean
}

export function useChartTrendAnalysis({
  symbol,
  enabled,
  override,
  supplementalItems = [],
  supplementalOnly = false,
}: ChartTrendAnalysisOptions) {
  const owner = useMemo(() => ({ symbol }), [symbol, enabled, override])
  const ownerRef = useRef(owner)
  ownerRef.current = owner
  const [result, setResult] = useState<{
    owner: typeof owner; analysis: TrendAnalysisRun | null; preview: boolean
  }>()
  const analysis = override ?? (enabled && result?.owner === owner ? result.analysis : null)
  const preview = !override && enabled && result?.owner === owner ? result.preview : false

  useEffect(() => {
    if (override !== undefined && override !== null) {
      return
    }
    if (!enabled) {
      return
    }
    let controller: AbortController | undefined
    const reload = () => {
      controller?.abort()
      const request = new AbortController()
      controller = request
      const current = () => !request.signal.aborted && controller === request && ownerRef.current === owner
      void loadTrendAnalysis(symbol, 'daily', request.signal).then(snapshot => {
        if (!current()) return
        setResult({ owner, analysis: snapshot.effective,
          preview: snapshot.effective !== null && snapshot.effective.run_id === snapshot.preview?.run_id })
      }).catch(error => {
        if (!current()) return
        if (error instanceof DOMException && error.name === 'AbortError') return
        logWarning('trading-system', '趋势分析结果读取失败', {
          symbol,
          error: error instanceof Error ? error.message : String(error),
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
  }, [enabled, override, symbol, owner])

  const displayed = useMemo(() => (
    analysis && supplementalItems.length > 0
      ? {
        ...analysis,
        items: supplementalOnly
          ? supplementalItems
          : [...analysis.items, ...supplementalItems],
      }
      : analysis
  ), [analysis, supplementalItems, supplementalOnly])

  return { analysis, displayed, preview }
}
