import type { IChartApi, Time } from 'lightweight-charts'
import type { TrendAnalysisRun } from './trendAnalysisClient'

export type StructuralScenarioTarget = {
  label: string
  price: number
  basis: string
  riskRewardRatio?: number
  stressedRiskRewardRatio?: number
  evidenceItemIds: string[]
}

export type StructuralTradeScenario = {
  id: string
  direction: 'long' | 'short'
  state: 'waiting-trigger' | 'triggered' | 'retest' | 'invalidated' | 'extended' | 'no-entry'
  setupFamily: string
  horizon: string
  referencePrice?: number
  entryPrice: number
  triggerEntryPrice?: number
  invalidationPrice: number
  riskPercent: number
  targets: StructuralScenarioTarget[]
  selectedTargetLabel?: string
  hasTradeSpace: boolean
  evidenceItemIds: string[]
  invalidationEvidenceItemIds: string[]
}

export type RiskRewardGeometry = {
  scenarioId: string
  direction: 'long' | 'short'
  state: StructuralTradeScenario['state']
  x: number
  width: number
  entryY: number
  invalidationY: number
  entryPrice: number
  invalidationPrice: number
  targets: RiskRewardTargetGeometry[]
  selectedTargetLabel: string
  riskPercent: number
}

export type RiskRewardTargetGeometry = {
  target: StructuralScenarioTarget
  targetY: number
  x: number
  width: number
  selected: boolean
}

export function readPrimaryStructuralScenario(
  run: TrendAnalysisRun | null | undefined,
): StructuralTradeScenario | undefined {
  if (!run) return undefined
  const candidates = run.items.filter(item => (
    item.item_type === 'scenario'
    && item.payload.kind === 'structural-trade-scenario'
  )).sort((left, right) => {
    const leftPrimary = left.payload.primary === true ? 1 : 0
    const rightPrimary = right.payload.primary === true ? 1 : 0
    const leftRank = numberValue(left.payload.rank) ?? 999
    const rightRank = numberValue(right.payload.rank) ?? 999
    return rightPrimary - leftPrimary || leftRank - rightRank
  })
  const item = candidates[0]
  if (!item) return undefined
  const direction = item.payload.direction
  const state = item.payload.state
  const entryPrice = numberValue(item.payload.entry_price)
  const invalidationPrice = numberValue(item.payload.invalidation_price)
  if ((direction !== 'long' && direction !== 'short')
    || !isScenarioState(state)
    || entryPrice === undefined || invalidationPrice === undefined) return undefined
  const referencePrice = numberValue(item.payload.reference_price)
  const triggerEntryPrice = numberValue(item.payload.trigger_entry_price)
  const parsedTargets = Array.isArray(item.payload.targets)
    ? item.payload.targets.flatMap(value => parseTarget(value))
    : []
  const actionablePrice = state === 'waiting-trigger'
    ? entryPrice
    : referencePrice ?? entryPrice
  const targets = parsedTargets.filter(target => direction === 'long'
    ? target.price > actionablePrice
    : target.price < actionablePrice)
  return {
    id: item.item_id,
    direction,
    state,
    setupFamily: stringValue(item.payload.setup_family) ?? 'structural',
    horizon: stringValue(item.payload.horizon) ?? 'unspecified',
    referencePrice,
    entryPrice,
    triggerEntryPrice,
    invalidationPrice,
    riskPercent: numberValue(item.payload.risk_percent) ?? 0,
    targets,
    selectedTargetLabel: stringValue(item.payload.selected_target_label),
    hasTradeSpace: item.payload.has_trade_space === true && targets.some(
      target => target.stressedRiskRewardRatio !== undefined,
    ),
    evidenceItemIds: stringArray(item.payload.evidence_item_ids),
    invalidationEvidenceItemIds: stringArray(item.payload.invalidation_evidence_item_ids),
  }
}

