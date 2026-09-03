import { extendLineToBounds, type LineGeometry } from './trendLines'
import type { TrendAnalysisRun } from './trendAnalysisClient'
import type { IChartApi, Time } from 'lightweight-charts'

export type GeneratedPivotGeometry = {
  id: string
  kind: 'high' | 'low'
  x: number
  y: number
  price: number
  tentative: boolean
  pivotDate: string
  confirmedDate?: string
}

export type GeneratedTrendLineGeometry = {
  id: string
  kind: 'support' | 'resistance'
  horizon: 'short' | 'long'
  line: LineGeometry
  score: number
  touchCount: number
  label?: string
}

export type GeneratedZoneGeometry = {
  id: string
  kind: 'key-level' | 'estimated-volume-at-price'
  y: number
  height: number
  width: number
  lower: number
  upper: number
  score: number
  estimatedShare?: number
}

export type GeneratedPatternGeometry = {
  id: string
  displayName: string
  state: 'forming' | 'confirmed' | 'invalidated'
  primary: boolean
  points: string
  neckline: LineGeometry
  boundaries: LineGeometry[]
  labelX: number
  labelY: number
  showLabel: boolean
  score: number
}

export type GeneratedBreakoutState = {
  state: 'forming' | 'ready' | 'triggered' | 'confirmed' | 'retesting' | 'continuing' | 'failed' | 'invalidated' | 'stale'
  direction: 'up' | 'down'
  boundaryPrice: number
  invalidationPrice: number
  triggerDate?: string
  confirmationDate?: string
  failureDate?: string
  preview: boolean
  eventKind?: 'upward-breakout' | 'downward-breakdown' | 'retest' | 'false-breakout-risk' | 'no-structural-change'
}

export type AnalysisItem = TrendAnalysisRun['items'][number]

export function selectCoreTrendLineItems(items: AnalysisItem[]): AnalysisItem[] {
  const bestByRole = new Map<string, AnalysisItem>()
  const aiReferences: AnalysisItem[] = []
  for (const item of items) {
    if (item.item_type !== 'line') continue
    const kind = item.payload.kind
    const horizon = item.payload.horizon
    if ((kind !== 'support' && kind !== 'resistance')
      || (horizon !== 'short' && horizon !== 'long')) continue
    if (typeof item.payload.ai_reference_code === 'string') {
      aiReferences.push(item)
      continue
    }
    const key = `${horizon}:${kind}`
    const current = bestByRole.get(key)
    const score = typeof item.payload.score === 'number' ? item.payload.score : 0
    const currentScore = typeof current?.payload.score === 'number' ? current.payload.score : 0
    const touches = typeof item.payload.touch_count === 'number' ? item.payload.touch_count : 0
    const currentTouches = typeof current?.payload.touch_count === 'number' ? current.payload.touch_count : 0
    if (!current || score > currentScore || (score === currentScore && touches > currentTouches)) {
      bestByRole.set(key, item)
    }
  }
  return [...bestByRole.values(), ...aiReferences]
}

