export type ScreenerStrategyId = string
export type ScreenerStrategy = { strategy_id: string; name: string; version: string; states: string[] }

export async function listScreenerStrategies(signal?: AbortSignal): Promise<ScreenerStrategy[]> {
  const payload = await json<{ items: ScreenerStrategy[] }>(await fetch('/api/screener/strategies', { signal }))
  return payload.items
}
export type ScreenerState = 'critical-breakout' | 'breakout-retest' | 'broken-out' | 'accumulating' | 'pullback-observation' | 'pullback-confirmed' | 'shape-match'
export type ScreenerPeriod = '3m' | '6m' | '1y'

export type ScreenerRun = {
  run_id: string
  strategy_id: string
  strategy_version: string
  as_of_date: string
  parameters: {
    batch_id?: string
    periods?: ScreenerPeriod[]
    states?: ScreenerState[]
    window?: number
    baseline_window?: number
    context_window?: number
    max_results: number
  }
  status: 'running' | 'succeeded' | 'failed'
  execution_state?: 'queued' | 'running'
  queue_position?: number
  universe_count: number
  scanned_count: number
  candidate_count: number
  error?: string | null
  started_at_ms: number
  completed_at_ms?: number | null
}

export type ScreenerCandidate = {
  evidence_complete?: boolean
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
    platform_end_date?: string
    breakout_age_sessions?: number
    breakout_volume_ratio?: number
    retest_breakout_volume_ratio?: number
    breakout_distance_percent?: number
    upper_touch_count?: number
    lower?: number
    upper?: number
    robust_pullback_volume_ratio?: number
    retained_advance_fraction?: number
    retention_events?: number
    median_volume_expansion?: number
    robust_volume_expansion?: number
    volume_persistence?: number
    platform_type?: 'low-base' | 'continuation' | 'neutral'
    platform_stage?: 'near-upper' | 'tightening'
    recent_range_percent?: number
    recent_history_volume_ratio?: number
    floor_lift_percent?: number
    range_position?: number
    ma60_slope_percent?: number | null
    ma240_slope_percent?: number | null
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
    price_basis?: string
    price_correlation?: number
    recent_correlation?: number
    return_60d_percent?: number
    max_drawdown_percent?: number
    recent_early_volume_ratio?: number
    reference?: { symbol: string; start_date: string; end_date: string }
    similarity_components?: { price_path: number; recent_path: number; amplitude: number; volume_path: number }
    launch_date?: string
    launch_type?: string
    start_date?: string
    end_date?: string
    launch_age_sessions?: number
    pole_sessions?: number
    flag_sessions?: number
    pole_low?: number
    center_drift_percent?: number
    flag_range_percent?: number
    flag_pole_volume_ratio?: number
    late_early_volume_ratio?: number
    rolling_contraction_fraction?: number
    shape_maturity?: 'forming' | 'platform-established' | 'platform-retest'
    platform_shape?: { start_date: string; end_date: string; sessions: number; stable: boolean; mature: boolean;
      close_drift_percent: number; small_body_fraction: number; volume_quality: string }
    origin_above_context_low_percent?: number
    pullback_platform_volume_ratio?: number
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

export type ScreenerBatch = {
  batch_id: string
  as_of_date: string
  items: ScreenerRun[]
  skipped: { strategy_id: string; reason: 'already-running' | 'cutoff-unavailable' | 'creation-failed' }[]
}

export async function startScreenerBatch(maxResults: number): Promise<ScreenerBatch> {
  return json<ScreenerBatch>(await fetch('/api/screener/batches', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_results: maxResults }),
  }))
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
  const items: ScreenerCandidate[] = []
  for (let offset = 0; offset < 10000;) {
    const payload = await json<{ items: ScreenerCandidate[]; has_more?: boolean }>(
      await fetch(`/api/screener/runs/${encodeURIComponent(runId)}/candidates?limit=200&offset=${offset}&summary=true`, { signal }),
    )
    items.push(...payload.items)
    if (!payload.has_more || !payload.items.length) break
    offset += payload.items.length
  }
  return items
}

export async function loadScreenerCandidate(runId: string, rank: number, signal?: AbortSignal): Promise<ScreenerCandidate> {
  return json<ScreenerCandidate>(await fetch(`/api/screener/runs/${encodeURIComponent(runId)}/candidates/${rank}`, { signal }))
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
