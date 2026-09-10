export type SignalProfile = 'recent' | 'historical' | 'market' | 'attention'
export type SignalChangeType = 'added' | 'retained' | 'removed'
export type SignalStateTransition = 'new' | 'unchanged' | 'strengthened' | 'weakened' | 'changed' | 'invalidated'

export type SignalDefinition = {
  signal_id: string
  name: string
  description: string
  cadence: 'daily' | 'weekly'
  definition_version: string
  algorithm_version: string
  manual_only: boolean
  profiles: SignalProfile[]
  observation_systems?: Array<{
    system_id: string
    version: string
    entity_scope: string
    display_name: string
    dependencies: string[]
    score_combination: 'independent'
  }>
}

export type SignalRun = {
  run_id: string
  signal_id: string
  definition_version: string
  algorithm_version: string
  cadence: string
  effective_date: string
  revision: number
  prior_run_id?: string | null
  status: 'running' | 'succeeded' | 'failed'
  phase: string
  work_total: number
  work_done: number
  item_count: number
  added_count: number
  retained_count: number
  removed_count: number
  summary: Record<string, unknown>
  error?: string | null
  started_at_ms: number
  completed_at_ms?: number | null
}

export type SignalEvidence = {
  evidence_id: string
  alias: string
  evidence_type: string
  source_run_id?: string | null
  source_item_id?: string | null
  payload: {
    board_symbol?: string
    board_name?: string
    board_classification?: string
    rank?: number
    score?: number
    confidence?: number
  }
}

export type SignalItem = {
  item_id: string
  item_key: string
  rank: number
  symbol: string
  name: string
  kind: string
  exchange: string
  profile: SignalProfile
  change_type: SignalChangeType
  active: boolean
  score: number
  confidence: number
  payload: {
    board_count?: number
    rank_one_count?: number
    board_names?: string[]
    conclusion_code?: string
    state_codes?: string[]
    attention_reasons?: string[]
    rendered_summary?: string
    metrics?: Record<string, unknown>
    effective_date?: string
    comparison?: Record<string, unknown>
    deep_analysis_state?: string
    deep_analysis_run_id?: string | null
    score_result?: SignalScoreResult
  }
  evidence: SignalEvidence[]
}

export type BoardDailyObservation = {
  run_id: string
  symbol: string
  name: string
  exchange: string
  effective_date: string
  coverage_state: 'complete' | 'insufficient'
  state_codes: string[]
  metrics: Record<string, unknown>
  disqualifiers: string[]
  attention_reasons: string[]
  attention_eligible: boolean
  conclusion_code: string
  rendered_summary: string
  comparison: {
    transition?: SignalStateTransition
    prior_run_id?: string | null
    prior_effective_date?: string | null
    correction_baseline?: {
      run_id?: string | null
      effective_date?: string | null
      conclusion_code?: string | null
    } | null
    shape?: Record<string, SignalStateTransition>
    price?: SignalStateTransition
    volume?: SignalStateTransition
    recent_sessions?: Array<Record<string, unknown>>
  }
  deep_analysis_state: string
  deep_analysis_run_id?: string | null
  deep_analysis_summary?: Record<string, unknown>
  input_digest: string
  algorithm_version: string
  config_version: string
}

export type SignalAttention = {
  signal_id: string
  symbol: string
  name: string
  exchange: string
  status: 'manual-pinned' | 'auto-promoted' | 'cooldown' | 'inactive'
  manual_pinned: boolean
  first_observed_date: string
  last_observed_date: string
  cooldown_through_date?: string | null
  reasons: string[]
}

export type SignalHardEvent = {
  event_type: string
  direction: 'up' | 'down' | 'neutral'
  severity: 'high' | 'medium' | 'low'
  state: 'new' | 'continuing' | 'confirmed' | 'weakened' | 'resolved' | 'invalidated'
  source_code: string
}

