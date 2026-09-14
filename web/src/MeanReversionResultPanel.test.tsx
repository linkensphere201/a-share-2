// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MeanReversionResultPanel } from './MeanReversionResultPanel'
import type { SignalScoreResult } from './signalReviewClient'

afterEach(cleanup)

it('shows observation semantics and highlights each boundary independently', () => {
  const onHighlight = vi.fn()
  const score = {
    grade: 'B', total_score: 70, eligible: false, summary: '', risk_summary: '',
    setup_family: 'directional-pullback',
    opportunity: { state: 'confirmation-hold', confirmation_price: 10,
      invalidation_price: 9, maximum_holding_sessions: 10, targets: [] },
  } as unknown as SignalScoreResult
  render(<MeanReversionResultPanel score={score} onHighlight={onHighlight} />)
  expect(screen.getByText('观察期限')).toBeTruthy()
  expect(screen.getByText('确认后观察')).toBeTruthy()
  fireEvent.pointerEnter(screen.getByRole('button', { name: /失效/ }))
  expect(onHighlight).toHaveBeenLastCalledWith('mr:invalidation')
  fireEvent.pointerLeave(screen.getByRole('button', { name: /失效/ }))
  expect(onHighlight).toHaveBeenLastCalledWith(undefined)
})
