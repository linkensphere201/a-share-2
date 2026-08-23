import {
  selectCorePatternItems,
  selectCoreTrendLineItems,
  selectCoreZoneItems,
  type AnalysisItem,
} from './generatedAnalysisProjection'
import type { TrendAnalysisRun } from './trendAnalysisClient'

export type TrendExplanationItem = {
  analysisItemId: string
  title: string
  detail: string
  score?: number
  state?: string
}

export type TrendExplanationSection = {
  id: 'trend-lines' | 'key-levels' | 'patterns'
  title: string
  items: TrendExplanationItem[]
}

export type TrendExplanation = {
  asOfDate: string
  source: 'official' | 'preview'
  stale: boolean
  summary: string
  sections: TrendExplanationSection[]
  warnings: string[]
}

export function buildTrendExplanation(run: TrendAnalysisRun | null | undefined): TrendExplanation | undefined {
  if (!run) return undefined
  const lineItems = selectCoreTrendLineItems(run.items).map(explainLine)
  const zoneItems = selectCoreZoneItems(run.items).map(explainZone)
  const patternItems = selectCorePatternItems(run.items).map(explainPattern)
  const event = latestStructuralEvent(run.items)
  const summary = event
    ? `${structuralEventLabel(event.payload.event_kind, event.payload.current_state)}${numberValue(event.payload.boundary_price) !== undefined ? `，参考边界 ${numberValue(event.payload.boundary_price)!.toFixed(2)}` : ''}。`
    : `识别到 ${lineItems.length} 条核心趋势线、${zoneItems.length} 个关键价格区和 ${patternItems.length} 个核心形态。`
  return {
    asOfDate: run.as_of_date,
    source: run.source_observed_at_ms ? 'preview' : 'official',
    stale: run.stale,
    summary,
    sections: [
      { id: 'trend-lines', title: '趋势', items: lineItems },
      { id: 'key-levels', title: '关键位', items: zoneItems },
      { id: 'patterns', title: '形态', items: patternItems },
    ],
    warnings: [...run.stale_reasons, ...run.warnings.map(warningText)].filter(Boolean).slice(0, 3),
  }
}

function explainLine(item: AnalysisItem): TrendExplanationItem {
  const payload = item.payload
  const horizon = payload.horizon === 'short' ? '短期' : '长期'
  const role = payload.kind === 'support' ? '支撑线' : '压力线'
  const slope = numberValue(payload.slope_per_bar) ?? 0
  const direction = Math.abs(slope) < 0.000001 ? '横向' : slope > 0 ? '上行' : '下行'
  const firstPrice = formatPrice(payload.first_price)
  const secondPrice = formatPrice(payload.second_price)
  const projected = numberValue(payload.projected_price)
  const touches = numberValue(payload.touch_count)
  return {
    analysisItemId: item.item_id,
    title: `${horizon}${direction}${role}`,
    detail: `${stringValue(payload.first_pivot_date) ?? '-'} ${firstPrice} → ${stringValue(payload.second_pivot_date) ?? '-'} ${secondPrice}${projected !== undefined ? `；当前投影 ${projected.toFixed(2)}` : ''}${touches !== undefined ? `；触碰 ${touches} 次` : ''}。`,
    score: numberValue(payload.score),
  }
}

function explainZone(item: AnalysisItem): TrendExplanationItem {
  const payload = item.payload
  const volume = payload.kind === 'estimated-volume-at-price'
  const observations = numberValue(payload.observation_count)
  const share = numberValue(payload.estimated_share)
  const roleReversal = payload.role_reversal === true ? '；存在支撑/压力角色转换' : ''
  return {
    analysisItemId: item.item_id,
    title: `${volume ? '成交密集区' : '关键位'} ${formatPrice(payload.lower)}–${formatPrice(payload.upper)}`,
    detail: volume
      ? `基于日线成交量区间估算${share !== undefined ? `，估算占比 ${(share * 100).toFixed(1)}%` : ''}。`
      : `${observations !== undefined ? `${observations} 次历史观察` : '历史价格聚类'}${roleReversal}。`,
    score: numberValue(payload.score),
  }
}

function explainPattern(item: AnalysisItem): TrendExplanationItem {
  const payload = item.payload
  const horizon = payload.horizon === 'short' ? '短期' : '长期'
  const state = stringValue(payload.completion_state) ?? 'forming'
  const neckline = numberValue(payload.neckline_price)
  return {
    analysisItemId: item.item_id,
    title: `${horizon}${stringValue(payload.display_name) ?? '结构形态'}`,
    detail: `${stringValue(payload.start_date) ?? '-'} 至 ${stringValue(payload.end_date) ?? '-'}；${patternStateLabel(state)}${neckline !== undefined ? `；结构边界 ${neckline.toFixed(2)}` : ''}。`,
    score: numberValue(payload.score),
    state,
  }
}

function latestStructuralEvent(items: AnalysisItem[]): AnalysisItem | undefined {
  const evidence = items.filter(item => (
    item.item_type === 'evidence' && item.payload.kind === 'latest-structural-event-summary'
  ))
  return evidence.find(item => item.payload.current_state !== 'ready') ?? evidence[0]
}

function structuralEventLabel(kind: unknown, state: unknown): string {
  if (kind === 'upward-breakout') return '最新日线形成向上突破'
  if (kind === 'downward-breakdown') return '最新日线形成向下破位'
  if (kind === 'retest') return '最新日线正在回踩结构边界'
  if (kind === 'false-breakout-risk') return '最新日线出现假突破风险'
  if (kind === 'no-structural-change') return '最新日线未改变既有结构'
  return `当前结构状态：${typeof state === 'string' ? state : '待确认'}`
}

function patternStateLabel(value: string): string {
  if (value === 'confirmed') return '已确认'
  if (value === 'invalidated') return '已失效'
  return '形成中'
}

function warningText(value: Record<string, unknown>): string {
  if (value.code === 'adjustment_factors_incomplete') return '复权因子不完整，当前分析采用未复权价格。'
  return stringValue(value.message) ?? stringValue(value.code) ?? ''
}

function formatPrice(value: unknown): string {
  return typeof value === 'number' ? value.toFixed(2) : '-'
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}