export function selectCorePatternItems(items: AnalysisItem[]): AnalysisItem[] {
  const patterns = items.filter(item => item.item_type === 'pattern')
  const aiReferences = patterns.filter(item => typeof item.payload.ai_reference_code === 'string')
  const horizons = new Map<string, AnalysisItem[]>()
  for (const item of patterns) {
    const horizon = item.payload.horizon === 'long' || item.payload.horizon === 'short'
      ? item.payload.horizon
      : 'unspecified'
    horizons.set(horizon, [...(horizons.get(horizon) ?? []), item])
  }
  const core = [...horizons.values()].flatMap(candidates => {
    const active = candidates.filter(item => item.payload.completion_state !== 'invalidated')
    const pool = active.length > 0 ? active : candidates
    const ranked = [...pool].sort((left, right) => {
      const primaryOrder = Number(right.payload.primary === true) - Number(left.payload.primary === true)
      if (primaryOrder !== 0) return primaryOrder
      const leftRank = typeof left.payload.interpretation_rank === 'number'
        ? left.payload.interpretation_rank
        : Number.POSITIVE_INFINITY
      const rightRank = typeof right.payload.interpretation_rank === 'number'
        ? right.payload.interpretation_rank
        : Number.POSITIVE_INFINITY
      if (leftRank !== rightRank) return leftRank - rightRank
      const leftScore = typeof left.payload.ranking_score === 'number'
        ? left.payload.ranking_score
        : typeof left.payload.score === 'number' ? left.payload.score : 0
      const rightScore = typeof right.payload.ranking_score === 'number'
        ? right.payload.ranking_score
        : typeof right.payload.score === 'number' ? right.payload.score : 0
      return rightScore - leftScore
    })
    return ranked.slice(0, 1)
  })
  const coreIds = new Set(core.map(item => item.item_id))
  return [...core, ...aiReferences.filter(item => !coreIds.has(item.item_id))]
}

export function selectCoreZoneItems(items: AnalysisItem[]): AnalysisItem[] {
  const strongestByKind = new Map<string, AnalysisItem[]>()
  const aiReferences: AnalysisItem[] = []
  for (const item of items) {
    if (item.item_type !== 'zone') continue
    const kind = item.payload.kind
    if (kind !== 'key-level' && kind !== 'estimated-volume-at-price') continue
    if (typeof item.payload.ai_reference_code === 'string') {
      aiReferences.push(item)
      continue
    }
    strongestByKind.set(kind, [...(strongestByKind.get(kind) ?? []), item])
  }
  return [...strongestByKind.values()].flatMap(candidates => (
    [...candidates].sort((left, right) => {
      if (right.payload.kind === 'estimated-volume-at-price') {
        const leftShare = typeof left.payload.estimated_share === 'number' ? left.payload.estimated_share : 0
        const rightShare = typeof right.payload.estimated_share === 'number' ? right.payload.estimated_share : 0
        return rightShare - leftShare
      }
      const leftScore = typeof left.payload.score === 'number' ? left.payload.score : 0
      const rightScore = typeof right.payload.score === 'number' ? right.payload.score : 0
      return rightScore - leftScore
    }).slice(0, 2)
  )).concat(aiReferences)
}