export type SignalScoreResult = {
  run_id: string
  entity_key: string
  symbol: string
  name: string
  kind: string
  exchange: string
  effective_date: string
  system_id: string
  scorer_version: string
  entity_scope: string
  eligible: boolean
  total_score: number
  grade: 'S' | 'A' | 'B' | 'C' | 'D'
  rank: number
  participant_count: number
  eligible_count: number
  verdict: string
  summary: string
  risk_summary: string
  change_summary: string
  hotspot_stage?: string
  setup_path?: string
  radar_visible?: boolean
  radar_rank?: number | null
  radar_slot_limit?: number
  market_liquidity_regime?: string
  market_liquidity_ratio?: number | null
  market_liquidity_source?: string
  market_liquidity_version?: string
  market_turnover_5_median?: number | null
  market_turnover_20_median?: number | null
  market_liquidity_capacity?: string
  market_liquidity_direction?: string
  market_liquidity_raw_seats?: number
  market_liquidity_seat_streak?: number
  board_capacity_version?: string
  board_capacity_tier?: 'micro' | 'small' | 'medium' | 'large' | 'mega' | 'unknown'
  board_turnover_capacity_20?: number | null
  board_turnover_intensity?: number | null
  board_capacity_member_count?: number
  board_capacity_coverage_ratio?: number | null
  board_capacity_concentration_hhi?: number | null
  capacity_compatible?: boolean
  capacity_market_preferred?: boolean
  capacity_fit_score?: number
  visibility_score?: number
  capacity_fit_reasons?: string[]
  theme_registry_version?: string
  theme_name?: string
  theme_parent_id?: string | null
  theme_parent_name?: string | null
  theme_match_method?: 'explicit-alias' | 'canonical-name-fallback' | string
  theme_signal_eligible?: boolean
  score_direction?: 'new' | 'strengthening' | 'stable' | 'declining'
  score_delta?: number
  peak_score?: number
  drawdown_from_peak?: number
  candidate_streak?: number
  raw_score?: number
  limit_up_count?: number
  broken_up_count?: number
  max_limit_up_streak?: number
  stressed_risk_reward?: number | null
  components: Record<string, number>
  penalties: Array<{ code: string; points: number }>
  disqualifiers: string[]
  hard_events: SignalHardEvent[]
  history: Array<{
    run_id?: string
    entity_key?: string
    effective_date?: string
    total_score?: number
    grade?: string
    rank?: number
    eligible?: boolean
  }>
}

export type ObservationPoolSource = {
  source_type: string
  source_reference: string
  source_entity_key: string
  reason: string
  payload: Record<string, unknown>
}

export type ObservationPoolItem = {
  symbol: string
  name: string
  kind: string
  exchange: string
  lifecycle_state: 'new' | 'active' | 'strengthened' | 'weakened' | 'invalidated' | 'cooldown' | 'manual-pinned'
  rank: number
  payload: {
    trend_score?: number | null
    trend_grade?: string | null
    trend_eligible?: boolean
    hotspot_score?: number | null
    hotspot_grade?: string | null
    hotspot_eligible?: boolean
    hotspot_stage?: string | null
    hotspot_direction?: string | null
    hotspot_peak_score?: number | null
    recognition_assignment_count?: number
    recognized?: boolean
    independent_score?: number
    manual_pinned?: boolean
    source_types?: string[]
    member_scan?: Record<string, unknown> | null
    independent_scan?: Record<string, unknown> | null
    screener_results?: Array<Record<string, unknown>>
    m4_analysis?: {
      state?: string
      run_id?: string
      status?: string
      scenario?: Record<string, unknown>
      core_item_ids?: string[]
      warning_count?: number
    }
    opportunity_classification?: {
      classification?: string
      recognition_state?: string
      independent_strength_eligible?: boolean
      opportunity_state?: string
      opportunity_eligible?: boolean
      credible_target_count?: number
    }
    opportunity_score?: SignalScoreResult
    presentation_bucket?: 'opportunity' | 'focus' | 'risk' | 'archive'
    presentation_rank?: number
    presentation_reasons?: string[]
    presentation_version?: string
    presentation_lane?: string
  }
  sources: ObservationPoolSource[]
}

