// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { listScreenerActivity, listScreenerHistory, loadScreenerRun, type ScreenerRun } from './screenerClient'
import { useScreenerHistory } from './useScreenerHistory'

vi.mock('./screenerClient', () => ({ listScreenerActivity: vi.fn(), listScreenerHistory: vi.fn(), loadScreenerRun: vi.fn() }))
const run = (index: number, status: ScreenerRun['status'] = 'succeeded') => ({
  run_id: String(index), started_at_ms: 1000 - index, status,
} as ScreenerRun)

beforeEach(() => { vi.resetAllMocks(); vi.useFakeTimers(); localStorage.clear() })
afterEach(() => { cleanup(); vi.useRealTimers() })

it('polls only known active IDs regardless of loaded pages and preserves older selection', async () => {
  vi.mocked(listScreenerHistory)
    .mockResolvedValueOnce({ items: Array.from({ length: 50 }, (_, i) => run(i, i === 0 ? 'running' : 'succeeded')), has_more: true, next_cursor: '50' })
    .mockResolvedValueOnce({ items: Array.from({ length: 50 }, (_, i) => run(i + 50)), has_more: true, next_cursor: '100' })
    .mockResolvedValueOnce({ items: Array.from({ length: 50 }, (_, i) => run(i + 100)), has_more: false, next_cursor: null })
  vi.mocked(listScreenerActivity).mockResolvedValue([run(0)])
  const { result } = renderHook(() => useScreenerHistory(vi.fn()))
  await act(async () => {})
  await act(async () => result.current.loadMore())
  await act(async () => result.current.loadMore())
  act(() => result.current.setSelectedRun(run(149)))
  await act(async () => vi.advanceTimersByTimeAsync(1000))
  expect(result.current.runs).toHaveLength(150)
  expect(result.current.selectedRun?.run_id).toBe('149')
  expect(listScreenerActivity).toHaveBeenLastCalledWith(['0'], expect.any(AbortSignal))
  expect(listScreenerHistory).toHaveBeenCalledTimes(3)
  await act(async () => vi.advanceTimersByTimeAsync(1000))
  expect(listScreenerActivity).toHaveBeenLastCalledWith([], expect.any(AbortSignal))
  expect(listScreenerHistory).toHaveBeenCalledTimes(3)
})

it('retries a failed page without changing cursor or losing existing results', async () => {
  vi.mocked(listScreenerHistory)
    .mockResolvedValueOnce({ items: [run(0)], has_more: true, next_cursor: 'next' })
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce({ items: [run(1)], has_more: false, next_cursor: null })
  const error = vi.fn()
  const { result } = renderHook(() => useScreenerHistory(error))
  await act(async () => {})
  await act(async () => result.current.loadMore())
  expect(error).toHaveBeenCalledOnce()
  expect(result.current.runs).toHaveLength(1)
  expect(result.current.hasMore).toBe(true)
  await act(async () => result.current.loadMore())
  expect(listScreenerHistory).toHaveBeenLastCalledWith('next', expect.any(AbortSignal))
  expect(result.current.runs).toHaveLength(2)
  expect(result.current.hasMore).toBe(false)
})

it('does not resurrect locally removed runs from delayed pages and aborts unmounted work', async () => {
  let resolve!: (value: Awaited<ReturnType<typeof listScreenerHistory>>) => void
  vi.mocked(listScreenerHistory).mockReturnValue(new Promise(done => { resolve = done }))
  const { result, unmount } = renderHook(() => useScreenerHistory(vi.fn()))
  act(() => { result.current.add([run(0)]); result.current.remove('0') })
  await act(async () => resolve({ items: [run(0), run(1)], has_more: false, next_cursor: null }))
  expect(result.current.runs.map(item => item.run_id)).toEqual(['1'])
  vi.mocked(listScreenerActivity).mockReturnValue(new Promise(() => {}))
  await act(async () => vi.advanceTimersByTimeAsync(1000))
  const signal = vi.mocked(listScreenerActivity).mock.calls[0][1]!
  unmount()
  expect(signal.aborted).toBe(true)
})

it('restores a saved old selection outside the initial history page', async () => {
  localStorage.setItem('stock-harness.screener.selected-run.v1', '999')
  vi.mocked(listScreenerHistory).mockResolvedValue({ items: [run(0)], has_more: true, next_cursor: 'next' })
  vi.mocked(loadScreenerRun).mockResolvedValue(run(999))
  const { result } = renderHook(() => useScreenerHistory(vi.fn()))
  await act(async () => {})
  expect(result.current.selectedRun?.run_id).toBe('999')
  expect(result.current.runs).toHaveLength(2)
})
