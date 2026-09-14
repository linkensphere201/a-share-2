export type ScreenerStrategyId = 'major-descending-breakout' | 'volume-accumulation-20d'
export type ScreenerState = 'critical-breakout' | 'breakout-retest' | 'broken-out' | 'accumulating'
export type ScreenerPeriod = '3m' | '6m' | '1y'

export type ScreenerRun = {
  run_id: string
  strategy_id: string
  strategy_version: string
  as_of_date: string
  parameters: {
    periods?: ScreenerPeriod[]
    states?: ScreenerState[]
    window?: number
    baseline_window?: number
    max_results: number
  }
  status: 'running' | 'succeeded' | 'failed'
  universe_count: number
  scanned_count: number
  candidate_count: number
  error?: string | null
  started_at_ms: number
  completed_at_ms?: number | null
}

export type ScreenerCandidate = {
  rank: number
  symbol: string
  name: string
  exchange: string
  kind: 'stock'
  state: ScreenerState
  score: number
  line_item_id: string
  line_code: string
  analysis_run_id: string
  evidence: {
    period?: ScreenerPeriod
    as_of_date: string
    latest_close?: number
    projected_price?: number
    distance_percent?: number
    invalidation_price?: number | null
    first_target_price?: number | null
    major_target_price?: number | null
    scenario_item_id?: string | null
    first_risk_reward?: number | null
    major_risk_reward?: number | null
    first_date?: string
    second_date?: string
    small_14?: { return_percent: number; recent_half_percent: number }
    medium_28?: { return_percent: number; recent_half_percent: number }
    window?: number
    window_start_date?: string
    total_volume_ratio?: number
    median_volume_ratio?: number
    elevated_sessions?: number
    supported_blocks?: number
    pile_mode?: 'distributed' | 'clustered'
    cluster_sessions?: number
    cluster_peak_ratio?: number
    cluster_average_ratio?: number
    cluster_age_sessions?: number
    dominant_session_share?: number
    return_20d_percent?: number
    close_range_20d_percent?: number
    daily_volatility_percent?: number
    up_down_volume_ratio?: number
    limit_up_count?: number
  }
}

export type ScreenerExclusionPoolEntry = {
  entry_id: number
  strategy_id: ScreenerStrategyId
  symbol: string
  name: string
  exchange: string
  kind: 'stock'
  event_date: string
  reason_code: 'limit-up' | 'large-drop'
  reason_text: string
  evidence: { change_percent?: number; threshold_percent?: number; event_date?: string }
  entered_at_ms: number
}

async function json<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>
  let detail = `HTTP ${response.status}`
  try { detail = (await response.json() as { detail?: string }).detail ?? detail } catch { /* no JSON */ }
  throw new Error(detail)
}

export async function listScreenerRuns(signal?: AbortSignal): Promise<ScreenerRun[]> {
  const payload = await json<{ items: ScreenerRun[] }>(await fetch('/api/screener/runs?limit=10', { signal }))
  return payload.items
}

export async function loadScreenerRun(runId: string, signal?: AbortSignal): Promise<ScreenerRun> {
  return json<ScreenerRun>(await fetch(`/api/screener/runs/${encodeURIComponent(runId)}`, { signal }))
}

export async function deleteScreenerRun(runId: string): Promise<void> {
  const response = await fetch(`/api/screener/runs/${encodeURIComponent(runId)}`, {
    method: 'DELETE',
  })
  if (!response.ok) await json<never>(response)
}

export async function listScreenerCandidates(runId: string, signal?: AbortSignal): Promise<ScreenerCandidate[]> {
  const payload = await json<{ items: ScreenerCandidate[] }>(
    await fetch(`/api/screener/runs/${encodeURIComponent(runId)}/candidates`, { signal }),
  )
  return payload.items
}

export async function listScreenerExclusionPool(signal?: AbortSignal): Promise<ScreenerExclusionPoolEntry[]> {
  const payload = await json<{ items: ScreenerExclusionPoolEntry[] }>(await fetch(
    '/api/screener/exclusion-pool?strategy_id=volume-accumulation-20d&limit=100', { signal },
  ))
  return payload.items
}

export async function startScreenerRun(input: {
  strategy_id: ScreenerStrategyId
  periods: ScreenerPeriod[]
  states: ScreenerState[]
  max_results: number
}): Promise<ScreenerRun> {
  return json<ScreenerRun>(await fetch('/api/screener/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  }))
}