export function projectGeneratedPivots(
  run: TrendAnalysisRun | null,
  chart: IChartApi | null,
  priceSeries: { priceToCoordinate: (price: number) => number | null } | null,
  host: HTMLDivElement | null,
  showTentative: boolean,
): GeneratedPivotGeometry[] {
  if (!run || !chart || !priceSeries || !host) return []
  const projected = run.items.flatMap(item => {
    if (item.item_type !== 'anchor') return []
    const kind = item.payload.kind
    const pivotDate = item.payload.pivot_date
    const price = item.payload.price
    const tentative = item.payload.tentative === true
    if ((kind !== 'high' && kind !== 'low') || typeof pivotDate !== 'string' || typeof price !== 'number') return []
    if (tentative && !showTentative) return []
    const x = chart.timeScale().timeToCoordinate(pivotDate as Time)
    const y = priceSeries.priceToCoordinate(price)
    if (x === null || y === null || x < -20 || x > host.clientWidth + 20 || y < -20 || y > host.clientHeight + 20) return []
    return [{
      id: item.item_id,
      kind: kind as GeneratedPivotGeometry['kind'],
      x,
      y,
      price,
      tentative,
      pivotDate,
      confirmedDate: typeof item.payload.confirmed_date === 'string'
        ? item.payload.confirmed_date
        : undefined,
    }]
  })
  const seen = new Set<string>()
  return projected.filter(item => {
    const key = `${item.kind}:${item.pivotDate}:${item.price}:${item.tentative}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  }).sort((left, right) => left.pivotDate.localeCompare(right.pivotDate)).slice(-12)
}

export function projectGeneratedTrendLines(
  run: TrendAnalysisRun | null,
  chart: IChartApi | null,
  priceSeries: { priceToCoordinate: (price: number) => number | null } | null,
  host: HTMLDivElement | null,
  showShort: boolean,
  showLong: boolean,
  highlightedItemId?: string,
): GeneratedTrendLineGeometry[] {
  if (!run || !chart || !priceSeries || !host) return []
  const width = chart.timeScale().width()
  const height = chart.panes()[0]?.getHeight() ?? host.clientHeight
  const core = selectCoreTrendLineItems(run.items)
  const highlighted = highlightedItemId
    ? run.items.find(item => item.item_id === highlightedItemId && item.item_type === 'line')
    : undefined
  const selected = highlighted && !core.some(item => item.item_id === highlighted.item_id)
    ? [...core, highlighted]
    : core
  return selected.flatMap(item => {
    if (item.item_type !== 'line') return []
    const kind = item.payload.kind
    const horizon = item.payload.horizon
    const firstDate = item.payload.first_pivot_date
    const secondDate = item.payload.second_pivot_date
    const firstPrice = item.payload.first_price
    const secondPrice = item.payload.second_price
    if ((kind !== 'support' && kind !== 'resistance')
      || (horizon !== 'short' && horizon !== 'long')
      || typeof firstDate !== 'string' || typeof secondDate !== 'string'
      || typeof firstPrice !== 'number' || typeof secondPrice !== 'number') return []
    if ((horizon === 'short' && !showShort) || (horizon === 'long' && !showLong)) return []
    const x1 = chart.timeScale().timeToCoordinate(firstDate as Time)
    const x2 = chart.timeScale().timeToCoordinate(secondDate as Time)
    const y1 = priceSeries.priceToCoordinate(firstPrice)
    const y2 = priceSeries.priceToCoordinate(secondPrice)
    if (x1 === null || x2 === null || y1 === null || y2 === null) return []
    return [{
      id: item.item_id,
      kind: kind as GeneratedTrendLineGeometry['kind'],
      horizon,
      line: extendLineToBounds({ x1, y1, x2, y2 }, width, height),
      score: typeof item.payload.score === 'number' ? item.payload.score : 0,
      touchCount: typeof item.payload.touch_count === 'number' ? item.payload.touch_count : 0,
      label: typeof item.payload.major_line_code === 'string' ? item.payload.major_line_code : undefined,
    }]
  })
}

export function projectGeneratedZones(
  run: TrendAnalysisRun | null,
  chart: IChartApi | null,
  priceSeries: { priceToCoordinate: (price: number) => number | null } | null,
  host: HTMLDivElement | null,
  showKeyLevels: boolean,
  showVolumeZones: boolean,
): GeneratedZoneGeometry[] {
  if (!run || !chart || !priceSeries || !host) return []
  const width = chart.timeScale().width()
  const paneHeight = chart.panes()[0]?.getHeight() ?? host.clientHeight
  return selectCoreZoneItems(run.items).flatMap(item => {
    if (item.item_type !== 'zone') return []
    const kind = item.payload.kind
    const lower = item.payload.lower
    const upper = item.payload.upper
    if ((kind !== 'key-level' && kind !== 'estimated-volume-at-price')
      || typeof lower !== 'number' || typeof upper !== 'number') return []
    if ((kind === 'key-level' && !showKeyLevels)
      || (kind === 'estimated-volume-at-price' && !showVolumeZones)) return []
    const lowerY = priceSeries.priceToCoordinate(lower)
    const upperY = priceSeries.priceToCoordinate(upper)
    if (lowerY === null || upperY === null) return []
    const top = Math.max(0, Math.min(lowerY, upperY))
    const bottom = Math.min(paneHeight, Math.max(lowerY, upperY))
    if (bottom < 0 || top > paneHeight) return []
    return [{
      id: item.item_id,
      kind: kind as GeneratedZoneGeometry['kind'],
      y: top,
      height: Math.max(kind === 'key-level' ? 2 : 1, bottom - top),
      width,
      lower,
      upper,
      score: typeof item.payload.score === 'number' ? item.payload.score : 0,
      estimatedShare: typeof item.payload.estimated_share === 'number'
        ? item.payload.estimated_share
        : undefined,
    }]
  })
}

export function projectGeneratedPatterns(
  run: TrendAnalysisRun | null,
  chart: IChartApi | null,
  priceSeries: { priceToCoordinate: (price: number) => number | null } | null,
  visible: boolean,
): GeneratedPatternGeometry[] {
  if (!visible || !run || !chart || !priceSeries) return []
  const chartWidth = chart.timeScale().width()
  const projectedPatterns = selectCorePatternItems(run.items).flatMap(item => {
    if (item.item_type !== 'pattern') return []
    const displayName = item.payload.display_name
    const state = item.payload.completion_state
    const pivots = item.payload.pivots
    const necklinePrice = item.payload.neckline_price
    if (typeof displayName !== 'string'
      || (state !== 'forming' && state !== 'confirmed' && state !== 'invalidated')
      || !Array.isArray(pivots) || typeof necklinePrice !== 'number') return []
    const projected = pivots.flatMap(value => {
      if (!value || typeof value !== 'object') return []
      const pivot = value as Record<string, unknown>
      if (typeof pivot.pivot_date !== 'string' || typeof pivot.price !== 'number') return []
      const x = chart.timeScale().timeToCoordinate(pivot.pivot_date as Time)
      const y = priceSeries.priceToCoordinate(pivot.price)
      return x === null || y === null ? [] : [{ x, y }]
    })
    if (projected.length !== pivots.length || projected.length < 1) return []
    const necklineY = priceSeries.priceToCoordinate(necklinePrice)
    if (necklineY === null) return []
    const first = projected[0]
    const last = projected.at(-1)!
    const boundaryGeometry = item.payload.boundary_geometry
    const boundaries: LineGeometry[] = []
    if (boundaryGeometry && typeof boundaryGeometry === 'object') {
      const geometry = boundaryGeometry as Record<string, unknown>
      const boundaryValues = [geometry.upper, geometry.lower]
      if (Array.isArray(geometry.segments)) boundaryValues.push(...geometry.segments)
      for (const value of boundaryValues) {
        if (!value || typeof value !== 'object') continue
        const boundary = value as Record<string, unknown>
        if (typeof boundary.start_date !== 'string' || typeof boundary.end_date !== 'string'
          || typeof boundary.start_price !== 'number' || typeof boundary.end_price !== 'number') continue
        const x1 = chart.timeScale().timeToCoordinate(boundary.start_date as Time)
        const x2 = chart.timeScale().timeToCoordinate(boundary.end_date as Time)
        const y1 = priceSeries.priceToCoordinate(boundary.start_price)
        const y2 = priceSeries.priceToCoordinate(boundary.end_price)
        if (x1 === null || x2 === null || y1 === null || y2 === null) continue
        boundaries.push({ x1, y1, x2, y2 })
      }
    }
    if (projected.length < 3 && boundaries.length === 0) return []
    const patternXs = [
      ...projected.map(point => point.x),
      ...boundaries.flatMap(boundary => [boundary.x1, boundary.x2]),
    ]
    const patternStartX = Math.min(...patternXs)
    const patternEndX = Math.max(...patternXs)
    return [{
      id: item.item_id,
      displayName,
      state: state as GeneratedPatternGeometry['state'],
      primary: item.payload.primary === true,
      points: projected.map(point => `${point.x},${point.y}`).join(' '),
      neckline: { x1: patternStartX, y1: necklineY, x2: patternEndX, y2: necklineY },
      boundaries,
      labelX: Math.max(36, Math.min(chartWidth - 36, Math.min(first.x, last.x) + Math.abs(last.x - first.x) / 2)),
      labelY: Math.max(10, Math.min(...projected.map(point => point.y), necklineY) - 5),
      showLabel: false,
      score: typeof item.payload.score === 'number' ? item.payload.score : 0,
    }]
  })
  return assignGeneratedPatternLabels(projectedPatterns, chartWidth)
}

export function assignGeneratedPatternLabels(
  patterns: GeneratedPatternGeometry[],
  chartWidth: number,
): GeneratedPatternGeometry[] {
  const maxLabels = chartWidth < 420 ? 1 : chartWidth < 700 ? 2 : 4
  const accepted: GeneratedPatternGeometry[] = []
  const ranked = [...patterns].sort((left, right) => (
    Number(right.primary) - Number(left.primary)
    || Number(right.state === 'confirmed') - Number(left.state === 'confirmed')
    || right.score - left.score
  ))
  for (const candidate of ranked) {
    if (accepted.length >= maxLabels) break
    const collides = accepted.some(value => (
      Math.abs(value.labelX - candidate.labelX) < 84
      && Math.abs(value.labelY - candidate.labelY) < 18
    ))
    if (!collides) accepted.push(candidate)
  }
  const visibleIds = new Set(accepted.map(item => item.id))
  return patterns.map(item => ({ ...item, showLabel: visibleIds.has(item.id) }))
}

export function readGeneratedBreakoutState(
  run: TrendAnalysisRun | null,
  visible: boolean,
): GeneratedBreakoutState | undefined {
  if (!visible || !run) return undefined
  const primaryIds = new Set(run.items.filter(item => (
    item.item_type === 'pattern' && item.payload.primary === true
  )).map(item => item.item_id))
  const patternCandidates = run.items.filter(item => (
    item.item_type === 'evidence'
    && item.payload.kind === 'breakout-state-summary'
  ))
  const structuralCandidates = run.items.filter(item => (
    item.item_type === 'evidence'
    && item.payload.kind === 'latest-structural-event-summary'
  ))
  const item = structuralCandidates.find(value => value.payload.current_state !== 'ready')
    ?? patternCandidates.find(value => (
    typeof value.parent_item_id === 'string' && primaryIds.has(value.parent_item_id)
    ))
    ?? structuralCandidates[0]
    ?? patternCandidates[0]
  if (!item) return undefined
  const state = item.payload.current_state
  const direction = item.payload.direction
  const boundaryPrice = item.payload.boundary_price
  const invalidationPrice = item.payload.invalidation_level
  const states = new Set([
    'forming', 'ready', 'triggered', 'confirmed', 'retesting',
    'continuing', 'failed', 'invalidated', 'stale',
  ])
  if (typeof state !== 'string' || !states.has(state)
    || (direction !== 'up' && direction !== 'down')
    || typeof boundaryPrice !== 'number' || typeof invalidationPrice !== 'number') return undefined
  return {
    state: state as GeneratedBreakoutState['state'],
    direction,
    boundaryPrice,
    invalidationPrice,
    triggerDate: typeof item.payload.trigger_date === 'string' ? item.payload.trigger_date : undefined,
    confirmationDate: typeof item.payload.confirmation_date === 'string' ? item.payload.confirmation_date : undefined,
    failureDate: typeof item.payload.failure_date === 'string' ? item.payload.failure_date : undefined,
    preview: item.payload.preview === true,
    ...(item.payload.event_kind === 'upward-breakout'
      || item.payload.event_kind === 'downward-breakdown'
      || item.payload.event_kind === 'retest'
      || item.payload.event_kind === 'false-breakout-risk'
      || item.payload.event_kind === 'no-structural-change'
      ? { eventKind: item.payload.event_kind }
      : {}),
  }
}
