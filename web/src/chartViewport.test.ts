import { describe, expect, it } from 'vitest'

import {
  constrainLogicalViewport,
  constrainPriceViewport,
  panChartViewport,
  wheelZoomFactor,
  zoomChartViewport,
} from './chartViewport'

const viewport = {
  logical: { from: 100, to: 200 },
  price: { from: 80, to: 120 },
}

describe('unified chart viewport', () => {
  it('pans time and price together without changing either span', () => {
    const moved = panChartViewport(viewport, 100, 50, 1000, 400)
    expect(moved.logical).toEqual({ from: 90, to: 190 })
    expect(moved.price).toEqual({ from: 85, to: 125 })
  })

  it('uses multiplicative price movement in logarithmic mode', () => {
    const moved = panChartViewport({
      logical: { from: 0, to: 100 },
      price: { from: 10, to: 40 },
    }, 0, 200, 1000, 400, true)
    expect(moved.price.from).toBeCloseTo(20)
    expect(moved.price.to).toBeCloseTo(80)
  })

  it('zooms both axes by one factor around the pointer anchor', () => {
    const zoomed = zoomChartViewport(viewport, 2, 0.25, 0.75)
    expect(zoomed.logical).toEqual({ from: 75, to: 275 })
    expect(zoomed.price).toEqual({ from: 70, to: 150 })
    expect(zoomed.logical.from + (zoomed.logical.to - zoomed.logical.from) * 0.25).toBe(125)
    expect(zoomed.price.from + (zoomed.price.to - zoomed.price.from) * 0.25).toBe(90)
  })

  it('preserves a logarithmic price anchor while zooming', () => {
    const zoomed = zoomChartViewport({
      logical: { from: 0, to: 100 },
      price: { from: 10, to: 40 },
    }, 2, 0.5, 0.5, true)
    expect(zoomed.price.from).toBeCloseTo(5)
    expect(zoomed.price.to).toBeCloseTo(80)
  })

  it('allows complete history to shrink to eighteen percent but no farther', () => {
    const constrained = constrainLogicalViewport({ from: -1000, to: 1000 }, 101)
    expect(constrained.to - constrained.from).toBeCloseTo(100 / 0.18)
    expect(constrainLogicalViewport({ from: 10, to: 12 }, 101).to - 11).toBeCloseTo(2.5)
  })

  it('keeps price zoom and blank panning inside recoverable global limits', () => {
    const zoomed = constrainPriceViewport({ from: -1000, to: 1000 }, 20, 40)
    expect(zoomed.to - zoomed.from).toBeCloseTo(20 / 0.18)
    const panned = constrainPriceViewport({ from: 1000, to: 1020 }, 20, 40)
    expect(panned.from).toBeLessThan(40)
    expect(panned.to).toBeGreaterThan(40)
  })

  it('uses the current time viewport for the anti-flattening limit', () => {
    const constrained = constrainPriceViewport(
      { from: -1000, to: 1000 },
      0,
      1000,
      false,
      0.18,
      0.001,
      0.42,
      20,
      40,
    )
    expect(constrained.to - constrained.from).toBeCloseTo(20 / 0.18)
  })

  it('normalizes wheel input into a bounded zoom factor', () => {
    expect(wheelZoomFactor(120)).toBeGreaterThan(1)
    expect(wheelZoomFactor(-120)).toBeLessThan(1)
    expect(wheelZoomFactor(20_000)).toBeCloseTo(wheelZoomFactor(10_000))
  })
})
