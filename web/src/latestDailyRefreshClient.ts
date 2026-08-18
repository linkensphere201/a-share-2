import { shouldUseFinalDailyRefresh, type DailyBar } from './chartData'

export type LatestDailyRefreshResult = {
  mode: 'provisional' | 'canonical' | 'final'
  items: DailyBar[]
  warning: boolean
  status: string
  error?: string
  rowsChanged?: number
}

type FinalDailyUpdateStatus = {
  state: string
  rows_changed?: number
  error?: string | null
}

export async function refreshLatestDailyBar(
  symbol: string,
  now = new Date(),
  signal?: AbortSignal,
): Promise<LatestDailyRefreshResult> {
  if (shouldUseFinalDailyRefresh(now)) {
    const request = await fetch('/api/update/refresh', { method: 'POST', signal })
    if (!request.ok) throw new Error(`HTTP ${request.status}`)
    const status = await waitForFinalDailyUpdate(signal)
    const items = await loadDailyBars(symbol, signal)
    return {
      mode: 'final',
      items,
      warning: status.state === 'warning' || status.state === 'error',
      status: status.state,
      error: status.error ?? undefined,
      rowsChanged: status.rows_changed,
    }
  }
  const response = await fetch('/api/intraday/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbols: [symbol] }),
    signal,
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const body = await response.json() as {
    items: DailyBar[]
    canonical_symbols?: string[]
    status: { state: string; last_error?: string }
  }
  if (body.canonical_symbols?.some(item => item.toUpperCase() === symbol.toUpperCase())) {
    return {
      mode: 'canonical',
      items: await loadDailyBars(symbol, signal),
      warning: false,
      status: body.status.state,
    }
  }
  return {
    mode: 'provisional',
    items: body.items,
    warning: body.status.state !== 'ready' || body.items.length === 0,
    status: body.status.state,
    error: body.status.last_error,
  }
}

async function loadDailyBars(symbol: string, signal?: AbortSignal): Promise<DailyBar[]> {
  const response = await fetch(
    `/api/instruments/${encodeURIComponent(symbol)}/daily-bars`, { signal },
  )
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const body = await response.json() as { items: DailyBar[] }
  return body.items
}

async function waitForFinalDailyUpdate(
  signal?: AbortSignal,
): Promise<FinalDailyUpdateStatus> {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    const response = await fetch('/api/update-status', { signal })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const status = await response.json() as FinalDailyUpdateStatus
    if (status.state !== 'queued' && status.state !== 'running') return status
    await new Promise(resolve => window.setTimeout(resolve, 1_000))
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
  }
  throw new Error('盘后正式日线刷新超时')
}
