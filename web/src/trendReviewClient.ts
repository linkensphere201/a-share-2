import type { GeneratedAnalysisItem, TrendAnalysisRun } from './trendAnalysisClient'
import type { TrendTradingSystemSettings } from './tradingSystems'

export type TrendReviewDecision = 'pending' | 'accepted' | 'rejected' | 'ambiguous'
export type TrendReviewStatus = 'proposed' | 'ambiguous' | 'confirmed' | 'rejected'

export type TrendReviewLabel = {
  item_id: string
  item_type: string
  decision: TrendReviewDecision
  payload: Record<string, unknown>
  rationale: string
}

export type TrendReview = {
  review_id: string
  symbol: string
  dataset_version: string
  timeframe: 'daily'
  horizon: 'short' | 'long'
  interval_start: string
  interval_end: string
  as_of_date: string
  input_digest: string
  algorithm_version: string
  config_version: string
  settings: Record<string, number>
  classification: string
  review_status: TrendReviewStatus
  tags: string[]
  labels: TrendReviewLabel[]
  expected: Record<string, unknown>
  rationale: string
  sources: Array<Record<string, string>>
  revision: number
  created_at_ms: number
  updated_at_ms: number
}

export type TrendReviewCreate = {
  symbol: string
  horizon: 'short' | 'long'
  intervalStart: string
  intervalEnd: string
  asOfDate: string
  classification: 'positive' | 'near-miss' | 'ambiguous' | 'robustness'
  tags: string[]
  rationale: string
  settings: TrendTradingSystemSettings
}

export async function createTrendReview(value: TrendReviewCreate): Promise<{
  review: TrendReview
  analysis: TrendAnalysisRun
}> {
  const response = await fetch('/api/trend-reviews', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      symbol: value.symbol,
      timeframe: 'daily',
      horizon: value.horizon,
      interval_start: value.intervalStart,
      interval_end: value.intervalEnd,
      as_of_date: value.asOfDate,
      dataset_version: 'local-review-v1',
      classification: value.classification,
      tags: value.tags,
      rationale: value.rationale,
      sources: [{
        provider: 'local-store',
        dataset: 'canonical-daily-bars',
        checked_on: localDate(),
      }],
      short_horizon_bars: value.settings.shortHorizonBars,
      medium_horizon_bars: value.settings.mediumHorizonBars,
      long_horizon_bars: value.settings.longHorizonBars,
      config_version: reviewConfigVersion(value.settings),
    }),
  })
  return jsonResponse(response)
}

export async function updateTrendReview(
  review: TrendReview,
  status: TrendReviewStatus,
  labels: TrendReviewLabel[],
  rationale: string,
): Promise<TrendReview> {
  const response = await fetch(`/api/trend-reviews/${encodeURIComponent(review.review_id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      revision: review.revision,
      review_status: status,
      labels,
      expected: buildExpectedLabels(labels),
      rationale,
    }),
  })
  return jsonResponse(response)
}

export async function listTrendReviews(symbol: string): Promise<TrendReview[]> {
  const response = await fetch(`/api/trend-reviews?symbol=${encodeURIComponent(symbol)}`)
  const body = await jsonResponse<{ items: TrendReview[] }>(response)
  return body.items
}

export async function rebuildTrendReviewSnapshot(reviewId: string): Promise<TrendAnalysisRun> {
  const response = await fetch(
    `/api/trend-reviews/${encodeURIComponent(reviewId)}/snapshot`,
    { method: 'POST' },
  )
  return jsonResponse(response)
}

export function buildExpectedLabels(labels: TrendReviewLabel[]): Record<string, unknown> {
  const fields: Record<string, GeneratedAnalysisItem[]> = {
    pivots: [], trend_lines: [], key_levels: [], patterns: [], events: [], forbidden: [],
  }
  const fieldByType: Record<string, string> = {
    anchor: 'pivots', line: 'trend_lines', zone: 'key_levels',
    pattern: 'patterns', transition: 'events',
  }
  labels.forEach(label => {
    const item = {
      item_id: label.item_id,
      item_type: label.item_type as GeneratedAnalysisItem['item_type'],
      payload: label.payload,
    }
    if (label.decision === 'accepted') fields[fieldByType[label.item_type] ?? 'forbidden'].push(item)
    if (label.decision === 'rejected') fields.forbidden.push(item)
  })
  return Object.fromEntries(Object.entries(fields).filter(([, items]) => items.length > 0))
}

async function jsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const body = await response.json() as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // Preserve the HTTP status for a non-JSON response.
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

function reviewConfigVersion(settings: TrendTradingSystemSettings): string {
  return `review-ui-v1-${settings.shortHorizonBars}-${settings.mediumHorizonBars}-${settings.longHorizonBars}`
}

function localDate(): string {
  const now = new Date()
  return [now.getFullYear(), now.getMonth() + 1, now.getDate()]
    .map((item, index) => index === 0 ? String(item) : String(item).padStart(2, '0'))
    .join('-')
}
