// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TrendReviewPanel } from './TrendReviewPanel'
import { trendTradingSystemDefaults } from './tradingSystems'

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  list: vi.fn(),
  rebuild: vi.fn(),
  update: vi.fn(),
}))

vi.mock('./trendReviewClient', async importOriginal => {
  const actual = await importOriginal<typeof import('./trendReviewClient')>()
  return {
    ...actual,
    createTrendReview: mocks.create,
    listTrendReviews: mocks.list,
    rebuildTrendReviewSnapshot: mocks.rebuild,
    updateTrendReview: mocks.update,
  }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('TrendReviewPanel', () => {
  it('loads a causal cutoff, reviews every candidate, and confirms the draft', async () => {
    const analysis = {
      run_id: 'review-run', as_of_date: '2026-08-18', completion_state: 'complete',
      stale: false, stale_reasons: [], warnings: [], items: [],
    }
    const review = {
      review_id: 'review-1', symbol: '000001.SZ', dataset_version: 'local-review-v1',
      timeframe: 'daily', horizon: 'short', interval_start: '2025-08-18',
      interval_end: '2026-08-18', as_of_date: '2026-08-18', input_digest: 'abc',
      algorithm_version: 'trend-v1', config_version: 'review-v1', settings: {},
      classification: 'positive', review_status: 'proposed', tags: ['trend-line'],
      labels: [
        { item_id: 'line-1', item_type: 'line', decision: 'pending', payload: { kind: 'support' }, rationale: '' },
        { item_id: 'pattern-1', item_type: 'pattern', decision: 'pending', payload: { display_name: '双底' }, rationale: '' },
      ],
      expected: {}, rationale: '', sources: [], revision: 1,
      created_at_ms: 1, updated_at_ms: 1,
    }
    mocks.list.mockResolvedValue([])
    mocks.create.mockResolvedValue({ review, analysis })
    mocks.update.mockImplementation(async (_review, status, labels, rationale) => ({
      ...review, review_status: status, labels, rationale, revision: 2,
    }))
    const onContextChange = vi.fn()
    const user = userEvent.setup()
    render(<TrendReviewPanel
      symbol="000001.SZ"
      name="平安银行"
      settings={trendTradingSystemDefaults}
      onContextChange={onContextChange}
      onClose={vi.fn()}
    />)

    await user.click(screen.getByRole('button', { name: /加载截点/ }))
    expect(onContextChange).toHaveBeenCalledWith({ asOfDate: '2026-08-18', analysis })
    await user.click(screen.getByRole('button', { name: '接受 line-1' }))
    await user.click(screen.getByRole('button', { name: '拒绝 pattern-1' }))
    await user.type(screen.getByRole('textbox', { name: '复核说明' }), '人工复核完成')
    await user.click(screen.getByRole('button', { name: '确认入库' }))

    expect(mocks.update).toHaveBeenCalledWith(
      review,
      'confirmed',
      expect.arrayContaining([
        expect.objectContaining({ item_id: 'line-1', decision: 'accepted' }),
        expect.objectContaining({ item_id: 'pattern-1', decision: 'rejected' }),
      ]),
      '人工复核完成',
    )
  })
})
