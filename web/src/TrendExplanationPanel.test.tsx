// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TrendExplanationPanel } from './TrendExplanationPanel'
import { GeneratedAnalysisOverlay } from './chartCanvasParts'
import type { TrendAnalysisRun } from './trendAnalysisClient'

afterEach(cleanup)

const run: TrendAnalysisRun = {
  run_id: 'run', as_of_date: '2026-08-21', completion_state: 'complete',
  stale: false, stale_reasons: [], warnings: [], items: [{
    item_id: 'short-support', item_type: 'line', payload: {
      horizon: 'short', kind: 'support', score: 0.9,
      first_pivot_date: '2026-06-01', first_price: 10,
      second_pivot_date: '2026-07-01', second_price: 11,
      projected_price: 12, touch_count: 3, slope_per_bar: 0.1,
    },
  }, {
    item_id: 'short-support-event', item_type: 'evidence', parent_item_id: 'short-support', payload: {
      kind: 'latest-structural-event-summary', event_kind: 'no-structural-change',
      current_state: 'ready', direction: 'down', event_date: '2026-08-21', boundary_price: 12,
      invalidation_level: 12,
    },
  }, {
    item_id: 'primary-pattern', item_type: 'pattern', payload: {
      primary: true, display_name: '对称三角形', timeframe: 'daily', score: 0.82,
      score_components: { contraction: 0.75 }, completion_state: 'forming',
    },
  }],
}

describe('TrendExplanationPanel', () => {
  it('links pointer and keyboard inspection to the matching chart item', () => {
    const onHighlight = vi.fn()
    render(<TrendExplanationPanel run={run} onHighlightItemChange={onHighlight} onClose={vi.fn()}/>)
    expect(screen.getByText('突破与破位')).toBeTruthy()
    expect(screen.getByText('分析证据')).toBeTruthy()
    expect(screen.getByText('对称三角形 · 82分')).toBeTruthy()
    expect(screen.getAllByText('12.00')).toHaveLength(2)
    expect(screen.getByText('短期支撑：尚未向下破位')).toBeTruthy()
    const row = screen.getByText('短期上行支撑线').closest('article')!

    fireEvent.pointerEnter(row)
    expect(onHighlight).toHaveBeenLastCalledWith('short-support')
    fireEvent.pointerLeave(row)
    expect(onHighlight).toHaveBeenLastCalledWith(undefined)
    fireEvent.focus(row)
    expect(onHighlight).toHaveBeenLastCalledWith('short-support')
    fireEvent.blur(row)
    expect(onHighlight).toHaveBeenLastCalledWith(undefined)
  })

  it('marks only the linked generated geometry as highlighted', () => {
    const { container } = render(<GeneratedAnalysisOverlay
      pivots={[]}
      zones={[]}
      patterns={[]}
      lines={[{
        id: 'short-support', kind: 'support', horizon: 'short', score: 0.9,
        touchCount: 3, line: { x1: 0, y1: 10, x2: 100, y2: 20 },
      }, {
        id: 'long-resistance', kind: 'resistance', horizon: 'long', score: 0.8,
        touchCount: 2, line: { x1: 0, y1: 30, x2: 100, y2: 25 },
      }]}
      run={run}
      preview={false}
      highlightedItemId="short-support"
    />)

    expect(container.querySelector('.chart-generated-analysis')?.classList.contains('has-highlight')).toBe(true)
    expect(container.querySelector('.generated-trend-line.highlighted')?.querySelector('title')?.textContent)
      .toContain('短期支撑')
    expect(container.querySelectorAll('.generated-trend-line.highlighted')).toHaveLength(1)
  })

  it('clears chart highlight before closing', () => {
    const onHighlight = vi.fn()
    const onClose = vi.fn()
    render(<TrendExplanationPanel run={run} onHighlightItemChange={onHighlight} onClose={onClose}/>)

    fireEvent.click(screen.getByRole('button', { name: '关闭趋势分析结果说明' }))
    expect(onHighlight).toHaveBeenLastCalledWith(undefined)
    expect(onClose).toHaveBeenCalledOnce()
  })
})
