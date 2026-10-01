type Stage = 'chart-data' | 'chart-ready'
type Aggregate = { count: number; totalMs: number; maxMs: number }
const stages: Partial<Record<Stage, Aggregate>> = {}

export function observeFrontend(stage: Stage, elapsedMs: number) {
  if (!Number.isFinite(elapsedMs) || elapsedMs < 0) return
  const aggregate = stages[stage] ??= { count: 0, totalMs: 0, maxMs: 0 }
  aggregate.count++
  aggregate.totalMs += elapsedMs
  aggregate.maxMs = Math.max(aggregate.maxMs, elapsedMs)
}

export function frontendPerformance() {
  return Object.fromEntries(Object.entries(stages).map(([key, value]) => [key, { ...value }]))
}

declare global { interface Window { __stockHarnessPerformance?: typeof frontendPerformance } }
if (typeof window !== 'undefined') window.__stockHarnessPerformance = frontendPerformance
