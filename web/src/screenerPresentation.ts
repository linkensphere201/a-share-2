import type { ScreenerCandidate, ScreenerPeriod, ScreenerState } from './screenerClient'
import { accumulationStageLabel, accumulationStyleLabel, firstPullbackStageLabel, lowBaseMaturityLabel } from './trendExplanation'
export const periodLabels: Record<ScreenerPeriod, string> = { '3m': '3个月', '6m': '半年', '1y': '1年' }
export const stateLabels: Record<ScreenerState, string> = {
  'critical-breakout': '临界突破', 'breakout-retest': '突破回踩', 'broken-out': '已突破',
  accumulating: '堆量蓄势',
  'pullback-observation': firstPullbackStageLabel('pullback-observation'),
  'pullback-confirmed': firstPullbackStageLabel('pullback-confirmed'),
  'shape-match': '形态相似',
}

export const pullbackStates: ScreenerState[] = ['pullback-confirmed', 'pullback-observation']
export function candidatePeriodLabel(value: ScreenerCandidate) {
  if (value.evidence.kind === 'deep-drawdown-range') return '60日'
  if (value.evidence.kind === 'bull-flag-range') return `盘整${value.evidence.flag_sessions ?? '-'}日`
  if (pullbackStates.includes(value.state)) return `回踩${value.evidence.pullback_sessions ?? '-'}日`
  return value.evidence.period ? periodLabels[value.evidence.period]
    : `${value.evidence.platform_sessions ?? 20}日`
}
export function candidateStageLabel(value: ScreenerCandidate) {
  if (value.evidence.kind === 'low-accumulation-range') return '低位平台承接'
  if (value.evidence.kind === 'long-platform-range') return value.evidence.platform_stage === 'near-upper' ? '临近上沿' : '平台收紧'
  if (value.evidence.kind === 'deep-drawdown-range') return '形态相似'
  if (value.evidence.kind === 'bull-flag-range') return '旗面盘整中'
  if (value.evidence.shape_maturity) return lowBaseMaturityLabel(value.evidence.shape_maturity)
  if (pullbackStates.includes(value.state)) return firstPullbackStageLabel(value.evidence.stage)
  return value.evidence.platform_style ? accumulationStyleLabel(value.evidence.platform_style)
    : value.evidence.stage ? accumulationStageLabel(value.evidence.stage) : stateLabels[value.state]
}
export function signed(value: number) { return `${value > 0 ? '+' : ''}${value.toFixed(2)}` }
export function latestClose(value: ScreenerCandidate) {
  if (value.evidence.latest_close != null) return value.evidence.latest_close
  return (value.evidence.projected_price ?? 0) * (1 + (value.evidence.distance_percent ?? 0) / 100)
}
