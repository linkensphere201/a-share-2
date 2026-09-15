// @vitest-environment jsdom

import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { useAnalysisOverlayVisibility } from './AnalysisOverlayToggle'

afterEach(cleanup)

it('requires a fresh opt-in after changing context, including returning to the old result', () => {
  const { result, rerender } = renderHook(({ context }) => useAnalysisOverlayVisibility(context), {
    initialProps: { context: 'stock-a/run-1' },
  })
  expect(result.current[0]).toBe(false)
  act(() => result.current[1](true))
  expect(result.current[0]).toBe(true)
  rerender({ context: 'stock-a/run-1' })
  expect(result.current[0]).toBe(true)
  rerender({ context: 'stock-a/run-2' })
  expect(result.current[0]).toBe(false)
  rerender({ context: 'stock-a/run-1' })
  expect(result.current[0]).toBe(false)
})

it('keeps different overlays and chart instances independent', () => {
  const first = renderHook(() => useAnalysisOverlayVisibility('same-run'))
  const second = renderHook(() => useAnalysisOverlayVisibility('same-run'))
  act(() => first.result.current[1](true))
  expect(second.result.current[0]).toBe(false)
  first.unmount()
  const reopened = renderHook(() => useAnalysisOverlayVisibility('same-run'))
  expect(reopened.result.current[0]).toBe(false)
})
