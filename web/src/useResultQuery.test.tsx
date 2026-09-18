// @vitest-environment jsdom

import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useResultQuery, useSerialPolling } from './useResultQuery'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

afterEach(() => vi.useRealTimers())

describe('result request ownership', () => {
  it('ignores an old selection even when its loader ignores abort', async () => {
    const first = deferred<string>()
    const second = deferred<string>()
    const publish = vi.fn()
    const reject = vi.fn()
    const signals: AbortSignal[] = []
    const { rerender, unmount } = renderHook(({ key }) => useResultQuery(key, signal => {
      signals.push(signal)
      return key === 'a' ? first.promise : second.promise
    }, publish, reject), { initialProps: { key: 'a' } })
    await act(async () => {})
    rerender({ key: 'b' })
    await act(async () => {})
    expect(signals[0].aborted).toBe(true)
    await act(async () => { first.resolve('obsolete'); second.resolve('current') })
    expect(publish).toHaveBeenCalledTimes(1)
    expect(publish).toHaveBeenCalledWith('current', expect.any(Number))
    expect(reject).not.toHaveBeenCalled()
    unmount()
    expect(signals[1].aborted).toBe(true)
  })

  it('suppresses late failures after unmount', async () => {
    const pending = deferred<string>()
    const reject = vi.fn()
    const { unmount } = renderHook(() => useResultQuery('a', () => pending.promise, vi.fn(), reject))
    await act(async () => {})
    unmount()
    await act(async () => { pending.reject(new Error('obsolete')) })
    expect(reject).not.toHaveBeenCalled()
  })
})

describe('serial polling', () => {
  it('does not overlap slow requests and aborts the active request on cleanup', async () => {
    vi.useFakeTimers()
    const pending = deferred<void>()
    const poll = vi.fn(() => pending.promise)
    const { unmount } = renderHook(() => useSerialPolling('a', 100, poll, vi.fn()))
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(poll).toHaveBeenCalledTimes(1)
    await act(async () => { pending.resolve() })
    await act(async () => { await vi.advanceTimersByTimeAsync(100) })
    expect(poll).toHaveBeenCalledTimes(2)
    const signal = (poll.mock.calls[0] as unknown as [AbortSignal])[0]
    unmount()
    expect(signal.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('retries errors but does not reschedule an obsolete request', async () => {
    vi.useFakeTimers()
    const pending = deferred<void>()
    const reject = vi.fn()
    const poll = vi.fn().mockRejectedValueOnce(new Error('retry')).mockReturnValue(pending.promise)
    const { rerender, unmount } = renderHook(({ key }: { key: string | undefined }) =>
      useSerialPolling(key, 100, poll, reject), { initialProps: { key: 'a' as string | undefined } })
    await act(async () => { await vi.advanceTimersByTimeAsync(200) })
    expect(poll).toHaveBeenCalledTimes(2)
    expect(reject).toHaveBeenCalledTimes(1)
    rerender({ key: undefined })
    await act(async () => { pending.resolve(); await vi.advanceTimersByTimeAsync(1000) })
    expect(poll).toHaveBeenCalledTimes(2)
    unmount()
  })
})
