export type ScreenerState = 'critical-breakout' | 'breakout-retest' | 'broken-out'
export type ScreenerPeriod = '3m' | '6m' | '1y'

export type ScreenerRun = {
  run_id: string
  strategy_id: string
  strategy_version: string
  as_of_date: string
  parameters: { periods: ScreenerPeriod[]; states: ScreenerState[]; max_results: number }
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
    period: ScreenerPeriod
    as_of_date: string
    latest_close?: number
    projected_price: number
    distance_percent: number
    invalidation_price: number
    first_target_price: number
    major_target_price: number
    first_risk_reward?: number | null
    major_risk_reward?: number | null
    first_date: string
    second_date: string
    small_14: { return_percent: number; recent_half_percent: number }
    medium_28: { return_percent: number; recent_half_percent: number }
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
