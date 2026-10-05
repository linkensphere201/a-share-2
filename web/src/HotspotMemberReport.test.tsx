// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { HotspotMemberReport } from './HotspotMemberReport'
import type { HotspotMemberReport as Report } from './signalReviewClient'

afterEach(cleanup)

const report: Report = {
  version: 'hotspot-member-roles-v1', effective_date: '2026-09-30', status: 'complete',
  member_count: 24, covered_count: 23, candidate_count: 1,
  items: [{ symbol: '600418.SH', name: '江淮汽车', rank: 1, role: 'core-leader',
    role_label: '核心领涨候选', score: 80, return_5: .28, return_20: .4,
    excess_return_5: .2, excess_return_20: .3, strength_sessions: 5,
    launch_lead_sessions: 2, down_market_excess: .02, down_market_sessions: 4,
    drawdown_10: -.01, amount_rank: 1, amount_share: .4, amount_proxy_5: 1e9,
    price_basis: 'forward-adjusted-as-of', recognition_fresh: true,
    recognition: [{ effective_date: '2026-09-25', rank: 1, recognition_role: 'recent-rank-1', run_id: 'weekly' }],
  }],
}

it('shows dated leader names, quantitative evidence and recognition provenance', () => {
  render(<HotspotMemberReport report={report}/>)
  expect(screen.getByText('1. 江淮汽车')).toBeTruthy()
  expect(screen.getByText('600418.SH')).toBeTruthy()
  expect(screen.getByText('核心领涨候选')).toBeTruthy()
  expect(screen.getByText('超额 +20.0pp')).toBeTruthy()
  expect(screen.getByText('启动代理领先 2 日')).toBeTruthy()
  expect(screen.getByText(/2026-09-25/)).toBeTruthy()
  expect(screen.getByText('截至当日复权')).toBeTruthy()
})

it('does not manufacture roles or recognition for unavailable data', () => {
  const { rerender } = render(<HotspotMemberReport report={{ ...report, status: 'insufficient-data', items: [], candidate_count: 0 }}/>)
  expect(screen.getByText('成员行情覆盖不足，暂不判定角色。')).toBeTruthy()
  rerender(<HotspotMemberReport report={{ ...report, items: [{ ...report.items[0], recognition: [], down_market_excess: null, down_market_sessions: 0, launch_lead_sessions: null }] }}/>)
  expect(screen.getByText('无已保存记录')).toBeTruthy()
  expect(screen.getByText('启动先后未确认')).toBeTruthy()
  expect(screen.getByText('无板块下跌样本')).toBeTruthy()
})

it('labels stale recognition and empty candidates explicitly', () => {
  const { rerender } = render(<HotspotMemberReport report={{ ...report, items: [{ ...report.items[0], recognition_fresh: false }] }}/>)
  expect(screen.getByText('记录过期，不加权')).toBeTruthy()
  rerender(<HotspotMemberReport report={{ ...report, items: [], candidate_count: 0 }}/>)
  expect(screen.getByText('当前没有满足角色条件的成员。')).toBeTruthy()
})
