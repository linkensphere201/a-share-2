import { useEffect } from 'react'
import {
  millisecondsUntilMarketSession,
  millisecondsUntilNextMarketDay,
  type DailyBar,
} from './chartData'
import { logInfo, logWarning } from './eventLogger'

type IntradayPollResponse = {
  items: DailyBar[]
  status: { state: string }
}

type IntradayPollingOptions = {
  symbol: string
  disabled?: boolean
  onBar: (bar: DailyBar) => void
}

export function useIntradayDailyPolling({
  symbol,
  disabled = false,
  onBar,
}: IntradayPollingOptions) {
  useEffect(() => {
    if (disabled) return
    let stopped = false
    let timer = 0
    let failureCount = 0
    let controller: AbortController | undefined
    const schedule = (delay: number) => {
      timer = window.setTimeout(refresh, delay)
    }
    const refresh = () => {
      if (stopped) return
      const delayUntilSession = millisecondsUntilMarketSession(new Date())
      if (delayUntilSession > 0) {
        schedule(delayUntilSession)
        return
      }
      controller = new AbortController()
      const params = new URLSearchParams({ symbol })
      fetch(`/api/intraday-bars?${params}`, { signal: controller.signal })
        .then(response => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`)
          return response.json() as Promise<IntradayPollResponse>
        })
        .then(body => {
          if (stopped) return
          const live = body.items[0]
          if (live) onBar(live)
          if (failureCount > 0) {
            logInfo('intraday', '盘中行情读取恢复', { symbol })
            failureCount = 0
          }
          schedule(body.status.state === 'market_closed'
            ? millisecondsUntilNextMarketDay(new Date())
            : 30_000)
        })
        .catch(error => {
          if (stopped || (error as Error).name === 'AbortError') return
          failureCount += 1
          if (failureCount === 1 || failureCount % 10 === 0) {
            logWarning('intraday', '读取盘中临时日K失败，保留现有图表', {
              symbol, error,
            })
          }
          schedule(30_000)
        })
    }
    schedule(0)
    return () => {
      stopped = true
      window.clearTimeout(timer)
      controller?.abort()
    }
  }, [disabled, onBar, symbol])
}
