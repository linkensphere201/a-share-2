import {
  refreshLatestDailyBar,
  type LatestDailyRefreshResult,
} from './latestDailyRefreshClient'
import { recalculateTrendAnalysis } from './trendAnalysisClient'
import type { TrendTradingSystemSettings } from './tradingSystems'

type TrendRefreshCallbacks = {
  onRefresh?: (result: LatestDailyRefreshResult) => void
  onRefreshError?: (error: unknown) => void
}

type TrendRefreshDependencies = {
  refresh: typeof refreshLatestDailyBar
  recalculate: typeof recalculateTrendAnalysis
}

export type TrendRefreshOutcome = {
  refresh?: LatestDailyRefreshResult
  refreshError?: unknown
  analysis: unknown
}

const defaultDependencies: TrendRefreshDependencies = {
  refresh: refreshLatestDailyBar,
  recalculate: recalculateTrendAnalysis,
}

export async function refreshThenRecalculateTrend(
  symbol: string,
  settings: TrendTradingSystemSettings,
  settingsRevision: number,
  callbacks: TrendRefreshCallbacks = {},
  dependencies: TrendRefreshDependencies = defaultDependencies,
): Promise<TrendRefreshOutcome> {
  let refresh: LatestDailyRefreshResult | undefined
  let refreshError: unknown
  try {
    refresh = await dependencies.refresh(symbol)
    callbacks.onRefresh?.(refresh)
  } catch (error) {
    refreshError = error
    callbacks.onRefreshError?.(error)
  }
  const analysis = await dependencies.recalculate(
    symbol, settings, settingsRevision,
  )
  return { refresh, refreshError, analysis }
}
