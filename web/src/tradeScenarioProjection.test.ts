import { describe, expect, it } from 'vitest'
import type { IChartApi } from 'lightweight-charts'
import {
  isVolumeZoneTarget,
  projectRiskReward,
  readPrimaryStructuralScenario,
  targetBasisLabel,
} from './tradeScenarioProjection'
import type { TrendAnalysisRun } from './trendAnalysisClient'

const run: TrendAnalysisRun = {
  run_id: 'run-1', as_of_date: '2026-09-08', completion_state: 'complete',
  stale: false, stale_reasons: [], warnings: [], items: [{
    item_id: 'scenario-2', item_type: 'scenario', payload: {
      kind: 'structural-trade-scenario', rank: 2, primary: false,
      direction: 'short', state: 'waiting-trigger', setup_family: 'support-break',
      horizon: 'short', entry_price: 9, invalidation_price: 10,
      risk_percent: 11.1, targets: [], evidence_item_ids: [],
    },
  }, {
    item_id: 'scenario-1', item_type: 'scenario', payload: {
      kind: 'structural-trade-scenario', rank: 1, primary: true,
      direction: 'long', state: 'triggered', setup_family: 'triangle',
      horizon: 'medium', entry_price: 10, invalidation_price: 9,
      risk_percent: 10, selected_target_label: 'T2', has_trade_space: true,
      evidence_item_ids: ['pattern-1', 'target-1'],
      invalidation_evidence_item_ids: ['support-1'],
      targets: [{
        label: 'T1', price: 12, basis: 'key-level', risk_reward_ratio: 2,
        stressed_risk_reward_ratio: 1.8, evidence_item_ids: ['target-1'],
      }, {
        label: 'T2', price: 14, basis: 'range-high', risk_reward_ratio: 4,
        stressed_risk_reward_ratio: 3.7, evidence_item_ids: ['target-2'],
      }, {
        label: 'T3', price: 16, basis: 'estimated-volume-at-price', risk_reward_ratio: 6,
        stressed_risk_reward_ratio: 5.4, evidence_item_ids: ['target-3'],
      }],
    },
  }],
}

const chart = {
  timeScale: () => ({ timeToCoordinate: () => 180, width: () => 300 }),
} as unknown as IChartApi
const series = { priceToCoordinate: (price: number) => 200 - price * 10 }
const host = { clientWidth: 300, clientHeight: 220 } as HTMLDivElement

describe('structural trade scenario projection', () => {
  it('selects and parses the backend-owned primary scenario', () => {
    const scenario = readPrimaryStructuralScenario(run)

    expect(scenario).toEqual(expect.objectContaining({
      id: 'scenario-1', direction: 'long', state: 'triggered',
      entryPrice: 10, invalidationPrice: 9, selectedTargetLabel: 'T2',
    }))
    expect(scenario?.targets).toHaveLength(3)
    expect(scenario?.targets[1].evidenceItemIds).toEqual(['target-2'])
    expect(targetBasisLabel(scenario!.targets[2].basis)).toBe('成交密集区')
    expect(isVolumeZoneTarget(scenario!.targets[2])).toBe(true)
  })

  it('projects the selected target through live chart price coordinates', () => {
    const geometry = projectRiskReward(run, chart, series, host, 'T1')

    expect(geometry).toEqual(expect.objectContaining({
      scenarioId: 'scenario-1', entryY: 100, invalidationY: 110,
      selectedTargetLabel: 'T1', state: 'triggered',
    }))
    expect(geometry?.targets).toHaveLength(3)
    expect(geometry?.targets[0]).toEqual(expect.objectContaining({
      targetY: 80, selected: true,
    }))
    expect(geometry?.targets[1]).toEqual(expect.objectContaining({
      targetY: 60, selected: false,
    }))
    expect(geometry!.targets[1].x).toBeGreaterThan(geometry!.targets[0].x)
    expect(geometry!.targets[1].width).toBeLessThan(geometry!.targets[0].width)
    expect(geometry!.targets[2]).toEqual(expect.objectContaining({ targetY: 40, selected: false }))
    expect(geometry?.width).toBeGreaterThanOrEqual(80)
  })
})
