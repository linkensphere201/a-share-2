export type SignalProfile = 'recent' | 'historical'
export type SignalChangeType = 'added' | 'retained' | 'removed'

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
  payload: { board_count?: number; rank_one_count?: number; board_names?: string[] }
  evidence: SignalEvidence[]
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

export async function startSignalRun(signalId: string): Promise<SignalRun> {
  return json<SignalRun>(await fetch(`/api/signals/${encodeURIComponent(signalId)}/runs`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  }))
}
