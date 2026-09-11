import type { BoardDailyObservation, SignalItem } from './signalReviewClient'
import type { GeneratedAnalysisItem, TrendAnalysisRun } from './trendAnalysisClient'

const PREFIX = 'daily-review'

export type DailyConclusionSource = {
  runId: string
  effectiveDate: string
  stateCodes: string[]
  metrics: Record<string, unknown>
}

export type DailyConclusionReferences = {
  boundaryItemId?: string
  scenarioItemId?: string
  confirmationItemId?: string
  invalidationItemId?: string
}

export function dailyConclusionSource(
  runId: string | undefined,
  observation?: BoardDailyObservation,
  item?: SignalItem,
): DailyConclusionSource | undefined {
  if (!runId) return undefined
  if (observation) return {
    runId,
    effectiveDate: observation.effective_date,
    stateCodes: observation.state_codes,
    metrics: observation.metrics,
  }
  if (!item?.payload.metrics) return undefined
  return {
    runId,
    effectiveDate: String(item.payload.effective_date ?? ''),
    stateCodes: item.payload.state_codes ?? [],
    metrics: item.payload.metrics,
  }
}

export function mergeDailyConclusionAnalysis(
  exact: TrendAnalysisRun | null,
  source?: DailyConclusionSource,
): { run: TrendAnalysisRun | null; references: DailyConclusionReferences } {
  if (!source) return { run: exact, references: {} }
  const projected = projectDailyItems(source, exact?.items ?? [])
  if (projected.items.length === 0) return { run: exact, references: projected.references }
  return {
    run: {
      run_id: exact?.run_id ?? `${PREFIX}:${source.runId}`,
      as_of_date: exact?.as_of_date ?? source.effectiveDate,
      completion_state: exact?.completion_state ?? 'complete',
      algorithm_version: exact?.algorithm_version ?? 'daily-conclusion-projection-v1',
      config_version: exact?.config_version ?? 'daily-conclusion-projection-v1',
      input_digest: exact?.input_digest,
      source_observed_at_ms: exact?.source_observed_at_ms,
      expires_at_ms: exact?.expires_at_ms,
      stale: exact?.stale ?? false,
      stale_reasons: exact?.stale_reasons ?? [],
      warnings: exact?.warnings ?? [],
      items: [...(exact?.items ?? []), ...projected.items],
    },
    references: projected.references,
  }
}

export function dailyConclusionReferenceFor(
  kind: 'conclusion' | 'shape' | 'space' | 'conditions' | 'hard-event',
  references: DailyConclusionReferences,
  eventType?: string,
): string | undefined {
  if (kind === 'space' || kind === 'conditions') {
    return references.scenarioItemId ?? references.boundaryItemId
  }
  if (kind === 'hard-event' && ![
    'major-trend-breakout', 'trend-breakout', 'bullish-boundary-triggered',
    'trend-boundary-proximity', 'structure-invalidated',
  ].includes(eventType ?? '')) return undefined
  return references.boundaryItemId
}

