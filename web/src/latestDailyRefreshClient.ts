import { shouldUseFinalDailyRefresh, type DailyBar } from './chartData'

export type LatestDailyRefreshResult = {
  mode: 'provisional' | 'canonical' | 'final'
  items: DailyBar[]
  warning: boolean
  status: string
  feedback: 'success' | 'stale' | 'fallback' | 'canonical' | 'skipped'
  message: string
  error?: string
  rowsChanged?: number
  provider?: string
  requestedCount?: number
  receivedCount?: number
  missingCount?: number
  missingSymbols?: string[]
}
export type LatestDailyRefreshFeedback = LatestDailyRefreshResult['feedback']

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
  const futures = isFuturesSymbol(symbol)
  if (!futures && shouldUseFinalDailyRefresh(now)) {
    const request = await fetch('/api/update/refresh', { method: 'POST', signal })
    if (!request.ok) throw new Error(`HTTP ${request.status}`)
    const status = await waitForFinalDailyUpdate(signal)
    const items = await loadDailyBars(symbol, signal)
    return {
      mode: 'final',
      items,
      warning: status.state === 'warning' || status.state === 'error',
      status: status.state,
      feedback: status.state === 'warning' || status.state === 'error' ? 'fallback' : 'success',
      message: status.state === 'warning' || status.state === 'error'
        ? '正式日线更新有警告，已保留可用数据'
        : '正式日线更新完成',
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
    status: {
      state: string
      last_error?: string
      provider?: string
      last_requested_count?: number
      last_received_count?: number
      last_missing_count?: number
      last_missing_symbols?: string[]
    }
    futures?: {
      mode?: 'provisional' | 'final'
      state: string
      skip_reason?: string | null
      last_error?: string | null
      received?: number
      received_count?: number
      stale_count?: number
      provider?: string
    }
  }
  if (futures) return futuresRefreshResult(symbol, body.futures, signal)
  if (body.canonical_symbols?.some(item => item.toUpperCase() === symbol.toUpperCase())) {
    return {
      mode: 'canonical',
      items: await loadDailyBars(symbol, signal),
      warning: false,
      status: body.status.state,
      feedback: 'canonical',
      message: '主库正式日线已接管当日数据',
    }
  }
  return {
    mode: 'provisional',
    items: body.items,
    warning: body.status.state !== 'ready' || body.items.length === 0,
    status: body.status.state,
    feedback: body.status.state === 'ready' && body.items.length > 0 ? 'success' : 'fallback',
    message: body.status.state === 'ready' && body.items.length > 0
      ? '盘中临时日线刷新完成'
      : '未获得新盘中数据，已保留现有数据',
    error: body.status.last_error,
    provider: body.status.provider,
    requestedCount: body.status.last_requested_count,
    receivedCount: body.status.last_received_count,
    missingCount: body.status.last_missing_count,
    missingSymbols: body.status.last_missing_symbols,
  }
}

async function futuresRefreshResult(
  symbol: string,
  status: {
    mode?: 'provisional' | 'final'
    state: string
    skip_reason?: string | null
    last_error?: string | null
    received?: number
    received_count?: number
    stale_count?: number
    provider?: string
  } | undefined,
  signal?: AbortSignal,
): Promise<LatestDailyRefreshResult> {
  if (!status) throw new Error('期货刷新状态缺失')
  if (status.mode === 'final') {
    const finalStatus = await waitForFinalDailyUpdate(signal)
    return {
      mode: 'final',
      items: await loadDailyBars(symbol, signal),
      warning: finalStatus.state === 'warning' || finalStatus.state === 'error',
      status: finalStatus.state,
      feedback: finalStatus.state === 'warning' || finalStatus.state === 'error'
        ? 'fallback' : 'canonical',
      message: finalStatus.state === 'warning' || finalStatus.state === 'error'
        ? '期货正式日线更新有警告，已保留可用数据'
        : '期货主库正式日线已接管',
      error: finalStatus.error ?? undefined,
      rowsChanged: finalStatus.rows_changed,
    }
  }

  const items = await loadDailyBars(symbol, signal)
  const latest = items.at(-1)
  const received = status.received_count ?? status.received ?? 0
  if (status.state === 'ready' && received > 0 && latest?.bar_state !== 'intraday') {
    return {
      mode: 'canonical', items, warning: false, status: status.state,
      feedback: 'canonical', message: '期货主库正式日线已覆盖当日临时数据',
    }
  }
  if (status.state === 'error' || status.state === 'partial' || status.state === 'empty') {
    return {
      mode: 'provisional', items: latestProvisional(items, true), warning: true, status: status.state,
      feedback: 'fallback', message: '期货行情源未完整返回，继续展示最后可用数据',
      error: status.last_error ?? undefined,
    }
  }
  if (status.state === 'stale' || (status.stale_count ?? 0) > 0 || latest?.stale) {
    return {
      mode: 'provisional', items: latestProvisional(items, true), warning: true, status: status.state,
      feedback: 'stale', message: '期货行情时间陈旧，继续展示最后可用临时日线',
    }
  }
  if (status.state === 'skipped') {
    const expected = status.skip_reason === 'market-closed'
    return {
      mode: 'provisional', items: latestProvisional(items), warning: !expected, status: status.state,
      feedback: 'skipped',
      message: expected ? '当前不在期货交易时段，未发起盘中刷新' : skipReasonMessage(status.skip_reason),
    }
  }
  return {
    mode: 'provisional', items: latestProvisional(items),
    warning: status.state !== 'ready' || received === 0,
    status: status.state,
    feedback: status.state === 'ready' && received > 0 ? 'success' : 'fallback',
    message: status.state === 'ready' && received > 0
      ? '期货盘中临时日线刷新完成'
      : '未获得新期货行情，继续展示最后可用数据',
    error: status.last_error ?? undefined,
  }
}

function latestProvisional(items: DailyBar[], stale = false): DailyBar[] {
  const latest = items.at(-1)
  return latest?.bar_state === 'intraday' ? [{ ...latest, stale: stale || latest.stale }] : []
}

function isFuturesSymbol(symbol: string): boolean {
  return symbol.toUpperCase().startsWith('FUT:')
    || symbol.toUpperCase().startsWith('FUTCONT:')
}

function skipReasonMessage(reason?: string | null): string {
  if (reason === 'calendar-unavailable') return '\u671f\u8d27\u4ea4\u6613\u65e5\u5386\u5c1a\u672a\u5c31\u7eea\uff0c\u5df2\u4fdd\u7559\u73b0\u6709\u6570\u636e'
  if (reason === 'mapping-ambiguous') return '主力连续映射存在歧义，未覆盖现有数据'
  if (reason === 'unresolved') return '未解析到可刷新的期货合约，已保留现有数据'
  if (reason === 'disabled') return '期货盘中行情未启用'
  return '期货盘中刷新已跳过，保留现有数据'
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
