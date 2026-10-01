// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { listSignalRuns, loadSignalRun, type SignalRun } from './signalReviewClient'
import { useSignalRunHistory } from './useSignalRunHistory'

vi.mock('./signalReviewClient', () => ({ listSignalRuns: vi.fn(), loadSignalRun: vi.fn() }))
beforeEach(() => { vi.resetAllMocks(); vi.useFakeTimers() })
afterEach(() => { cleanup(); vi.useRealTimers() })
const run = (id: string, signal = 'daily', status: SignalRun['status'] = 'succeeded') => ({
  run_id: id, signal_id: signal, status, effective_date: '2026-09-17', revision: 1, started_at_ms: 1,
} as SignalRun)

it('isolates old definition responses and preserves locally started runs during initial load', async () => {
  let old!: (runs: SignalRun[]) => void
  vi.mocked(listSignalRuns).mockImplementation(signal => signal === 'daily'
    ? new Promise(resolve => { old = resolve }) : Promise.resolve([run('weekly', 'weekly')]))
  const { result, rerender } = renderHook(({ signal }) => useSignalRunHistory(signal, vi.fn()), { initialProps: { signal: 'daily' } })
  await act(async () => {})
  act(() => result.current.addRun(run('started', 'daily', 'running')))
  await act(async () => old([run('older')]))
  expect(result.current.selectedRun?.run_id).toBe('started')
  rerender({ signal: 'weekly' })
  expect(result.current.runs).toEqual([])
  await act(async () => {})
  expect(result.current.selectedRun?.run_id).toBe('weekly')
  await act(async () => old([run('late')]))
  expect(result.current.runs.map(item => item.run_id)).toEqual(['weekly'])
})

it('polls running work without replacing a selected historical result', async () => {
  const active = run('active', 'daily', 'running'), older = run('older')
  vi.mocked(listSignalRuns).mockResolvedValue([active, older])
  vi.mocked(loadSignalRun).mockResolvedValue(run('active'))
  const { result } = renderHook(() => useSignalRunHistory('daily', vi.fn()))
  await act(async () => {})
  act(() => result.current.setSelectedRun(older))
  await act(async () => vi.advanceTimersByTimeAsync(1200))
  expect(result.current.selectedRun?.run_id).toBe('older')
  expect(result.current.activeRun).toBeUndefined()
})
