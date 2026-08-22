import type { GeneratedBreakoutState } from './generatedAnalysisProjection'
import type { TrendAnalysisRun } from './trendAnalysisClient'

export type TrendEvidenceView = {
  source: 'official' | 'preview'
  asOfDate: string
  observedAt?: string
  timeframe: string
  stale: boolean
  state?: string
  patternName?: string
  score?: number
  scoreComponents: Array<{ label: string; value: number }>
  boundaryPrice?: number
  invalidationPrice?: number
  observation: Array<{ label: string; value: string }>
  context?: { alignment: string; score: number; availableCount: number }
  warnings: string[]
}

export function readTrendEvidence(
  run: TrendAnalysisRun | null | undefined,
  breakoutState?: GeneratedBreakoutState,
): TrendEvidenceView | undefined {
  if (!run) return undefined
  const patterns = run.items.filter(item => item.item_type === 'pattern')
  const primary = patterns.find(item => item.payload.primary === true) ?? patterns[0]
  const eventEvidence = run.items.find(item => (
    item.item_type === 'evidence'
    && item.payload.kind === 'latest-structural-event-summary'
    && item.payload.current_state !== 'ready'
  )) ?? run.items.find(item => (
    item.item_type === 'evidence'
    && item.payload.kind === 'breakout-state-summary'
    && item.parent_item_id === primary?.item_id
  ))
  const evidence = record(eventEvidence?.payload.evidence)
  const contextItem = run.items.find(item => (
    item.item_id === 'market-board-context-evidence'
  ))
  const context = contextItem?.payload
  const components = record(primary?.payload.score_components)
  const warnings = [
    ...run.stale_reasons,
    ...run.warnings.map(item => warningText(item)),
  ].filter((item, index, values) => item && values.indexOf(item) === index)
  return {
    source: run.source_observed_at_ms ? 'preview' : 'official',
    asOfDate: run.as_of_date,
    observedAt: run.source_observed_at_ms
      ? new Date(run.source_observed_at_ms).toISOString()
      : undefined,
    timeframe: stringValue(primary?.payload.timeframe) ?? 'daily',
    stale: run.stale,
    state: breakoutState?.state,
    patternName: stringValue(primary?.payload.display_name),
    score: numberValue(primary?.payload.score),
    scoreComponents: Object.entries(components).flatMap(([label, value]) => (
      typeof value === 'number' ? [{ label, value }] : []
    )),
    boundaryPrice: breakoutState?.boundaryPrice,
    invalidationPrice: breakoutState?.invalidationPrice,
    observation: [
      observationRow('距离', evidence.distance_percent, true),
      observationRow('相对成交量', evidence.relative_volume, false),
      observationRow('振幅扩张', evidence.range_expansion, false),
      observationRow('收盘位置', evidence.close_location, true),
      observationRow('突破前收敛', evidence.pre_breakout_contraction, false),
      observationRow('逆向影线', evidence.adverse_wick_ratio, true),
    ].filter((item): item is { label: string; value: string } => item !== undefined),
    context: (
      typeof context?.alignment === 'string'
      && typeof context?.combined_score === 'number'
      && typeof context?.available_context_count === 'number'
    ) ? {
        alignment: context.alignment,
        score: context.combined_score,
        availableCount: context.available_context_count,
      } : undefined,
    warnings,
  }
}

function observationRow(
  label: string, value: unknown, percent: boolean,
): { label: string; value: string } | undefined {
  if (typeof value !== 'number') return undefined
  return {
    label,
    value: percent ? `${(value * 100).toFixed(1)}%` : value.toFixed(2),
  }
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function warningText(value: Record<string, unknown>): string {
  return stringValue(value.message)
    ?? stringValue(value.code)
    ?? JSON.stringify(value)
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined
}
