import type { TrendTradingSystemSettings } from './tradingSystems'

export type GeneratedAnalysisItem = {
  item_id: string
  item_type: 'anchor' | 'line' | 'zone' | 'pattern' | 'transition' | 'evidence' | 'scenario'
  parent_item_id?: string | null
  payload: Record<string, unknown>
}

export type TrendAnalysisRun = {
  run_id: string
  as_of_date: string
  completion_state: string
  algorithm_version?: string
  config_version?: string
  input_digest?: string
  source_observed_at_ms?: number | null
  expires_at_ms?: number | null
  stale: boolean
  stale_reasons: string[]
  warnings: Array<Record<string, unknown>>
  items: GeneratedAnalysisItem[]
}

export type TrendAnalysisSnapshot = {
  symbol: string
  timeframe: string
  official: TrendAnalysisRun | null
  preview: TrendAnalysisRun | null
  preview_expired: boolean
  effective: TrendAnalysisRun | null
}

export type TrendAnalysisRunSummary = {
  run_id: string
  namespace: 'official' | 'preview'
  as_of_date: string
  algorithm_version: string
  config_version: string
  completion_state: string
  preview: boolean
  created_at_ms: number
  completed_at_ms: number
  item_count: number
}

export async function recalculateTrendAnalysis(
  symbol: string,
  settings: TrendTradingSystemSettings,
  settingsRevision: number,
): Promise<unknown> {
  const timeframes = [
    settings.dailyEnabled && 'daily',
    settings.weeklyEnabled && 'weekly',
    settings.monthlyEnabled && 'monthly',
  ].filter((item): item is string => Boolean(item))
  const configVersion = [
    `workspace-r${settingsRevision}`,
    settings.shortHorizonBars,
    settings.mediumHorizonBars,
    settings.longHorizonBars,
    Number(settings.provisionalPreview),
    Number(settings.showTentativePivots),
  ].join('-')
  const response = await fetch('/api/analysis/trend/recalculate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      symbol,
      timeframes,
      short_horizon_bars: settings.shortHorizonBars,
      medium_horizon_bars: settings.mediumHorizonBars,
      long_horizon_bars: settings.longHorizonBars,
      config_version: configVersion,
      include_preview: settings.provisionalPreview,
    }),
  })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const payload = await response.json() as { detail?: string }
      if (payload.detail) detail = payload.detail
    } catch {
      // Keep the HTTP status when the backend did not return JSON.
    }
    throw new Error(detail)
  }
  return response.json()
}

export async function loadTrendAnalysis(
  symbol: string,
  timeframe = 'daily',
  signal?: AbortSignal,
): Promise<TrendAnalysisSnapshot> {
  const response = await fetch(
    `/api/analysis/trend/${encodeURIComponent(symbol)}?timeframe=${encodeURIComponent(timeframe)}`,
    { signal },
  )
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<TrendAnalysisSnapshot>
}

export async function loadExactTrendAnalysis(
  runId: string,
  signal?: AbortSignal,
): Promise<TrendAnalysisRun> {
  const response = await fetch(`/api/analysis/runs/${encodeURIComponent(runId)}`, { signal })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<TrendAnalysisRun>
}

export async function loadTrendAnalysisRuns(
  symbol: string, timeframe = 'daily', signal?: AbortSignal,
): Promise<TrendAnalysisRunSummary[]> {
  const response = await fetch(
    `/api/analysis/trend/${encodeURIComponent(symbol)}/runs?timeframe=${encodeURIComponent(timeframe)}`,
    { signal },
  )
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return ((await response.json()) as { items: TrendAnalysisRunSummary[] }).items
}
