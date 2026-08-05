import { afterEach, describe, expect, it, vi } from 'vitest'
import { IntradaySubscriptionCoordinator, type IntradaySubscriptionStatus } from './intradaySubscription'

type Deferred = {
  promise: Promise<IntradaySubscriptionStatus>
  resolve: (value: IntradaySubscriptionStatus) => void
  reject: (reason: unknown) => void
}

afterEach(() => vi.useRealTimers())

describe('IntradaySubscriptionCoordinator', () => {
  it('serializes requests and guarantees the latest subscription is sent last', async () => {
    const requests: string[] = []
    const deferred: Deferred[] = []
    const coordinator = new IntradaySubscriptionCoordinator(subscription => {
      requests.push(subscription.groupId)
      const next = createDeferred()
      deferred.push(next)
      return next.promise
    })

    coordinator.update({ groupId: 'old', symbols: ['000001.SZ'] })
    coordinator.update({ groupId: 'new', symbols: ['600519.SH'] })
    expect(requests).toEqual(['old'])

    deferred[0].resolve({ state: 'ready' })
    await flushPromises()
    expect(requests).toEqual(['old', 'new'])

    deferred[1].resolve({ state: 'ready' })
    await flushPromises()
    coordinator.dispose()
  })

  it('retries the newest failed subscription with bounded backoff', async () => {
    vi.useFakeTimers()
    const send = vi.fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ state: 'ready' })
    const coordinator = new IntradaySubscriptionCoordinator(send)

    coordinator.update({ groupId: 'current', symbols: ['000001.SZ'] })
    await flushPromises()
    expect(send).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(5_000)
    expect(send).toHaveBeenCalledTimes(2)
    coordinator.dispose()
  })

  it('aborts the active request and suppresses work after disposal', () => {
    let signal: AbortSignal | undefined
    const coordinator = new IntradaySubscriptionCoordinator((_subscription, requestSignal) => {
      signal = requestSignal
      return new Promise(() => undefined)
    })

    coordinator.update({ groupId: 'current', symbols: [] })
    coordinator.dispose()

    expect(signal?.aborted).toBe(true)
  })
})

function createDeferred(): Deferred {
  let resolve!: Deferred['resolve']
  let reject!: Deferred['reject']
  const promise = new Promise<IntradaySubscriptionStatus>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

async function flushPromises() {
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
}
