import { useEffect, useState } from 'react'

type Summary = {
  symbol: string
  status: string
  algorithm_version?: string
  smoothing_period?: number
  last_trade_date?: string | null
  latest_absolute_close?: number | null
  latest_coverage_ratio?: number | null
  latest_change_percent?: number | null
}

export function ActiveMarketValueReadout({ symbol }: { symbol: string }) {
  const [summary, setSummary] = useState<Summary>()
  useEffect(() => {
    if (symbol !== 'SHAMV.A') {
      setSummary(undefined)
      return
    }
    const controller = new AbortController()
    fetch('/api/active-market-value', { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<Summary>
      })
      .then(setSummary)
      .catch(error => {
        if ((error as Error).name !== 'AbortError') setSummary(undefined)
      })
    return () => controller.abort()
  }, [symbol])
  if (!summary || summary.status !== 'ready') return null
  return <span className="amv-title-readout" title={`算法 ${summary.algorithm_version ?? '-'} · 独立估算，非净流入 · 合成 OHLC 的成分高低并非同时发生`}>
    <i>截至 {summary.last_trade_date?.slice(5) ?? '-'}</i>
    <i>活跃 {formatValue(summary.latest_absolute_close)}</i>
    <i className={(summary.latest_change_percent ?? 0) >= 0 ? 'rise' : 'fall'}>{formatChange(summary.latest_change_percent)}</i>
    <i>覆盖 {formatCoverage(summary.latest_coverage_ratio)}</i>
    <i>CYF{summary.smoothing_period ?? 13}</i>
  </span>
}

function formatValue(value?: number | null): string {
  if (value === undefined || value === null) return '-'
  if (value >= 1_000_000_000_000) return `${(value / 1_000_000_000_000).toFixed(2)}万亿`
  return `${(value / 100_000_000).toFixed(0)}亿`
}

function formatCoverage(value?: number | null): string {
  if (value === undefined || value === null) return '-'
  return `${(value * 100).toFixed(1)}%`
}

function formatChange(value?: number | null): string {
  if (value === undefined || value === null) return '-'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}
