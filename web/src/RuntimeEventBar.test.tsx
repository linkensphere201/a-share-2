// @vitest-environment jsdom

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RuntimeEventBar } from './RuntimeEventBar'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('RuntimeEventBar polling', () => {
  it('keeps at most one request in flight when a response is slow', async () => {
    vi.useFakeTimers()
    let resolveRequest!: (value: Response) => void
    const pending = new Promise<Response>(resolve => { resolveRequest = resolve })
    const fetchMock = vi.fn(() => pending)
    vi.stubGlobal('fetch', fetchMock)

    render(<RuntimeEventBar/>)
    await vi.advanceTimersByTimeAsync(20_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    resolveRequest({ ok: true, json: async () => ({ items: [] }) } as Response)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('aborts the active request when unmounted', async () => {
    vi.useFakeTimers()
    let requestSignal: AbortSignal | undefined
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      requestSignal = init?.signal as AbortSignal
      return new Promise<Response>(() => undefined)
    }))

    const view = render(<RuntimeEventBar/>)
    await vi.advanceTimersByTimeAsync(0)
    view.unmount()

    expect(requestSignal?.aborted).toBe(true)
  })
})

async function flushPromises() {
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
}
