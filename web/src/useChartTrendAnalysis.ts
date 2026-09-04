import { useEffect, useMemo, useState } from 'react'
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
  const [analysis, setAnalysis] = useState<TrendAnalysisRun | null>(null)
  const [preview, setPreview] = useState(false)

  useEffect(() => {
    if (override !== undefined && override !== null) {
      setAnalysis(override)
      setPreview(false)
      return
    }
    if (!enabled) {
      setAnalysis(null)
      setPreview(false)
      return
    }
    let controller: AbortController | undefined
    const reload = () => {
      controller?.abort()
      controller = new AbortController()
      void loadTrendAnalysis(symbol, 'daily', controller.signal).then(snapshot => {
        setAnalysis(snapshot.effective)
        setPreview(
          snapshot.effective !== null
          && snapshot.effective.run_id === snapshot.preview?.run_id
        )
      }).catch(error => {
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
  }, [enabled, override, symbol])

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