function projectDailyItems(
  source: DailyConclusionSource,
  exactItems: GeneratedAnalysisItem[],
): { items: GeneratedAnalysisItem[]; references: DailyConclusionReferences } {
  const items: GeneratedAnalysisItem[] = []
  const references: DailyConclusionReferences = {}
  const atr = numberValue(source.metrics.atr14)
  const priceSpace = recordValue(source.metrics.price_space)
  const envelopes = recordValue(source.metrics.descending_envelopes)
  const envelope = selectEnvelope(envelopes)
  if (envelope) {
    const lineId = `${PREFIX}:envelope:${envelope.label}`
    const startDate = stringValue(envelope.value.start_date)
      ?? stringValue(priceSpace?.start_date)
    const endDate = stringValue(envelope.value.end_date) ?? source.effectiveDate
    const boundary = numberValue(envelope.value.boundary)
      ?? numberValue(envelope.value.end_price)
    const slope = numberValue(envelope.value.slope_per_bar)
    const period = numberValue(envelope.value.period_bars)
    const startPrice = numberValue(envelope.value.start_price)
      ?? (boundary !== undefined && slope !== undefined && period !== undefined
        ? boundary - slope * period : undefined)
    const secondPrice = numberValue(envelope.value.end_price) ?? boundary
    if (startDate && endDate && startPrice !== undefined
      && secondPrice !== undefined && boundary !== undefined) {
      items.push({
        item_id: lineId,
        item_type: 'line',
        payload: {
          kind: 'resistance', horizon: envelope.label === '3m' ? 'medium' : 'long',
          first_pivot_date: startDate, second_pivot_date: endDate,
          first_price: startPrice, second_price: secondPrice,
          projected_price: boundary, score: 1, touch_count: 0,
          major_line_code: `${envelope.label.toUpperCase()}下降边界`,
          ai_reference_code: '一级结论', source: 'daily-board-observation',
          confirmation_state: envelope.value.confirmation_state,
          speed_state: envelope.value.speed_state,
          slope_change_ratio: envelope.value.slope_change_ratio,
          evolution_role: 'current',
        },
      })
      references.boundaryItemId = lineId
    }
    const previous = recordValue(envelope.value.previous_line)
    const previousStartDate = stringValue(previous?.start_date)
    const previousEndDate = stringValue(previous?.end_date)
    const previousStartPrice = numberValue(previous?.start_price)
    const previousEndPrice = numberValue(previous?.end_price)
    if (previousStartDate && previousEndDate
      && previousStartPrice !== undefined && previousEndPrice !== undefined) {
      items.push({
        item_id: `${lineId}:previous`, item_type: 'line',
        payload: {
          kind: 'resistance', horizon: envelope.label === '3m' ? 'medium' : 'long',
          first_pivot_date: previousStartDate,
          second_pivot_date: previousEndDate,
          first_price: previousStartPrice,
          second_price: previousEndPrice,
          score: .99, touch_count: 0,
          major_line_code: `${envelope.label.toUpperCase()}前版边界`,
          ai_reference_code: '趋势线对比', source: 'daily-board-observation',
          evolution_role: 'previous',
          superseded_by_line_id: lineId,
        },
      })
    }
    const confirmation = numberValue(envelope.value.confirmation_price)
      ?? numberValue(priceSpace?.trigger_entry_price)
      ?? numberValue(priceSpace?.entry_price)
      ?? (boundary !== undefined && atr !== undefined ? boundary + atr * .2 : undefined)
    const invalidation = numberValue(priceSpace?.invalidation_price)
      ?? numberValue(envelope.value.invalidation_price)
      ?? (boundary !== undefined && atr !== undefined ? boundary - atr * .25 : undefined)
    if (confirmation !== undefined) {
      references.confirmationItemId = `${PREFIX}:confirmation`
      items.push(levelItem(references.confirmationItemId, confirmation, '确认价'))
    }
    if (invalidation !== undefined) {
      references.invalidationItemId = `${PREFIX}:invalidation`
      items.push(levelItem(references.invalidationItemId, invalidation, '失效价'))
    }
    if (boundary !== undefined && invalidation !== undefined) {
      items.push({
        item_id: `${PREFIX}:boundary-state`, item_type: 'evidence',
        parent_item_id: lineId,
        payload: {
          kind: 'latest-structural-event-summary',
          current_state: envelope.value.state === 'broken' ? 'triggered' : 'ready',
          direction: 'up', boundary_price: boundary,
          invalidation_level: invalidation,
          event_kind: envelope.value.state === 'broken'
            ? 'upward-breakout' : 'no-structural-change',
          trigger_date: envelope.value.state === 'broken' ? source.effectiveDate : undefined,
        },
      })
    }
  }

  const exactScenarioId = stringValue(priceSpace?.scenario_item_id)
  const exactScenario = exactScenarioId
    ? exactItems.find(item => item.item_id === exactScenarioId && item.item_type === 'scenario')
    : undefined
  if (exactScenario) {
    references.scenarioItemId = exactScenario.item_id
  } else if (priceSpace?.kind === 'structural-trade-scenario'
    && numberValue(priceSpace.entry_price) !== undefined
    && numberValue(priceSpace.invalidation_price) !== undefined) {
    const scenarioId = `${PREFIX}:scenario`
    const targets = Array.isArray(priceSpace.targets) ? priceSpace.targets : []
    items.push({
      item_id: scenarioId, item_type: 'scenario',
      payload: {
        ...priceSpace,
        primary: true,
        evidence_item_ids: references.boundaryItemId ? [references.boundaryItemId] : [],
        invalidation_evidence_item_ids: references.invalidationItemId
          ? [references.invalidationItemId] : [],
        targets: targets.map((value, index) => {
          const target = recordValue(value)
          const targetId = `${PREFIX}:target:${index + 1}`
          const price = numberValue(target?.price)
          if (price !== undefined) items.push(levelItem(targetId, price, `目标T${index + 1}`))
          return { ...target, evidence_item_ids: price === undefined ? [] : [targetId] }
        }),
      },
    })
    references.scenarioItemId = scenarioId
  }
  return { items, references }
}

function selectEnvelope(envelopes?: Record<string, unknown>) {
  const values = Object.entries(envelopes ?? {}).flatMap(([label, raw]) => {
    const value = recordValue(raw)
    return value && numberValue(value.boundary) !== undefined
      ? [{ label, value }] : []
  })
  return values.sort((left, right) => (
    envelopeRank(left.value.state) - envelopeRank(right.value.state)
    || Math.abs(numberValue(left.value.distance_atr) ?? 999)
      - Math.abs(numberValue(right.value.distance_atr) ?? 999)
  ))[0]
}

function envelopeRank(value: unknown): number {
  return value === 'broken' ? 0 : value === 'approaching' ? 1 : 2
}

function levelItem(id: string, price: number, label: string): GeneratedAnalysisItem {
  return {
    item_id: id, item_type: 'zone',
    payload: {
      kind: 'key-level', lower: price, upper: price, score: 1,
      label, ai_reference_code: '一级结论', source: 'daily-board-observation',
    },
  }
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}
