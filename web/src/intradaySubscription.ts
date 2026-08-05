export type IntradaySubscription = {
  groupId: string
  symbols: string[]
}

export type IntradaySubscriptionStatus = {
  state: string
  symbol_count?: number
}

type SendSubscription = (
  subscription: IntradaySubscription,
  signal: AbortSignal,
) => Promise<IntradaySubscriptionStatus>

type SubscriptionCallbacks = {
  onSuccess?: (status: IntradaySubscriptionStatus, subscription: IntradaySubscription) => void
  onFailure?: (error: unknown, failures: number, subscription: IntradaySubscription) => void
}

export class IntradaySubscriptionCoordinator {
  private pending?: IntradaySubscription
  private active?: AbortController
  private retryTimer: ReturnType<typeof setTimeout> | undefined
  private failures = 0
  private disposed = false

  constructor(
    private readonly send: SendSubscription,
    private readonly callbacks: SubscriptionCallbacks = {},
  ) {}

  update(subscription: IntradaySubscription) {
    if (this.disposed) return
    this.pending = subscription
    clearTimeout(this.retryTimer)
    this.retryTimer = undefined
    this.pump()
  }

  dispose() {
    this.disposed = true
    this.pending = undefined
    clearTimeout(this.retryTimer)
    this.retryTimer = undefined
    this.active?.abort()
    this.active = undefined
  }

  private pump() {
    if (this.disposed || this.active || !this.pending) return
    const subscription = this.pending
    this.pending = undefined
    const controller = new AbortController()
    this.active = controller
    void this.send(subscription, controller.signal)
      .then(status => {
        if (this.disposed) return
        this.failures = 0
        this.callbacks.onSuccess?.(status, subscription)
      })
      .catch(error => {
        if (this.disposed || isAbortError(error)) return
        this.failures += 1
        this.callbacks.onFailure?.(error, this.failures, subscription)
        if (!this.pending) this.pending = subscription
      })
      .finally(() => {
        if (this.active === controller) this.active = undefined
        if (this.disposed) return
        if (this.failures > 0 && this.pending === subscription) {
          const delay = Math.min(30_000, 5_000 * (2 ** Math.min(this.failures - 1, 3)))
          this.retryTimer = setTimeout(() => {
            this.retryTimer = undefined
            this.pump()
          }, delay)
          return
        }
        this.pump()
      })
  }
}

export async function sendIntradaySubscription(
  subscription: IntradaySubscription,
  signal: AbortSignal,
): Promise<IntradaySubscriptionStatus> {
  const response = await fetch('/api/intraday/subscription', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ group_id: subscription.groupId, symbols: subscription.symbols }),
    signal,
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<IntradaySubscriptionStatus>
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}
