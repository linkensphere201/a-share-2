// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TradeScenarioPanel } from './TradeScenarioPanel'
import type { TrendAnalysisRun } from './trendAnalysisClient'

const run = {
  run_id: 'run-1', as_of_date: '2026-09-09', completion_state: 'complete',
  stale: false, stale_reasons: [], warnings: [], items: [{
    item_id: 'scenario-1', item_type: 'scenario', payload: {
      kind: 'structural-trade-scenario', primary: true, rank: 1,
      direction: 'long', state: 'triggered', setup_family: 'triangle',
      horizon: 'medium', entry_price: 10, invalidation_price: 9,
      risk_percent: 10, selected_target_label: 'T2', has_trade_space: true,
      evidence_item_ids: ['pattern-1'], invalidation_evidence_item_ids: ['support-1'],
      targets: [
        { label: 'T1', price: 12, basis: 'key-level', risk_reward_ratio: 2,
          stressed_risk_reward_ratio: 1.8, evidence_item_ids: ['target-1'] },
        { label: 'T2', price: 14, basis: 'range-high', risk_reward_ratio: 4,
          stressed_risk_reward_ratio: 3.7, evidence_item_ids: ['target-2'] },
        { label: 'T3', price: 16, basis: 'estimated-volume-at-price', risk_reward_ratio: 6,
          stressed_risk_reward_ratio: 5.4, evidence_item_ids: ['target-3'] },
      ],
    },
  }],
} as TrendAnalysisRun

afterEach(cleanup)

describe('TradeScenarioPanel', () => {
  it('switches targets, visibility, and exact evidence highlights', () => {
    const onTargetChange = vi.fn()
    const onVisibleChange = vi.fn()
    const onHighlightItemChange = vi.fn()
    render(<TradeScenarioPanel
      run={run}
      visible
      onTargetChange={onTargetChange}
      onVisibleChange={onVisibleChange}
      onHighlightItemChange={onHighlightItemChange}
    />)

    fireEvent.click(screen.getByRole('button', { name: /T1/ }))
    expect(onTargetChange).toHaveBeenCalledWith('T1')
    expect(screen.getByRole('button', { name: /T1.*关键位.*盈亏比 1.80/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '成交密集区' }))
    expect(onTargetChange).toHaveBeenLastCalledWith('T3')
    expect(screen.queryByRole('button', { name: /T1.*关键位/ })).toBeNull()
    expect(screen.getByRole('button', { name: /T3.*成交密集区.*盈亏比 5.40/ })).toBeTruthy()
    expect(screen.getByText(/三角形/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /隐藏盈亏比图层/ }))
    expect(onVisibleChange).toHaveBeenCalledWith(false)
    const setup = screen.getByRole('button', { name: /向上场景/ })
    fireEvent.pointerEnter(setup)
    expect(onHighlightItemChange).toHaveBeenCalledWith('pattern-1')
    fireEvent.pointerLeave(setup)
    expect(onHighlightItemChange).toHaveBeenLastCalledWith(undefined)
  })
})
