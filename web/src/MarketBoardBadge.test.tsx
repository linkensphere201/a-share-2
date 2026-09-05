// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MarketBoardBadge, marketBoardOf } from './MarketBoardBadge'

afterEach(cleanup)

describe('MarketBoardBadge', () => {
  it('classifies BSE, ChiNext, and STAR Market stocks', () => {
    expect(marketBoardOf({ symbol: '920982.BJ', kind: 'stock', exchange: 'BJ' })).toBe('bse')
    expect(marketBoardOf({ symbol: '300364.SZ', kind: 'stock', exchange: 'SZ' })).toBe('chinext')
    expect(marketBoardOf({ symbol: '301018.SZ', kind: 'stock', exchange: 'SZ' })).toBe('chinext')
    expect(marketBoardOf({ symbol: '688519.SH', kind: 'stock', exchange: 'SH' })).toBe('star')
    expect(marketBoardOf({ symbol: '689009.SH', kind: 'stock', exchange: 'SH' })).toBe('star')
  })

  it('does not mark main-board stocks or non-stock instruments', () => {
    expect(marketBoardOf({ symbol: '600000.SH', kind: 'stock', exchange: 'SH' })).toBeUndefined()
    expect(marketBoardOf({ symbol: '159915.SZ', kind: 'etf', exchange: 'SZ' })).toBeUndefined()
  })

  it('renders an accessible compact glyph', () => {
    render(<MarketBoardBadge instrument={{ symbol: '920982.BJ', kind: 'stock', exchange: 'BJ' }}/>)
    expect(screen.getByLabelText('北交所').textContent).toBe('北')
  })
})