export type ObservationPoolSnapshot = {
  source_run_id: string
  pool_kind: 'board' | 'stock'
  effective_date: string
  algorithm_version: string
  summary: Record<string, unknown>
  created_at_ms: number
  items: ObservationPoolItem[]
}

async function json<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>
  let detail = `HTTP ${response.status}`
  try { detail = (await response.json() as { detail?: string }).detail ?? detail } catch { /* no JSON */ }
  throw new Error(detail)
}

export async function listSignalDefinitions(signal?: AbortSignal): Promise<SignalDefinition[]> {
  return (await json<{ items: SignalDefinition[] }>(
    await fetch('/api/signals/definitions', { signal }),
  )).items
}

export async function listSignalRuns(signalId: string, signal?: AbortSignal): Promise<SignalRun[]> {
  const query = new URLSearchParams({ signal_id: signalId, limit: '50' })
  return (await json<{ items: SignalRun[] }>(
    await fetch(`/api/signals/runs?${query}`, { signal }),
  )).items
}

export async function loadSignalRun(runId: string, signal?: AbortSignal): Promise<SignalRun> {
  return json<SignalRun>(await fetch(`/api/signals/runs/${encodeURIComponent(runId)}`, { signal }))
}

export async function listSignalItems(runId: string, signal?: AbortSignal): Promise<SignalItem[]> {
  return (await json<{ items: SignalItem[] }>(
    await fetch(`/api/signals/runs/${encodeURIComponent(runId)}/items`, { signal }),
  )).items
}

export async function listSignalScores(
  runId: string, signal?: AbortSignal,
): Promise<SignalScoreResult[]> {
  const pageSize = 5000
  const base = `/api/signals/runs/${encodeURIComponent(runId)}/scores`
  const first = await json<{ items: SignalScoreResult[]; total: number }>(
    await fetch(base, { signal }),
  )
  if (!Number.isFinite(first.total) || first.items.length >= first.total) return first.items
  const offsets = []
  for (let offset = first.items.length; offset < first.total; offset += pageSize) {
    offsets.push(offset)
  }
  const pages = await Promise.all(offsets.map(async offset =>
    json<{ items: SignalScoreResult[] }>(
      await fetch(`${base}?limit=${pageSize}&offset=${offset}`, { signal }),
    ),
  ))
  return first.items.concat(...pages.map(page => page.items))
}

export async function loadObservationPool(
  runId: string, poolKind: 'board' | 'stock', signal?: AbortSignal,
): Promise<ObservationPoolSnapshot> {
  return json<ObservationPoolSnapshot>(await fetch(
    `/api/observation-pools/runs/${encodeURIComponent(runId)}/${poolKind}`, { signal },
  ))
}

export async function startSignalRun(signalId: string): Promise<SignalRun> {
  return json<SignalRun>(await fetch(`/api/signals/${encodeURIComponent(signalId)}/runs`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  }))
}

export async function listBoardObservations(
  runId: string, query = '', signal?: AbortSignal,
): Promise<{ items: BoardDailyObservation[]; total: number }> {
  const params = new URLSearchParams({ limit: '5000' })
  if (query.trim()) params.set('query', query.trim())
  return json<{ items: BoardDailyObservation[]; total: number }>(await fetch(
    `/api/signals/runs/${encodeURIComponent(runId)}/board-observations?${params}`, { signal },
  ))
}

export async function listSignalAttention(
  signalId: string, signal?: AbortSignal,
): Promise<SignalAttention[]> {
  return (await json<{ items: SignalAttention[] }>(await fetch(
    `/api/signals/${encodeURIComponent(signalId)}/attention`, { signal },
  ))).items
}

export async function setSignalAttention(
  signalId: string, symbol: string, manualPinned: boolean,
): Promise<SignalAttention> {
  return json<SignalAttention>(await fetch(
    `/api/signals/${encodeURIComponent(signalId)}/attention/${encodeURIComponent(symbol)}`,
    {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ manual_pinned: manualPinned }),
    },
  ))
}
