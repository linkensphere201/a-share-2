export type SimulationSession = {
  session: string; open: number; close: number | null; atr20: number | null
  lower_limit: number; upper_limit: number; tradable: boolean; verified: boolean; market_risk_off: boolean
}
export type SimulationInput = {
  strategy_id: string
  label: string; signal_session: string; capital: number; upper: number; lower: number
  signal_close: number; atr20: number; target: number; median_amount20: number; lot_size: number
  fee_bps: number; slippage_bps: number; eligibility_assumed: boolean; sessions: SimulationSession[]
}
export type SimulationResult = {
  strategy_name: string; strategy_version: string
  strategy_id: string; input_digest: string; mode: string; input: SimulationInput; status: string
  plan: { upper: number; ceiling: number; stop: number; target: number }; planned_quantity: number
  events: { session: string; kind: string; reason: string; price: number | null; quantity: number | null }[]
  equity: { session: string; equity: number; cash: number; quantity: number; stale: boolean; stop: number | null }[]
  summary: { final_equity: number; net_return: number; realized_pnl: number | null; fees: number
    max_drawdown: number; open_quantity: number; stale_valuation: boolean; has_stale_valuation: boolean }
}

export type SimulationStrategy = {
  strategy_id: string; name: string; version: string; mode: string; scenarios: SimulationInput[]
}

async function read<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = body?.detail
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.map((item: { loc?: string[]; msg: string }) => `${item.loc?.join('.') ?? ''}: ${item.msg}`).join('；')
      : `模拟服务请求失败 (${response.status})`)
  }
  return response.json() as Promise<T>
}

export function loadSimulationScenarios(signal: AbortSignal) {
  return read<{ items: SimulationInput[] }>('/api/trade-simulation/scenarios', { signal })
}

export function loadSimulationStrategies(signal: AbortSignal) {
  return read<{ items: SimulationStrategy[] }>('/api/trade-simulation/strategies', { signal })
}

export function runTradeSimulation(input: SimulationInput, signal: AbortSignal) {
  return read<SimulationResult>('/api/trade-simulation/run', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input), signal,
  })
}
