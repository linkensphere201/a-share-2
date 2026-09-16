export type ScreenerStrategyId = 'major-descending-breakout' | 'volume-accumulation-20d' | 'strong-first-pullback'
export type ScreenerState = 'critical-breakout' | 'breakout-retest' | 'broken-out' | 'accumulating' | 'pullback-observation' | 'pullback-confirmed'
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
    context_window?: number
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
  recognition?: {
    available: boolean
    source_run_id: string | null
    source_date: string | null
    tags: string[]
  }
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
    decline_return_percent?: number
    ma20_decline_percent?: number
    ma_divergence_percent?: number
    bottom_lift_percent?: number
    platform_range_percent?: number
    platform_return_percent?: number
    small_body_sessions?: number
    platform_volume_ratio?: number
    stage?: string
    kind?: string
    launch_date?: string
    launch_type?: string
    volume_regime?: string
    pullback_turnover_ratio?: number
    observation_window_sessions?: number
    observation_window_start_date?: string
    observation_window_end_date?: string
    flag_window?: { start_date: string; end_date: string; sessions: number; phase: string; qualified: boolean }
    peak_date?: string
    confirmation_date?: string | null
    impulse_gain_percent?: number
    pullback_depth_percent?: number
    pullback_sessions?: number
    breakout_price?: number
    pattern_type?: string
    platform_style?: string
    recognition_rank_bonus?: number
    compact_platform?: {
      qualified: boolean; score: number; reasons: string[]
      last10?: { range_percent: number; small_body_fraction: number }
    }
    demand_regime?: string
    gentle_retest?: boolean
    pullback_volume_ratio?: number
    platform_sessions?: number
    bottom_lift_atr?: number
    decline_slowing?: boolean
    average_up_down_volume_ratio?: number
    robust_up_down_volume_ratio?: number
    score_components?: { decline: number; lift: number; platform: number; demand: number }
    missing_evidence?: string[]
    dominant_session_share?: number
    return_20d_percent?: number
    close_range_20d_percent?: number
    daily_volatility_percent?: number
    up_down_volume_ratio?: number
    limit_up_count?: number
  }
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
