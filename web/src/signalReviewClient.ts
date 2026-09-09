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
  return (await json<{ items: SignalScoreResult[] }>(
    await fetch(`/api/signals/runs/${encodeURIComponent(runId)}/scores`, { signal }),
  )).items
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