export function projectRiskReward(
  run: TrendAnalysisRun | null | undefined,
  chart: IChartApi | null,
  priceSeries: { priceToCoordinate: (price: number) => number | null } | null,
  host: HTMLDivElement | null,
  selectedTargetLabel?: string,
): RiskRewardGeometry | undefined {
  const scenario = readPrimaryStructuralScenario(run)
  if (!scenario || !chart || !priceSeries || !host || scenario.targets.length === 0) return undefined
  const target = scenario.targets.find(item => item.label === selectedTargetLabel)
    ?? scenario.targets.find(item => item.label === scenario.selectedTargetLabel)
    ?? scenario.targets[0]
  const asOfX = chart.timeScale().timeToCoordinate(run!.as_of_date as Time)
  const chartWidth = chart.timeScale().width()
  if (asOfX === null || asOfX < -12 || asOfX > chartWidth + 12 || chartWidth < 40) return undefined
  const entryY = priceSeries.priceToCoordinate(scenario.entryPrice)
  const invalidationY = priceSeries.priceToCoordinate(scenario.invalidationPrice)
  if (entryY === null || invalidationY === null) return undefined
  const maxWidth = Math.min(156, chartWidth - 6)
  const x = Math.max(3, Math.min(asOfX, chartWidth - maxWidth - 3))
  const width = Math.max(20, Math.min(maxWidth, chartWidth - x - 3))
  const targets = scenario.targets.flatMap((item, index) => {
    const targetY = priceSeries.priceToCoordinate(item.price)
    if (targetY === null) return []
    const inset = Math.min(index * 12, Math.max(0, width - 42))
    return [{
      target: item,
      targetY,
      x: x + inset,
      width: width - inset,
      selected: item.label === target.label,
    }]
  })
  if (targets.length === 0) return undefined
  return {
    scenarioId: scenario.id,
    direction: scenario.direction,
    state: scenario.state,
    x,
    width,
    entryY,
    invalidationY,
    entryPrice: scenario.entryPrice,
    invalidationPrice: scenario.invalidationPrice,
    targets,
    selectedTargetLabel: target.label,
    riskPercent: scenario.riskPercent,
  }
}

export function targetBasisLabel(value: string): string {
  const labels = value.split('+').map(item => {
    if (item === 'estimated-volume-at-price') return '成交密集区'
    if (item === 'key-level') return '关键位'
    if (item.includes('range-high')) return '历史区间高点'
    if (item.includes('range-low')) return '历史区间低点'
    if (item.includes('trend-line') || item.includes('-projection-')) return '趋势线投影'
    if (item.includes('pattern')) return '形态边界'
    if (item.includes('gap')) return '缺口边界'
    return '结构边界'
  })
  return [...new Set(labels)].join(' + ')
}

export function setupFamilyLabel(value: string): string {
  return ({
    'v-top': '倒V形顶',
    'v-bottom': 'V形底',
    'double-top': '双顶',
    'double-bottom': '双底',
    'head-and-shoulders-top': '头肩顶',
    'head-and-shoulders-bottom': '头肩底',
    'ascending-triangle': '上升三角形',
    'descending-triangle': '下降三角形',
    'symmetrical-triangle': '对称三角形',
    'bull-flag': '多头旗形',
    'bear-flag': '空头旗形',
    diamond: '菱形',
    triangle: '三角形',
    reversal: '反转形态',
    'trend-line-break': '趋势线突破/破位',
    'support-break': '支撑破位',
    structural: '结构形态',
  } as Record<string, string>)[value] ?? '结构形态'
}

export function isVolumeZoneTarget(target: StructuralScenarioTarget): boolean {
  return target.basis.split('+').includes('estimated-volume-at-price')
}

function parseTarget(value: unknown): StructuralScenarioTarget[] {
  if (!value || typeof value !== 'object') return []
  const target = value as Record<string, unknown>
  const label = stringValue(target.label)
  const price = numberValue(target.price)
  if (!label || price === undefined) return []
  return [{
    label,
    price,
    basis: stringValue(target.basis) ?? 'structure',
    riskRewardRatio: numberValue(target.risk_reward_ratio),
    stressedRiskRewardRatio: numberValue(target.stressed_risk_reward_ratio),
    evidenceItemIds: stringArray(target.evidence_item_ids),
  }]
}

function isScenarioState(value: unknown): value is StructuralTradeScenario['state'] {
  return typeof value === 'string' && new Set([
    'waiting-trigger', 'triggered', 'retest', 'invalidated', 'extended', 'no-entry',
  ]).has(value)
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : []
}
